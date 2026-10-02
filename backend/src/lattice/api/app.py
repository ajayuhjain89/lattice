"""Application factory.

``uvicorn lattice.api.app:create_app --factory`` loads settings from the environment
(fail-fast), configures logging, and at startup refuses to serve unless the runtime
database role is safe and the schema is at the revision this build expects
(lattice.db.role_check). With ``LATTICE_METRICS_ADDRESS`` set it also serves
Prometheus metrics on that separate listener (lattice.metrics).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI

from lattice import metrics
from lattice.answering.service import AskDependencies
from lattice.api.handlers import register_exception_handlers
from lattice.api.middleware import BodyLimitMiddleware, CsrfMiddleware, RequestContextMiddleware
from lattice.api.routes import access, activity, admin, ask, auth, health, me, sources
from lattice.config import Environment, Settings, load_provider_settings, load_settings
from lattice.db.engine import Database, create_engine
from lattice.db.role_check import verify_runtime_role
from lattice.identity.dev_provider import DevIdentityProvider
from lattice.identity.sessions import SessionPolicy, SessionService
from lattice.log import configure_logging
from lattice.providers.factory import build_embedding_provider
from lattice.providers.generation_factory import DefaultGenerationFactory
from lattice.providers.secrets import EnvironmentSecretStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    production = settings.env is Environment.PRODUCTION
    configure_logging(
        level=settings.log_level,
        include_exception_text=settings.env is Environment.DEVELOPMENT,
    )

    db = Database(
        create_engine(
            settings.database_url.get_secret_value(),
            application_name="lattice-api",
            pool_size=settings.db_pool_size,
            statement_timeout_ms=settings.db_statement_timeout_ms,
            connect_timeout_seconds=settings.db_connect_timeout_seconds,
        )
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await verify_runtime_role(db.engine, forbid_content_writes=True)
        server = metrics.serve(settings.metrics_address) if settings.metrics_address else None
        try:
            yield
        finally:
            if server is not None:
                server[0].shutdown()  # type: ignore[attr-defined]
                server[0].server_close()  # type: ignore[attr-defined]
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

    # Ask (Phase 6): the query-embedding provider is a deployment setting; generation is
    # resolved per organization from its recorded configuration and credential
    # references, per request, through the invocation rule.
    app.state.ask_dependencies = AskDependencies(
        embedding=(
            None
            if settings.query_embedding == "none"
            else build_embedding_provider(settings.query_embedding, load_provider_settings())
        ),
        generation=DefaultGenerationFactory(settings.env),
        secrets=EnvironmentSecretStore(),
        generation_enabled=settings.generation_enabled,
        generation_deadline_seconds=settings.generation_deadline_seconds,
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(ask.router)
    app.include_router(admin.router)
    app.include_router(sources.router)
    app.include_router(activity.router)
    app.include_router(access.router)
    if settings.dev_auth_active:
        app.state.dev_identity_provider = DevIdentityProvider(settings, db)
        app.include_router(auth.dev_router)

    register_exception_handlers(app, settings)
    # add_middleware wraps: the last added is outermost.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(RequestContextMiddleware, production=production)
    return app
