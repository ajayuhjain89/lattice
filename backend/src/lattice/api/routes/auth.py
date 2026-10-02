from typing import Any

from fastapi import APIRouter, Request, Response, status

from lattice.api.deps import AppSettings, CurrentPrincipal, Limits, Sessions, client_address
from lattice.api.schemas import DevLoginRequest, MeResponse
from lattice.config import Settings
from lattice.errors import ErrorResponse
from lattice.identity.dev_provider import DevIdentityProvider

router = APIRouter(tags=["auth"])
# Mounted only when Settings.dev_auth_active (never in production).
dev_router = APIRouter(tags=["auth"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
}


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_absolute_timeout_minutes * 60,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@dev_router.post(
    "/v1/auth/dev-login",
    response_model=MeResponse,
    responses=_ERRORS,
    summary="Development-only persona sign-in",
    description="Available only when LATTICE_ENV is development or test and "
    "LATTICE_DEV_AUTH_ENABLED=true. Requires the X-Lattice-Request: 1 header.",
)
async def dev_login(
    body: DevLoginRequest,
    request: Request,
    response: Response,
    settings: AppSettings,
    sessions: Sessions,
    limits: Limits,
) -> MeResponse:
    # Per client, before the identity is looked up: the same for any subject.
    await limits.sign_in.hit(client_address(request))
    provider: DevIdentityProvider = request.app.state.dev_identity_provider
    identity = await provider.resolve(body.subject, body.organization)
    # Session rotation: never keep using a session that existed before sign-in.
    previous = request.cookies.get(settings.session_cookie_name)
    if previous:
        await sessions.revoke_token_if_valid(previous)
    token, principal = await sessions.create(
        user_id=identity.user_id, organization_id=identity.organization_id
    )
    _set_session_cookie(response, settings, token)
    return MeResponse.from_principal(principal)


@router.post(
    "/v1/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="Revoke the current session",
)
async def logout(
    principal: CurrentPrincipal, settings: AppSettings, sessions: Sessions
) -> Response:
    await sessions.revoke(principal)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response, settings)
    return response
