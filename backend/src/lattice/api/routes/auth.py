import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from lattice import metrics
from lattice.api.deps import AppSettings, CurrentPrincipal, Limits, Sessions, client_address
from lattice.api.schemas import DevLoginRequest, MeResponse
from lattice.config import Settings
from lattice.errors import ErrorResponse, LatticeError
from lattice.identity.dev_provider import DevIdentityProvider
from lattice.identity.oidc import OidcFailure, OidcSignIn
from lattice.log import log_event
from lattice.ratelimit import RateLimited

logger = logging.getLogger(__name__)

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


# --- OIDC sign-in (Phase 10; ADR-015) -------------------------------------------------------

_OIDC_REDIRECT: dict[int | str, dict[str, Any]] = {
    303: {
        "description": "A redirect: to the identity provider, back into Lattice, or to "
        "/sign-in?error=sso_failed|sso_unavailable|rate_limited"
    }
}


def _binding_cookie(settings: Settings) -> str:
    return "__Host-lattice_oidc" if settings.session_cookie_secure else "lattice_oidc"


def _oidc(request: Request) -> OidcSignIn:
    found: OidcSignIn = request.app.state.oidc
    return found


def _oidc_failed(settings: Settings, public_error: str, outcome: str) -> Response:
    """One redirect per public error; the fixed outcome goes to metrics and the log."""
    metrics.SSO_SIGN_INS.labels(outcome=metrics.bounded(outcome, metrics.SSO_OUTCOMES)).inc()
    log_event(logger, logging.INFO, "auth.oidc.failed", outcome=outcome)
    response = RedirectResponse(f"/sign-in?error={public_error}", status_code=303)
    response.delete_cookie(
        _binding_cookie(settings),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get(
    "/v1/auth/oidc/{login_key}/start",
    response_class=RedirectResponse,
    status_code=303,
    responses=_OIDC_REDIRECT,
    summary="Start signing in with the organization's identity provider (OIDC)",
    description="`login_key` is the connection's opaque login key (the IdP app tile or a "
    "link from the administrator). `next`: a same-origin path to return to.",
)
async def oidc_start(
    login_key: str,
    request: Request,
    settings: AppSettings,
    limits: Limits,
    next_: Annotated[str | None, Query(alias="next")] = None,
) -> Response:
    try:
        await limits.sign_in.hit(client_address(request))
        started = await _oidc(request).start(login_key, next_)
    except RateLimited:
        return _oidc_failed(settings, "rate_limited", "rate_limited")
    except OidcFailure as failure:
        return _oidc_failed(settings, failure.public_error, failure.outcome)
    response = RedirectResponse(started.authorization_url, status_code=303)
    response.set_cookie(
        _binding_cookie(settings),
        started.binding,
        max_age=600,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get(
    "/v1/auth/oidc/{login_key}/callback",
    response_class=RedirectResponse,
    status_code=303,
    responses=_OIDC_REDIRECT,
    summary="The identity provider's redirect back to Lattice (OIDC authorization code)",
)
async def oidc_callback(
    login_key: str,
    request: Request,
    settings: AppSettings,
    sessions: Sessions,
    limits: Limits,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    try:
        await limits.sign_in.hit(client_address(request))
        signed = await _oidc(request).complete(
            login_key,
            code=code,
            state=state,
            binding=request.cookies.get(_binding_cookie(settings)),
            provider_error=error is not None,
        )
        # Session rotation, as for any sign-in.
        previous = request.cookies.get(settings.session_cookie_name)
        if previous:
            await sessions.revoke_token_if_valid(previous)
        token, principal = await sessions.create(
            user_id=signed.user_id,
            organization_id=signed.organization_id,
            oidc_connection_id=signed.connection_id,
        )
    except RateLimited:
        return _oidc_failed(settings, "rate_limited", "rate_limited")
    except OidcFailure as failure:
        return _oidc_failed(settings, failure.public_error, failure.outcome)
    except LatticeError:
        # The membership stopped being active between mapping and session creation, or
        # the connection was disabled meanwhile (D-25).
        return _oidc_failed(settings, "sso_failed", "inactive")
    await _oidc(request).record_success(signed.organization_id, signed.connection_id)
    metrics.SSO_SIGN_INS.labels(outcome="succeeded").inc()
    log_event(
        logger,
        logging.INFO,
        "auth.oidc.succeeded",
        organization_id=str(principal.organization_id),
        session_id=str(principal.session_id),
    )
    response = RedirectResponse(signed.return_to, status_code=303)
    _set_session_cookie(response, settings, token)
    response.delete_cookie(
        _binding_cookie(settings),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


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
