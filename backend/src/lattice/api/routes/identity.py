"""Identity administration (Phase 12, P12.4; phase-12-plan C5). Thin: parse, check the
capability, call one service, map the result.

Under ``/v1/admin/identity``, administrators only; the capability is checked before
anything is read, so a member gets the same 403 whether or not the target exists, and a
connection or token of another organization answers exactly as a random ID.

* ``GET`` -- the organization's OIDC connection, SCIM tokens and their setup status.
* ``oidc`` -- add (when none is active), change the client ID, set / replace / remove the
  client secret, disable and enable (previewed), change the issuer or identifying claim
  (previewed, with the administrator's matched test sign-in as proof), check, and test
  sign-in. Each change: a reason, the version (409), an audit event.
* ``scim/tokens`` -- create, rotate (each shows the token once, ``Cache-Control:
  no-store``), revoke (previewed).

Secrets arrive only in request bodies (``SecretStr``) and never appear in a response,
except a newly created SCIM token in the one response that created it.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from lattice.api.deps import AppSettings, CurrentPrincipal, Limits
from lattice.api.routes.auth import set_binding_cookie
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.identity import settings as service
from lattice.identity.oidc import OidcSignIn
from lattice.identity.sessions import Principal
from lattice.identity.settings_models import (
    CheckOut,
    ClientIdRequest,
    ClientSecretRequest,
    IdentityChangePreview,
    IdentityChangePreviewRequest,
    IdentityChangeRequest,
    IdentitySettings,
    NewConnectionRequest,
    RemoveSecretRequest,
    RevealedScimToken,
    RevokePreview,
    ScimTokenOut,
    StatusPreview,
    StatusPreviewRequest,
    StatusRequest,
    TestSignInOut,
    TestSignInRequest,
    TestSignInStarted,
    TokenRequest,
)
from lattice.vault.keys import Keyring

router = APIRouter(tags=["identity"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {**_ERRORS, 429: {"model": ErrorResponse}}
_CHANGE_ERRORS: dict[int | str, dict[str, Any]] = {**_WRITE_ERRORS, 409: {"model": ErrorResponse}}
_CSRF = "Requires the X-Lattice-Request: 1 header."


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _oidc(request: Request) -> OidcSignIn:
    found: OidcSignIn = request.app.state.oidc
    return found


def _keyring(request: Request) -> Keyring:
    found: Keyring = request.app.state.keyring
    return found


def _require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise LatticeError(ErrorCode.PERMISSION_DENIED)


async def _admin_write(principal: Principal, limits: Limits) -> None:
    """Capability first (403 identical for any target), then the write limit."""
    _require_admin(principal)
    await limits.limiter.hit(
        principal.organization_id, principal.membership_id, limits.access_write
    )


async def _settings(
    request: Request, principal: Principal, settings: AppSettings
) -> IdentitySettings:
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await service.identity_settings(
            session, principal.organization_id, web_origin=settings.web_origin
        )


async def _own_session_on(request: Request, principal: Principal, connection: uuid.UUID) -> bool:
    """Whether the caller's own session came through ``connection`` (it would end)."""
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        found: bool = (
            await session.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM sessions WHERE organization_id = :o "
                    "AND id = :s AND oidc_connection_id = :c)"
                ),
                {"o": principal.organization_id, "s": principal.session_id, "c": connection},
            )
        ).scalar_one()
    return found


@router.get(
    "/v1/admin/identity",
    response_model=IdentitySettings,
    responses=_ERRORS,
    summary="The organization's identity provider connection and SCIM provisioning "
    "(administrators)",
    description="Never a client secret, its reference or a SCIM token: whether a secret is "
    "set and how it is kept, token hints and times.",
)
async def get_identity(
    request: Request, principal: CurrentPrincipal, settings: AppSettings
) -> IdentitySettings:
    _require_admin(principal)
    return await _settings(request, principal, settings)


# --- the OIDC connection ----------------------------------------------------------------------


@router.post(
    "/v1/admin/identity/oidc",
    response_model=IdentitySettings,
    status_code=201,
    responses=_CHANGE_ERRORS,
    summary="Add the organization's OIDC connection, with a reason (administrators; audited)",
    description="Only when no connection is active, and no disabled connection still signs "
    "members in (re-enable that one: a new connection would not sign them in). The issuer "
    "must pass the outbound policy: a public https address, or a private one your Lattice "
    f"operator registered for this organization (422 `outbound_blocked`). {_CSRF}",
)
async def post_connection(
    body: NewConnectionRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> IdentitySettings:
    await _admin_write(principal, limits)
    await service.add_connection(
        _db(request),
        _oidc(request),
        _keyring(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        request=body,
    )
    return await _settings(request, principal, settings)


@router.patch(
    "/v1/admin/identity/oidc/{connection_id}",
    response_model=IdentitySettings,
    responses=_CHANGE_ERRORS,
    summary="Change the connection's client ID, with a reason (administrators; audited)",
    description=f"Sessions are kept. `version` must be current (409 otherwise). {_CSRF}",
)
async def patch_connection(
    connection_id: uuid.UUID,
    body: ClientIdRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> IdentitySettings:
    await _admin_write(principal, limits)
    await service.update_client_id(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        connection_id=connection_id,
        request=body,
    )
    return await _settings(request, principal, settings)


@router.put(
    "/v1/admin/identity/oidc/{connection_id}/client-secret",
    response_model=IdentitySettings,
    responses=_CHANGE_ERRORS,
    summary="Set or replace the client secret, with a reason (administrators; audited)",
    description="Encrypted at once (AES-256-GCM) for this connection; the previous "
    "encrypted secret is destroyed. Never returned, logged or audited. Sessions are kept. "
    f"{_CSRF}",
)
async def put_client_secret(
    connection_id: uuid.UUID,
    body: ClientSecretRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> IdentitySettings:
    await _admin_write(principal, limits)
    await service.set_client_secret(
        _db(request),
        _keyring(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        connection_id=connection_id,
        request=body,
    )
    return await _settings(request, principal, settings)


@router.post(
    "/v1/admin/identity/oidc/{connection_id}/client-secret/remove",
    response_model=IdentitySettings,
    responses=_CHANGE_ERRORS,
    summary="Remove the client secret: a public client (PKCE only) from now on "
    "(administrators; audited)",
    description=f"The encrypted secret is destroyed. {_CSRF}",
)
async def post_remove_client_secret(
    connection_id: uuid.UUID,
    body: RemoveSecretRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> IdentitySettings:
    await _admin_write(principal, limits)
    await service.remove_client_secret(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        connection_id=connection_id,
        request=body,
    )
    return await _settings(request, principal, settings)


@router.post(
    "/v1/admin/identity/oidc/{connection_id}/status/preview",
    response_model=StatusPreview,
    responses=_ERRORS,
    summary="Preview disabling or enabling the connection (administrators; changes nothing)",
    description=f"Disabling stops sign-in at once and ends the connection's live sessions. {_CSRF}",
)
async def post_status_preview(
    connection_id: uuid.UUID,
    body: StatusPreviewRequest,
    request: Request,
    principal: CurrentPrincipal,
) -> StatusPreview:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        preview = await service.preview_status(
            session, principal.organization_id, connection_id, enabled=body.enabled
        )
    own = not body.enabled and await _own_session_on(request, principal, connection_id)
    return preview.model_copy(update={"ends_own_session": own})


@router.post(
    "/v1/admin/identity/oidc/{connection_id}/status",
    response_model=IdentitySettings,
    responses=_CHANGE_ERRORS,
    summary="Disable or enable the connection, with a reason (administrators; audited)",
    description="Disable: sign-in through it stops and its sessions end at once (yours too, "
    "when you signed in through it). Enable: the same connection and members again; 409 "
    f"`connection_exists` while another is active. {_CSRF}",
)
async def post_status(
    connection_id: uuid.UUID,
    body: StatusRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> IdentitySettings:
    await _admin_write(principal, limits)
    await service.set_status(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        connection_id=connection_id,
        request=body,
    )
    return await _settings(request, principal, settings)


@router.post(
    "/v1/admin/identity/oidc/{connection_id}/identity/preview",
    response_model=IdentityChangePreview,
    responses=_ERRORS,
    summary="Preview an issuer or identifying-claim change (administrators; changes nothing)",
    description="Every session of the connection ends. The change needs your own test "
    "sign-in under exactly these values, matching your directory identity (`proof`): only "
    "the same identity provider at a new address, or a claim carrying the same values, is "
    f"supported -- never a provider whose subjects differ. {_CSRF}",
)
async def post_identity_preview(
    connection_id: uuid.UUID,
    body: IdentityChangePreviewRequest,
    request: Request,
    principal: CurrentPrincipal,
) -> IdentityChangePreview:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        preview = await service.preview_identity_change(
            session, principal.organization_id, principal.membership_id, connection_id, body
        )
    own = await _own_session_on(request, principal, connection_id)
    return preview.model_copy(update={"ends_own_session": own})


@router.post(
    "/v1/admin/identity/oidc/{connection_id}/identity",
    response_model=IdentitySettings,
    responses=_CHANGE_ERRORS,
    summary="Change the issuer or identifying claim, with proof, confirmation and a reason "
    "(administrators; audited)",
    description="`proof_id`: your matched test sign-in under exactly these values (422 "
    "`identity_unverified` otherwise: a changed-subject migration is not supported). "
    "`confirmation`: the new issuer, or the new claim name when only the claim changes. "
    f"Every session of the connection ends. {_CSRF}",
)
async def post_identity(
    connection_id: uuid.UUID,
    body: IdentityChangeRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
) -> IdentitySettings:
    await _admin_write(principal, limits)
    await service.change_identity(
        _db(request),
        _oidc(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        connection_id=connection_id,
        request=body,
    )
    return await _settings(request, principal, settings)


@router.post(
    "/v1/admin/identity/oidc/{connection_id}/check",
    response_model=CheckOut,
    responses=_WRITE_ERRORS,
    summary="Check the connection without signing in (administrators)",
    description="Discovery, keys and the client credentials, through the outbound policy: "
    "`valid`, `invalid_configuration`, `credentials_rejected`, `provider_unavailable` or "
    "`outbound_blocked`, and the step that decided. The result is recorded on the "
    f"connection. {_CSRF}",
)
async def post_check(
    connection_id: uuid.UUID, request: Request, principal: CurrentPrincipal, limits: Limits
) -> CheckOut:
    await _admin_write(principal, limits)
    return await service.run_check(
        _db(request),
        _oidc(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        connection_id=connection_id,
    )


@router.post(
    "/v1/admin/identity/oidc/test-sign-in",
    response_model=TestSignInStarted,
    status_code=status.HTTP_201_CREATED,
    responses=_WRITE_ERRORS,
    summary="Start a test sign-in through the active connection (administrators)",
    description="Open `authorization_url` in a new tab and sign in as yourself. No session "
    "is created; you return to the identity settings, which read the result by `test_id`. "
    "Optional `issuer` / `subject_claim`: test proposed values (the proof an issuer or "
    f"claim change needs). {_CSRF}",
)
async def post_test_sign_in(
    body: TestSignInRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
    settings: AppSettings,
    response: Response,
) -> TestSignInStarted:
    await _admin_write(principal, limits)
    test_id, url, binding = await service.start_test_sign_in(
        _oidc(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        request=body,
    )
    set_binding_cookie(response, settings, binding)
    return TestSignInStarted(test_id=test_id, authorization_url=url)


@router.get(
    "/v1/admin/identity/oidc/test-sign-in/{test_id}",
    response_model=TestSignInOut,
    responses=_ERRORS,
    summary="Your test sign-in's result (administrators; only the one who started it)",
)
async def get_test_sign_in(
    test_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> TestSignInOut:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        found = await service.test_sign_in_result(
            session, principal.organization_id, principal.membership_id, test_id
        )
    return found


# --- SCIM tokens --------------------------------------------------------------------------------


@router.post(
    "/v1/admin/identity/scim/tokens",
    response_model=RevealedScimToken,
    status_code=201,
    responses=_CHANGE_ERRORS,
    summary="Create a SCIM token, shown once, with a reason (administrators; audited)",
    description="The token is in this response only (`Cache-Control: no-store`); Lattice "
    "keeps its SHA-256 hash and last four characters. It does not expire: revoke it when "
    f"the provider no longer uses it. {_CSRF}",
)
async def post_token(
    body: TokenRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> RevealedScimToken:
    await _admin_write(principal, limits)
    created = await service.create_token(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        reason=body.reason,
    )
    return created


@router.post(
    "/v1/admin/identity/scim/tokens/{token_id}/rotate",
    response_model=RevealedScimToken,
    responses=_CHANGE_ERRORS,
    summary="Rotate a SCIM token: a replacement shown once; the old token is refused from "
    "now on (administrators; audited)",
    description="No overlap: the provider is refused until the new token is pasted in. "
    "For an overlap, create a second token, switch the provider, then revoke the old one. "
    f"{_CSRF}",
)
async def post_rotate(
    token_id: uuid.UUID,
    body: TokenRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> RevealedScimToken:
    await _admin_write(principal, limits)
    rotated = await service.rotate_token(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        token_id=token_id,
        reason=body.reason,
    )
    return rotated


@router.post(
    "/v1/admin/identity/scim/tokens/{token_id}/revoke/preview",
    response_model=RevokePreview,
    responses=_ERRORS,
    summary="Preview revoking a SCIM token (administrators; changes nothing)",
    description=f"The provider's next request with it is refused. {_CSRF}",
)
async def post_revoke_preview(
    token_id: uuid.UUID, request: Request, principal: CurrentPrincipal
) -> RevokePreview:
    _require_admin(principal)
    async with _db(request).tenant_snapshot(principal.organization_id) as session:
        return await service.preview_revoke(session, principal.organization_id, token_id)


@router.post(
    "/v1/admin/identity/scim/tokens/{token_id}/revoke",
    response_model=ScimTokenOut,
    responses=_CHANGE_ERRORS,
    summary="Revoke a SCIM token, with a reason (administrators; audited)",
    description=f"Final: a revoked token is never valid again. {_CSRF}",
)
async def post_revoke(
    token_id: uuid.UUID,
    body: TokenRequest,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> ScimTokenOut:
    await _admin_write(principal, limits)
    return await service.revoke_token(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        token_id=token_id,
        reason=body.reason,
    )
