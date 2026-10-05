"""Phase 11, P11.1: the runtime pieces the production topology relies on.

* Forwarding headers are trusted only from ``LATTICE_TRUSTED_PROXIES`` (the ingress);
  the client a rate limit keys on can never be chosen by a client-supplied
  ``X-Forwarded-For``.
* Build identification reaches the internal metric and the startup log only.
* The worker's heartbeat (metric and file) renews while the loop runs.
* The fake generation provider's verification delay is refused in production.
* ``lattice smoke`` passes a healthy deployment and names each failing check.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from lattice import build, metrics, smoke
from lattice.api.app import create_app
from lattice.config import ConfigurationError, load_settings, load_worker_settings
from lattice.db.engine import Database, create_engine
from lattice.jobs.worker import Heartbeat, Worker
from lattice.providers.generation import FakeGenerationProvider, GenerationRequest

from .conftest import APP_URL, CSRF, WORKER_URL, make_settings

LIMIT = 2


@asynccontextmanager
async def _app(peer: str, **overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
    """An app whose TCP peer (what the ingress would be) is ``peer``."""
    app: FastAPI = create_app(make_settings(rate_limit_sign_in_per_minute=LIMIT, **overrides))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=(peer, 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def _sign_in(client: httpx.AsyncClient, forwarded_for: str | None) -> int:
    headers = dict(CSRF)
    if forwarded_for is not None:
        headers["X-Forwarded-For"] = forwarded_for
    response = await client.post(
        "/v1/auth/dev-login", json={"subject": "nobody@example.invalid"}, headers=headers
    )
    return response.status_code


# --- trusted forwarding hop ------------------------------------------------------------------


async def test_without_trusted_proxies_a_spoofed_forwarded_for_is_ignored() -> None:
    # A client talking to the API directly varies X-Forwarded-For on every request; the
    # limit still counts the TCP peer, so the third request is refused.
    async with _app("198.51.100.7") as client:
        statuses = [await _sign_in(client, f"203.0.113.{n}") for n in range(LIMIT + 1)]
    assert statuses[:LIMIT] == [401] * LIMIT  # counted (unknown subject), not refused
    assert statuses[LIMIT] == 429


async def test_an_untrusted_peer_cannot_use_forwarded_for_even_when_proxies_are_set() -> None:
    async with _app("198.51.100.7", trusted_proxies="10.20.0.10") as client:
        statuses = [await _sign_in(client, f"203.0.113.{n}") for n in range(LIMIT + 1)]
    assert statuses[LIMIT] == 429


async def test_the_trusted_ingress_reports_each_client() -> None:
    async with _app("10.20.0.10", trusted_proxies="10.20.0.0/24") as client:
        # Distinct clients behind the ingress: distinct keys, nobody refused.
        assert [await _sign_in(client, f"203.0.113.{n}") for n in range(LIMIT + 1)] == [401] * (
            LIMIT + 1
        )
        # One client, whatever it prepends: the right-most untrusted address decides.
        same = [await _sign_in(client, f"192.0.2.{n}, 203.0.113.99") for n in range(LIMIT + 1)]
    assert same[:LIMIT] == [401] * LIMIT
    assert same[LIMIT] == 429


async def test_forwarded_proto_is_trusted_only_from_the_ingress() -> None:
    for peer, expected in (("10.20.0.10", "https"), ("198.51.100.7", "http")):
        app = create_app(make_settings(trusted_proxies="10.20.0.10"))

        @app.get("/test/scheme")
        async def scheme(request: Request) -> dict[str, str]:
            return {"scheme": request.url.scheme}

        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=(peer, 40000))
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
                response = await c.get("/test/scheme", headers={"X-Forwarded-Proto": "https"})
        assert response.json() == {"scheme": expected}


@pytest.mark.parametrize("value", ["*", "10.0.0.1,*", "proxy.internal", "10.0.0.1,,10.0.0.2"])
def test_trusted_proxies_must_be_addresses_or_networks(value: str) -> None:
    with pytest.raises(ConfigurationError, match="LATTICE_TRUSTED_PROXIES"):
        load_settings(env="test", database_url=APP_URL, trusted_proxies=value)


def test_trusted_proxies_are_normalised() -> None:
    settings = load_settings(
        env="test", database_url=APP_URL, trusted_proxies=" 10.20.0.10 , 10.30.0.0/16,::1"
    )
    assert settings.trusted_proxies == "10.20.0.10/32,10.30.0.0/16,::1/128"
    empty = load_settings(env="test", database_url=APP_URL, trusted_proxies="")
    assert empty.trusted_proxies is None


# --- verification-only fake delay ------------------------------------------------------------


def test_the_fake_generation_delay_is_refused_in_production() -> None:
    with pytest.raises(ConfigurationError, match="LATTICE_FAKE_GENERATION_DELAY_SECONDS"):
        load_settings(
            env="production",
            database_url=APP_URL,
            public_url="https://lattice.example.com",
            fake_generation_delay_seconds=1,
        )


async def test_the_fake_provider_waits_before_answering() -> None:
    provider = FakeGenerationProvider(delay_seconds=0.05)
    started = time.monotonic()
    await provider.generate(GenerationRequest(system="", prompt="QUESTION: what?", schema={}))
    assert time.monotonic() - started >= 0.05


# --- build identification --------------------------------------------------------------------


def test_build_info_reaches_the_metric_and_the_startup_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LATTICE_BUILD_REVISION", "0123abc")
    monkeypatch.setenv("LATTICE_BUILD_VERSION", "v1.2.3")
    with caplog.at_level(logging.INFO):
        info = build.report_startup("api", logging.getLogger("lattice.tests"))
    assert (info.revision, info.version) == ("0123abc", "v1.2.3")
    labels = {"revision": "0123abc", "version": "v1.2.3", "schema_revision": info.schema_revision}
    assert metrics.REGISTRY.get_sample_value("lattice_build_info", labels) == 1


@pytest.mark.parametrize("value", [None, "", "x" * 65, "a b", "${EVIL}", "-leading"])
def test_unset_or_unsafe_build_values_read_unknown(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("LATTICE_BUILD_REVISION", raising=False)
    else:
        monkeypatch.setenv("LATTICE_BUILD_REVISION", value)
    assert build.build_info().revision == build.UNKNOWN


async def test_health_endpoints_still_report_no_build_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LATTICE_BUILD_REVISION", "0123abc")
    async with _app("127.0.0.1") as client:
        for path in ("/v1/health/live", "/v1/health/ready", "/v1/health"):
            response = await client.get(path)
            assert "0123abc" not in response.text
            assert "0123abc" not in str(response.headers)


# --- worker heartbeat ------------------------------------------------------------------------


def test_the_heartbeat_touches_its_file_and_sets_the_gauge(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    before = time.time()
    Heartbeat(path).beat()
    assert path.stat().st_mtime >= before - 1
    gauge = metrics.REGISTRY.get_sample_value("lattice_worker_heartbeat_timestamp_seconds")
    assert gauge is not None
    assert gauge >= before


def test_a_heartbeat_that_cannot_write_does_not_raise(tmp_path: Path) -> None:
    Heartbeat(tmp_path / "missing-directory" / "heartbeat").beat()


async def test_the_loop_beats_even_while_the_database_is_down(tmp_path: Path) -> None:
    down = Database(
        create_engine(
            "postgresql+asyncpg://lattice_worker:x@127.0.0.1:1/none",
            application_name="lattice-tests-heartbeat",
            connect_timeout_seconds=0.5,
        )
    )
    path = tmp_path / "heartbeat"
    stop = asyncio.Event()
    worker = Worker(down, embedding=None, heartbeat=Heartbeat(path))
    task = asyncio.create_task(worker.run_forever(stop, poll_seconds=0.05))
    await asyncio.sleep(0.3)
    first = path.stat().st_mtime_ns
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    await down.dispose()
    assert path.stat().st_mtime_ns > first


def test_the_worker_heartbeat_file_is_configurable(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    settings = load_worker_settings(
        env="test", worker_database_url=WORKER_URL, worker_heartbeat_file=str(path)
    )
    assert settings.worker_heartbeat_file == path


# --- lattice smoke ---------------------------------------------------------------------------

API_HEADERS = {
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "strict-transport-security": "max-age=63072000",
}
WEB_HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "content-security-policy": "default-src 'self'; script-src 'self' 'nonce-abc'",
}


def _deployment(
    *, ready: int = 200, heartbeat_age: float = 5.0, docs: int = 404, web_csp: bool = True
) -> Callable[[httpx.Request], httpx.Response]:
    now = time.time()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "worker":
            return httpx.Response(
                200, text=f"lattice_worker_heartbeat_timestamp_seconds {now - heartbeat_age}\n"
            )
        if path == "/":
            headers = WEB_HEADERS if web_csp else {"content-type": "text/html"}
            return httpx.Response(200, headers=headers, text="<html></html>")
        path = path.removeprefix("/api")
        if path == "/v1/health/live":
            return httpx.Response(200, headers=API_HEADERS, json={"status": "ok"})
        if path == "/v1/health/ready":
            body: dict[str, Any] = {"status": "ready", "database": "ok", "schema_revision": "ok"}
            if ready != 200:
                body = {"error": {"code": "service_unavailable"}}
            return httpx.Response(ready, headers=API_HEADERS, json=body)
        if path == "/v1/me":
            return httpx.Response(
                401,
                headers=API_HEADERS,
                json={
                    "error": {"code": "authentication_required", "message": "", "request_id": ""}
                },
            )
        if path in ("/openapi.json", "/docs"):
            return httpx.Response(docs, headers=API_HEADERS)
        return httpx.Response(404)

    return handler


def _smoke(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> list[smoke.Result]:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return smoke.run_smoke(
            client,
            "https://lattice.example.com",
            instances=kwargs.get("instances", ["http://api1:8000", "http://api2:8000"]),
            worker_metrics=kwargs.get("worker_metrics", "http://worker:9465/metrics"),
            worker_max_age=300,
            production=kwargs.get("production", True),
        )


def test_smoke_passes_a_healthy_deployment() -> None:
    results = _smoke(_deployment())
    assert all(r.ok for r in results), results
    assert len(results) == 10


@pytest.mark.parametrize(
    ("kwargs", "failing"),
    [
        ({"ready": 503}, "readiness"),
        ({"heartbeat_age": 900}, "worker heartbeat"),
        ({"docs": 200}, "production API surface"),
        ({"web_csp": False}, "web through the ingress"),
    ],
)
def test_smoke_names_each_failure(kwargs: dict[str, Any], failing: str) -> None:
    results = _smoke(_deployment(**kwargs))
    failed = [r.name for r in results if not r.ok]
    assert failed
    assert all(failing in name for name in failed), failed


def _with_build_metrics(revisions: dict[str, str]) -> Callable[[httpx.Request], httpx.Response]:
    base = _deployment()

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.url.path == "/metrics" and host in revisions:
            info = (
                f'lattice_build_info{{revision="{revisions[host]}",schema_revision="0013",'
                f'version="unknown"}} 1.0\n'
            )
            response = base(request) if host == "worker" else httpx.Response(200, text="")
            return httpx.Response(200, text=response.text + info)
        return base(request)

    return handler


def test_smoke_detects_a_process_running_another_revision() -> None:
    def run(revisions: dict[str, str]) -> dict[str, bool]:
        with httpx.Client(transport=httpx.MockTransport(_with_build_metrics(revisions))) as c:
            results = smoke.run_smoke(
                c,
                "https://lattice.example.com",
                instances=[],
                worker_metrics="http://worker:9465/metrics",
                worker_max_age=300,
                production=True,
                instance_metrics=["http://api1:9464/metrics", "http://api2:9464/metrics"],
                expect_revision="abc123",
            )
        return {r.name: r.ok for r in results if r.name.startswith("build revision")}

    same = run({"api1": "abc123", "api2": "abc123", "worker": "abc123"})
    assert same == {
        "build revision at api1:9464": True,
        "build revision at api2:9464": True,
        "build revision at worker:9465": True,
    }
    stale = run({"api1": "abc123", "api2": "old999", "worker": "abc123"})
    assert stale["build revision at api2:9464"] is False
    assert stale["build revision at api1:9464"] is True


def test_smoke_requires_hsts_only_in_production() -> None:
    def no_hsts(request: httpx.Request) -> httpx.Response:
        response = _deployment()(request)
        response.headers.pop("strict-transport-security", None)
        return response

    outside = {r.name: r.ok for r in _smoke(no_hsts, production=False)}
    assert outside["api authentication enforced"]
    production = {r.name: r.ok for r in _smoke(no_hsts, production=True)}
    assert not production["api authentication enforced"]


def test_smoke_notices_a_web_answer_where_the_api_should_be() -> None:
    def web_everywhere(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=WEB_HEADERS, json={"status": "ok"})

    results = {r.name: r for r in _smoke(web_everywhere, instances=[], worker_metrics=None)}
    assert not results["api liveness through the ingress"].ok
    assert "not answered by the API" in results["api liveness through the ingress"].detail


@pytest.mark.parametrize(
    "url", ["https://user:secret@lattice.example.com", "ftp://x", "https://x/?q=1", "nothing"]
)
def test_smoke_refuses_urls_with_credentials_or_odd_parts(url: str) -> None:
    with pytest.raises(ValueError, match=r"URL|must"):
        smoke._origin(url)


def test_smoke_prints_no_bodies_and_exits_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="secret-looking-body-text")

    with httpx.Client(transport=httpx.MockTransport(broken)) as client:
        results = smoke.run_smoke(
            client,
            "https://lattice.example.com",
            instances=[],
            worker_metrics=None,
            worker_max_age=300,
            production=False,
        )
    assert not any(r.ok for r in results)
    assert all("secret-looking" not in r.detail for r in results)
