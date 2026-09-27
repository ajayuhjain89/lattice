"""Request dependencies. The organization of every authenticated request comes from
the server-side session resolved here -- there is no other source of tenant context."""

from typing import Annotated

from fastapi import Depends, Request

from lattice.config import Settings
from lattice.errors import ErrorCode, LatticeError
from lattice.identity.sessions import Principal, SessionService


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
        raise LatticeError(ErrorCode.AUTHENTICATION_REQUIRED)
    principal = await sessions.authenticate(token)
    # IDs only, for the request log line.
    request.state.log_fields = {
        "organization_id": str(principal.organization_id),
        "user_id": str(principal.user_id),
    }
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Sessions = Annotated[SessionService, Depends(get_session_service)]
