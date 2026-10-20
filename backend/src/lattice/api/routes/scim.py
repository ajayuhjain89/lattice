"""SCIM 2.0 provisioning endpoints (Phase 10; D-20). Base URL: ``<public origin>/api/scim/v2``.

Authentication is a SCIM bearer token only (``Authorization: Bearer lsc_...``): the
session cookie is never read here, which is also why these routes are outside the CSRF
header rule (a browser cannot attach the header cross-site without a CORS preflight,
which Lattice never grants). Errors use the SCIM error schema with fixed details.

Limits: a client that keeps failing authentication is refused (429) before its token
is even checked; an authenticated token has its own per-minute limit
(``LATTICE_RATE_LIMIT_SCIM_PER_MINUTE``). Not in the OpenAPI document: SCIM clients
discover the service through ``ServiceProviderConfig``, ``ResourceTypes`` and
``Schemas``.
"""

import json
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from lattice import metrics
from lattice.api.deps import client_address
from lattice.config import Settings
from lattice.db.engine import Database
from lattice.provisioning import scim
from lattice.provisioning.scim import MAX_RESULTS, MEDIA_TYPE, ScimError
from lattice.provisioning.service import Provisioning, ScimCaller, resolve_token
from lattice.ratelimit import ApiLimits, RateLimited

PREFIX = "/scim/v2"
router = APIRouter(prefix=PREFIX, include_in_schema=False)


def _base_url(request: Request) -> str:
    settings: Settings = request.app.state.settings
    return f"{settings.web_origin}/api{PREFIX}"


def _json(content: Any, status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(content, status_code=status, media_type=MEDIA_TYPE, headers=headers)


def _limited(exc: RateLimited) -> ScimError:
    return ScimError(
        429, "Too many requests. Try again later.", headers={"Retry-After": str(exc.retry_after)}
    )


def _count(operation: str, status: int) -> None:
    metrics.SCIM_OPERATIONS.labels(
        operation=metrics.bounded(operation, metrics.SCIM_OPERATIONS_SET),
        status=f"{status // 100}xx",
    ).inc()


async def _caller(request: Request, operation: str) -> ScimCaller:
    limits: ApiLimits = request.app.state.limits
    db: Database = request.app.state.db
    address = client_address(request)
    try:
        await limits.sign_in.check(address)
    except RateLimited as exc:
        _count("unauthenticated", 429)
        raise _limited(exc) from None
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    caller = await resolve_token(db, token.strip()) if scheme.lower() == "bearer" else None
    if caller is None:
        _count("unauthenticated", 401)
        try:
            await limits.sign_in.hit(address)
        except RateLimited as exc:
            raise _limited(exc) from None
        raise ScimError(
            401,
            "A valid SCIM bearer token is required.",
            headers={"WWW-Authenticate": 'Bearer realm="lattice-scim"'},
        )
    try:
        await limits.limiter.hit(caller.organization_id, caller.token_id, limits.scim)
    except RateLimited as exc:
        _count(operation, 429)
        raise _limited(exc) from None
    request.state.log_fields = {
        "organization_id": str(caller.organization_id),
        "scim_token_id": str(caller.token_id),
    }
    await _provisioning(request).token_used(caller)
    return caller


async def _refused(request: Request, caller: ScimCaller, operation: str, error: ScimError) -> None:
    """Record an actionable refusal (``scim.request_refused``), then answer it."""
    await _provisioning(request).record_refusal(caller, operation, error)


async def _body(request: Request) -> Any:
    try:
        return json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        raise scim.invalid("The request body is not valid JSON.", "invalidSyntax") from None


def _provisioning(request: Request) -> Provisioning:
    found: Provisioning = request.app.state.provisioning
    return found


def _user_response(request: Request, user: scim.StoredUser, status: int = 200) -> JSONResponse:
    body = scim.render_user(user, _base_url(request))
    headers = {"ETag": scim.version_tag(user.version)}
    if status == 201:
        headers["Location"] = body["meta"]["location"]
    return _json(body, status, headers)


def _positive(value: str | None, name: str, default: int) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError:
        raise scim.invalid(f"{name} must be an integer.") from None
    return max(number, 0)


@router.get("/ServiceProviderConfig")
async def service_provider_config(request: Request) -> JSONResponse:
    await _caller(request, "metadata")
    _count("metadata", 200)
    return _json(scim.service_provider_config(_base_url(request)))


@router.get("/ResourceTypes")
async def resource_types(request: Request) -> JSONResponse:
    await _caller(request, "metadata")
    _count("metadata", 200)
    return _json(scim.resource_types(_base_url(request)))


@router.get("/Schemas")
async def schemas(request: Request) -> JSONResponse:
    await _caller(request, "metadata")
    _count("metadata", 200)
    return _json(scim.schemas(_base_url(request)))


@router.get("/Users")
async def list_users(request: Request) -> JSONResponse:
    caller = await _caller(request, "list")
    query = request.query_params
    found = scim.parse_filter(query.get("filter"))
    start = max(_positive(query.get("startIndex"), "startIndex", 1), 1)
    count = min(_positive(query.get("count"), "count", MAX_RESULTS), MAX_RESULTS)
    total, users = await _provisioning(request).list_users(caller, found, start, count)
    base = _base_url(request)
    _count("list", 200)
    return _json(
        {
            "schemas": [scim.LIST_SCHEMA],
            "totalResults": total,
            "startIndex": start,
            "itemsPerPage": len(users),
            "Resources": [scim.render_user(u, base) for u in users],
        }
    )


@router.get("/Users/{resource_id}")
async def get_user(resource_id: str, request: Request) -> JSONResponse:
    caller = await _caller(request, "read")
    user = await _provisioning(request).get_user(caller, resource_id)
    _count("read", 200)
    return _user_response(request, user)


@router.post("/Users")
async def create_user(request: Request) -> JSONResponse:
    caller = await _caller(request, "create")
    try:
        user = await _provisioning(request).create_user(caller, await _body(request))
    except ScimError as error:
        await _refused(request, caller, "create", error)
        raise
    _count("create", 201)
    return _user_response(request, user, 201)


@router.put("/Users/{resource_id}")
async def replace_user(resource_id: str, request: Request) -> JSONResponse:
    caller = await _caller(request, "replace")
    try:
        user = await _provisioning(request).replace_user(
            caller, resource_id, await _body(request), request.headers.get("if-match")
        )
    except ScimError as error:
        await _refused(request, caller, "replace", error)
        raise
    _count("replace", 200)
    return _user_response(request, user)


@router.patch("/Users/{resource_id}")
async def patch_user(resource_id: str, request: Request) -> JSONResponse:
    caller = await _caller(request, "patch")
    try:
        user = await _provisioning(request).patch_user(
            caller, resource_id, await _body(request), request.headers.get("if-match")
        )
    except ScimError as error:
        await _refused(request, caller, "patch", error)
        raise
    _count("patch", 200)
    return _user_response(request, user)


@router.delete("/Users/{resource_id}")
async def delete_user(resource_id: str, request: Request) -> Response:
    caller = await _caller(request, "delete")
    await _provisioning(request).delete_user(caller, resource_id, request.headers.get("if-match"))
    _count("delete", 204)
    return Response(status_code=204)


def register(app: FastAPI) -> None:
    async def scim_error(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, ScimError)  # noqa: S101 - registered for this type only
        return _json(exc.body(), exc.status, exc.headers or None)

    app.add_exception_handler(ScimError, scim_error)
    app.include_router(router)
