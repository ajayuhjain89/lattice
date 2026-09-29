"""Operational behaviour of the API process (Phase 8): health and readiness, the
schema-revision guard, database failures, request limits, and metrics.

Every failure here must be predictable and safe: a fixed error envelope, no stack
traces, SQL, revisions or hosts in a response, and nothing unbounded in a metric."""

import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from prometheus_client import generate_latest
from sqlalchemy import text

from lattice import metrics
from lattice.api.app import create_app
from lattice.authz import AuthorizationInvariantViolation
from lattice.config import ConfigurationError, Environment, load_settings
from lattice.db.engine import Database, create_engine
from lattice.db.role_check import UnsafeDatabaseRoleError, inspect_runtime_role
from lattice.db.schema import SCHEMA_REVISION

from .conftest import APP_URL, CSRF, AppFactory, make_settings


def _sample(name: str, **labels: str) -> float:
    value = metrics.REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


@asynccontextmanager
async def _revision(owner_db: Database, revision: str) -> AsyncIterator[None]:
    """Pretend the database is at another revision (restored afterwards)."""
    async with owner_db.unscoped_transaction() as s:
        await s.execute(text("UPDATE alembic_version SET version_num = :r"), {"r": revision})
    try:
        yield
    finally:
        async with owner_db.unscoped_transaction() as s:
            await s.execute(
                text("UPDATE alembic_version SET version_num = :r"), {"r": SCHEMA_REVISION}
            )


# --- health and readiness ------------------------------------------------------------------


async def test_liveness_and_readiness_report_states_only(client: httpx.AsyncClient) -> None:
    live = await client.get("/v1/health/live")
    ready = await client.get("/v1/health/ready")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "database": "ok", "schema_revision": "ok"}
    assert SCHEMA_REVISION not in ready.text


async def test_readiness_fails_when_the_schema_does_not_match(
    client: httpx.AsyncClient, owner_db: Database
) -> None:
    async with _revision(owner_db, "0009"):
        ready = await client.get("/v1/health/ready")
        live = await client.get("/v1/health/live")
    assert ready.status_code == 503
    assert ready.json()["error"]["code"] == "service_unavailable"
    assert ready.headers["retry-after"] == "5"
    assert "0009" not in ready.text
    assert live.status_code == 200  # a restart would not help


async def test_startup_refuses_a_schema_of_another_revision(owner_db: Database) -> None:
    async with _revision(owner_db, "0009"):
        engine = create_engine(APP_URL, application_name="lattice-tests-revision", pool_size=1)
        try:
            report = await inspect_runtime_role(engine)
        finally:
            await engine.dispose()
        assert any("0009" in p and SCHEMA_REVISION in p for p in report.problems)
        app = create_app(make_settings())
        with pytest.raises(UnsafeDatabaseRoleError, match="expects"):
            async with app.router.lifespan_context(app):
                pass


def _point_at_nothing(app: FastAPI) -> None:
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: FastAPI) -> AsyncIterator[None]:
        async with original(a):
            a.state.db = Database(
                create_engine(
                    "postgresql+asyncpg://lattice_app:x@127.0.0.1:1/none",
                    application_name="lattice-tests-down",
                    connect_timeout_seconds=1.0,
                )
            )
            yield

    app.router.lifespan_context = lifespan


async def test_readiness_fails_when_the_database_is_down(app_client: AppFactory) -> None:
    before = _sample("lattice_dependency_failures_total", dependency="database", check="ready")
    async with app_client(configure=_point_at_nothing) as client:
        ready = await client.get("/v1/health/ready")
        live = await client.get("/v1/health/live")
    assert ready.status_code == 503
    assert ready.json()["error"]["code"] == "service_unavailable"
    assert "127.0.0.1" not in ready.text
    assert live.status_code == 200
    after = _sample("lattice_dependency_failures_total", dependency="database", check="ready")
    assert after == before + 1


# --- database failures during a request ----------------------------------------------------


def _add_routes(app: FastAPI) -> None:
    @app.get("/v1/_test/slow-query")
    async def slow() -> None:
        db: Database = app.state.db
        async with db.unscoped_transaction() as s:
            await s.execute(text("SELECT pg_sleep(5)"))

    @app.get("/v1/_test/unreachable")
    async def unreachable() -> None:
        db = Database(
            create_engine(
                "postgresql+asyncpg://lattice_app:x@127.0.0.1:1/none",
                application_name="lattice-tests-down",
                connect_timeout_seconds=1.0,
            )
        )
        try:
            async with db.unscoped_transaction() as s:
                await s.execute(text("SELECT 1"))
        finally:
            await db.dispose()

    @app.get("/v1/_test/invariant")
    async def invariant() -> None:
        raise AuthorizationInvariantViolation()


async def test_a_cancelled_statement_is_a_503_not_a_hang(app_client: AppFactory) -> None:
    async with app_client(configure=_add_routes, db_statement_timeout_ms=1000) as client:
        response = await client.get("/v1/_test/slow-query")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert response.headers["retry-after"] == "5"
    assert "pg_sleep" not in response.text
    assert "canceling" not in response.text


async def test_an_unreachable_database_is_a_503(app_client: AppFactory) -> None:
    async with app_client(configure=_add_routes) as client:
        response = await client.get("/v1/_test/unreachable")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "127.0.0.1" not in response.text


async def test_an_invariant_violation_is_reported_by_its_code_only(
    app_client: AppFactory,
) -> None:
    before = _sample("lattice_security_invariant_violations_total", check="authorization")
    async with app_client(configure=_add_routes) as client:
        response = await client.get("/v1/_test/invariant")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "security_invariant_violated"
    assert set(error) == {"code", "message", "request_id"}
    after = _sample("lattice_security_invariant_violations_total", check="authorization")
    assert after == before + 1


# --- request limits ------------------------------------------------------------------------


async def test_an_oversized_body_is_refused_before_parsing(app_client: AppFactory) -> None:
    async with app_client(max_request_bytes=2048) as client:
        declared = await client.post(
            "/v1/ask",
            content=b'{"question": "' + b"x" * 4096 + b'"}',
            headers={**CSRF, "content-type": "application/json"},
        )

        async def stream() -> AsyncIterator[bytes]:
            for _ in range(8):
                yield b"y" * 1024

        streamed = await client.post(
            "/v1/ask",
            content=stream(),
            headers={**CSRF, "content-type": "application/json"},
        )
        fits = await client.post("/v1/ask", json={"question": "hi"}, headers=CSRF)
    for response in (declared, streamed):
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "request_too_large"
        assert "x" * 20 not in response.text
    assert fits.status_code == 401  # within the limit: reaches authentication


# --- metrics -------------------------------------------------------------------------------


async def test_http_metrics_use_route_templates_not_paths(client: httpx.AsyncClient) -> None:
    trace_id = uuid.uuid4()
    route = "/v1/traces/{trace_id}"
    before = _sample("lattice_http_requests_total", method="GET", route=route, status="401")
    await client.get(f"/v1/traces/{trace_id}")
    await client.get(f"/v1/no-such-route/{trace_id}")
    after = _sample("lattice_http_requests_total", method="GET", route=route, status="401")
    assert after == before + 1
    assert _sample("lattice_http_requests_total", method="GET", route="unmatched", status="404")
    exposition = generate_latest(metrics.REGISTRY).decode()
    assert str(trace_id) not in exposition
    assert "no-such-route" not in exposition


async def test_metrics_are_not_served_on_the_application_port(client: httpx.AsyncClient) -> None:
    assert (await client.get("/metrics")).status_code == 404


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_the_metrics_listener_serves_and_stops(app_client: AppFactory) -> None:
    port = _free_port()
    async with app_client(metrics_address=f"127.0.0.1:{port}") as client:
        await client.get("/v1/health/live")
        async with httpx.AsyncClient() as scraper:
            scraped = await scraper.get(f"http://127.0.0.1:{port}/metrics")
    assert scraped.status_code == 200
    assert "lattice_http_requests_total" in scraped.text
    assert "process_resident_memory_bytes" in scraped.text or "python_info" in scraped.text
    with socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", port)) != 0  # closed with the app


def test_bounded_labels() -> None:
    assert metrics.bounded("answered", metrics.ANSWER_STATUSES) == "answered"
    assert metrics.bounded("Acme Corp", metrics.ANSWER_STATUSES) == "other"
    assert metrics.bounded(None, metrics.ANSWER_STATUSES) == "none"
    assert metrics.outcome_of("generation_failed:rate_limited") == "rate_limited"
    assert metrics.outcome_of("generation_failed:something new") == "other"
    assert metrics.outcome_of(None) == "ok"


# --- configuration -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "variable"),
    [
        (
            {
                "env": "production",
                "dev_auth_enabled": "false",
                "session_cookie_secure": "true",
                "query_embedding": "fake",
            },
            "LATTICE_QUERY_EMBEDDING=fake",
        ),
        ({"metrics_address": "9464"}, "LATTICE_METRICS_ADDRESS"),
        ({"metrics_address": "localhost:99999"}, "LATTICE_METRICS_ADDRESS"),
        ({"generation_deadline_seconds": "0"}, "LATTICE_GENERATION_DEADLINE_SECONDS"),
        ({"db_statement_timeout_ms": "10"}, "LATTICE_DB_STATEMENT_TIMEOUT_MS"),
        ({"max_request_bytes": "10"}, "LATTICE_MAX_REQUEST_BYTES"),
    ],
)
def test_unsafe_operational_settings_refuse_to_start(
    overrides: dict[str, str], variable: str
) -> None:
    values = {"env": "test", "database_url": APP_URL, **overrides}
    with pytest.raises(ConfigurationError, match=variable.split("=")[0]) as caught:
        load_settings(**values)
    assert APP_URL not in str(caught.value)


def test_operational_defaults() -> None:
    settings = load_settings(env=Environment.TEST, database_url=APP_URL)
    assert settings.generation_enabled is True
    assert settings.generation_deadline_seconds == 120.0
    assert settings.metrics_address is None
    assert settings.max_request_bytes == 65536
