"""Error envelope contract (docs/architecture.md §7): typed, stable, non-leaking."""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from lattice.config import Environment
from lattice.db.engine import Database, create_engine

from .conftest import CSRF, AppFactory

SENSITIVE = "password=hunter2 at /srv/lattice/secret.py SELECT * FROM sessions"


def _add_failing_routes(app: FastAPI) -> None:
    @app.get("/v1/_test/crash")
    async def crash() -> None:
        raise RuntimeError(SENSITIVE)

    @app.get("/v1/_test/db-error")
    async def db_error() -> None:
        db: Database = app.state.db
        async with db.unscoped_transaction() as s:
            await s.execute(text("SELECT * FROM table_that_does_not_exist_hunter2"))


def _assert_envelope(response: httpx.Response, code: str, status: int) -> dict[str, object]:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str)
    assert error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    assert set(error) <= {"code", "message", "request_id", "details"}
    return dict(error)


async def test_unauthenticated(client: httpx.AsyncClient) -> None:
    _assert_envelope(await client.get("/v1/me"), "authentication_required", 401)


async def test_unknown_route(client: httpx.AsyncClient) -> None:
    _assert_envelope(await client.get("/v1/documents"), "not_found", 404)


async def test_wrong_method(client: httpx.AsyncClient) -> None:
    _assert_envelope(await client.get("/v1/auth/logout"), "method_not_allowed", 405)


async def test_validation_details_never_echo_submitted_values(client: httpx.AsyncClient) -> None:
    secret = "s3cr3t-" * 60  # too long for the subject field
    response = await client.post(
        "/v1/auth/dev-login", json={"subject": secret, "unexpected": secret}, headers=CSRF
    )
    error = _assert_envelope(response, "validation_failed", 422)
    assert error["details"] == [
        {"field": "subject", "issue": "string_too_long"},
        {"field": "unexpected", "issue": "extra_forbidden"},
    ]
    assert "s3cr3t" not in response.text


async def test_malformed_json(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/dev-login",
        content=b"{not json",
        headers={**CSRF, "content-type": "application/json"},
    )
    _assert_envelope(response, "validation_failed", 422)


@pytest.mark.parametrize("path", ["/v1/_test/crash", "/v1/_test/db-error"])
async def test_internal_errors_expose_nothing(app_client: AppFactory, path: str) -> None:
    async with app_client(configure=_add_failing_routes) as client:
        response = await client.get(path)
    error = _assert_envelope(response, "internal_error", 500)
    assert "details" not in error
    for fragment in ("hunter2", "SELECT", "secret.py", "Traceback", "table_that_does_not_exist"):
        assert fragment not in response.text
    # Security headers are present even on failures.
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_database_outage_reports_service_unavailable(app_client: AppFactory) -> None:
    def break_database(app: FastAPI) -> None:
        # After the startup check has run against the real database, point the app at a
        # port where nothing listens.
        original_startup = app.router.lifespan_context

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def lifespan(a: FastAPI):  # type: ignore[no-untyped-def]
            async with original_startup(a):
                a.state.db = Database(
                    create_engine(
                        "postgresql+asyncpg://lattice_app:x@127.0.0.1:1/none",
                        application_name="lattice-tests-down",
                    )
                )
                yield

        app.router.lifespan_context = lifespan

    async with app_client(configure=break_database) as client:
        response = await client.get("/v1/health")
    _assert_envelope(response, "service_unavailable", 503)


async def test_production_hides_api_docs_and_sets_hsts(app_client: AppFactory) -> None:
    async with app_client(
        env=Environment.PRODUCTION,
        dev_auth_enabled=False,
        session_cookie_secure=True,
        public_url="https://lattice.example.com",
    ) as prod:
        for path in ("/docs", "/openapi.json", "/redoc"):
            assert (await prod.get(path)).status_code == 404
        health = await prod.get("/v1/health")
    assert health.status_code == 200
    assert "max-age" in health.headers["strict-transport-security"]
    assert health.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


@pytest.mark.parametrize("path", ["/v1/health", "/v1/me", "/v1/no-such-route", "/scim/v2/Users"])
async def test_every_response_carries_the_security_headers(
    client: httpx.AsyncClient, path: str
) -> None:
    response = await client.get(path)
    expected = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "same-origin",
        "cache-control": "no-store",
        "cross-origin-opener-policy": "same-origin",
        "cross-origin-resource-policy": "same-origin",
        "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    }
    for name, value in expected.items():
        assert response.headers[name] == value, name
    assert "camera=()" in response.headers["permissions-policy"]
    # Lattice grants no cross-origin access.
    assert "access-control-allow-origin" not in response.headers


async def test_openapi_documents_the_error_envelope(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert "ErrorResponse" in schema["components"]["schemas"]
    paths = set(schema["paths"])
    assert paths == {
        "/v1/health",
        "/v1/health/live",
        "/v1/health/ready",
        "/v1/auth/dev-login",
        "/v1/auth/logout",
        "/v1/me",
        # Phase 6
        "/v1/ask",
        "/v1/evidence/{evidence_id}",
        "/v1/traces/{trace_id}",
        "/v1/admin/providers",
        # Phase 8
        "/v1/sources",
        "/v1/admin/sources",
        "/v1/admin/sources/{source_id}/failures",
        "/v1/admin/sources/{source_id}/sync",
        "/v1/activity",
        "/v1/admin/activity",
        # Phase 9
        "/v1/accounts",
        "/v1/admin/access/members",
        "/v1/admin/access/directory",
        "/v1/admin/access/members/{membership_id}",
        "/v1/admin/access/members/{membership_id}/preview",
        "/v1/admin/access/documents",
        "/v1/admin/access/members/{membership_id}/documents/{document_id}",
        "/v1/admin/access/members/{membership_id}/grants",
        "/v1/admin/access/grants/{grant_id}/revoke",
        "/v1/admin/audit-events",
        # Phase 10 (SCIM is outside the OpenAPI document: SCIM discovers itself)
        "/v1/auth/oidc/{login_key}/start",
        "/v1/auth/oidc/{login_key}/callback",
    }
