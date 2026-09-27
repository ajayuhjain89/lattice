"""Application factory.

``uvicorn lattice.api.app:create_app --factory`` loads settings from the environment
(fail-fast), configures logging, and at startup refuses to serve unless the runtime
database role is safe (lattice.db.role_check).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI

from lattice.api.handlers import register_exception_handlers
from lattice.api.middleware import CsrfMiddleware, RequestContextMiddleware
from lattice.api.routes import auth, health, me
from lattice.config import Environment, Settings, load_settings
from lattice.db.engine import Database, create_engine
from lattice.db.role_check import verify_runtime_role
from lattice.identity.dev_provider import DevIdentityProvider
from lattice.identity.sessions import SessionPolicy, SessionService
from lattice.log import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    production = settings.env is Environment.PRODUCTION
    configure_logging(
        level=settings.log_level,
        include_exception_text=settings.env is Environment.DEVELOPMENT,
    )

    db = Database(
        create_engine(settings.database_url.get_secret_value(), application_name="lattice-api")
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await verify_runtime_role(db.engine)
        yield
        await db.dispose()

    app = FastAPI(
        title="Lattice API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.db = db
    app.state.session_service = SessionService(
        db,
        SessionPolicy(
            idle_timeout=timedelta(minutes=settings.session_idle_timeout_minutes),
            absolute_timeout=timedelta(minutes=settings.session_absolute_timeout_minutes),
        ),
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    if settings.dev_auth_active:
        app.state.dev_identity_provider = DevIdentityProvider(settings, db)
        app.include_router(auth.dev_router)

    register_exception_handlers(app, settings)
    # add_middleware wraps: the last added is outermost.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(RequestContextMiddleware, production=production)
    return app
