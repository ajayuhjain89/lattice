"""Request dependencies. The organization of every authenticated request comes from
the server-side session resolved here -- there is no other source of tenant context."""

from typing import Annotated

from fastapi import Depends, Request

from lattice import metrics
from lattice.config import Settings
from lattice.errors import ErrorCode, LatticeError
from lattice.identity.sessions import Principal, SessionService
from lattice.ratelimit import ApiLimits


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_service(request: Request) -> SessionService:
    service: SessionService = request.app.state.session_service
    return service


async def get_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    sessions: Annotated[SessionService, Depends(get_session_service)],
) -> Principal:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        metrics.AUTH_REJECTED.labels(reason="missing").inc()
        raise LatticeError(ErrorCode.AUTHENTICATION_REQUIRED)
    principal = await sessions.authenticate(token)
    # IDs only, for the request log line.
    request.state.log_fields = {
        "organization_id": str(principal.organization_id),
        "user_id": str(principal.user_id),
    }
    return principal


def get_limits(request: Request) -> ApiLimits:
    limits: ApiLimits = request.app.state.limits
    return limits


def client_address(request: Request) -> str | None:
    """The peer address as the server sees it. Behind a reverse proxy, run uvicorn with
    ``--proxy-headers --forwarded-allow-ips=<proxy>`` so this is the client's address
    (runbook §7); a forwarded header from anyone else is ignored."""
    return request.client.host if request.client else None


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Sessions = Annotated[SessionService, Depends(get_session_service)]
Limits = Annotated[ApiLimits, Depends(get_limits)]
