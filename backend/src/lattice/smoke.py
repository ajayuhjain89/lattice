"""``lattice smoke``: is this deployment fundamentally usable? (Phase 11, P11.1)

A short, read-only black-box check of a running deployment, from the outside in:

* **web** -- the public origin serves the web app's HTML with its nonce-based
  Content-Security-Policy;
* **ingress → API** -- ``/api/v1/health/live`` and ``/api/v1/health/ready`` reach the
  API (its own ``default-src 'none'`` policy proves the answer did not come from the web
  server) and the database and schema are ready;
* **authentication enforced** -- ``/api/v1/me`` without a session is 401
  ``authentication_required``, with the API's security headers;
* **production surface** (``--production``) -- HSTS on API responses (over HTTPS) and
  the API documentation not served;
* **each API instance** (``--instance URL``, repeatable, from the private network) is
  live and ready;
* **worker** (``--worker-metrics URL``, from the private network) -- its heartbeat is
  younger than ``--worker-max-age`` seconds.

It sends no credentials, creates nothing and prints only check names, statuses and
fixed reasons -- never response bodies, cookies or header values. Exit 0 when every
check passes, 1 otherwise, 2 on bad arguments.

Not covered yet: an authenticated Ask. Production sign-in is OIDC only; P11.3's
verification identity provider makes an end-to-end Ask possible and extends this
command.
"""

import argparse
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import httpx

TIMEOUT_SECONDS: Final = 10.0
API_CSP: Final = "default-src 'none'"
_HEARTBEAT: Final = re.compile(
    r"^lattice_worker_heartbeat_timestamp_seconds\s+([0-9.eE+]+)\s*$", re.MULTILINE
)


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str


class SmokeError(Exception):
    """A check failed with a fixed, printable reason."""


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http(s) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("must not carry credentials, a query or a fragment")
    return url.rstrip("/")


def _json(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError:
        raise SmokeError("response is not JSON") from None
    if not isinstance(body, dict):
        raise SmokeError("response is not a JSON object")
    return body


def _expect(response: httpx.Response, status: int) -> None:
    if response.status_code != status:
        raise SmokeError(f"HTTP {response.status_code}, expected {status}")


def _from_api(response: httpx.Response) -> None:
    if response.headers.get("content-security-policy", "").split(";")[0] != API_CSP:
        raise SmokeError("not answered by the API (missing its Content-Security-Policy)")


class Smoke:
    def __init__(self, client: httpx.Client, clock: Callable[[], float] = time.time) -> None:
        self._client = client
        self._clock = clock
        self.results: list[Result] = []

    def check(self, name: str, probe: Callable[[], str]) -> None:
        try:
            detail = probe()
        except SmokeError as exc:
            self.results.append(Result(name, False, str(exc)))
        except httpx.HTTPError as exc:
            self.results.append(Result(name, False, f"request failed ({type(exc).__name__})"))
        else:
            self.results.append(Result(name, True, detail))

    # --- probes ------------------------------------------------------------------------

    def web(self, origin: str) -> str:
        response = self._client.get(f"{origin}/")
        _expect(response, 200)
        if not response.headers.get("content-type", "").startswith("text/html"):
            raise SmokeError("not HTML")
        if "'nonce-" not in response.headers.get("content-security-policy", ""):
            raise SmokeError("missing the web app's nonce Content-Security-Policy")
        return "HTML with a nonce CSP"

    def live(self, base: str) -> str:
        response = self._client.get(f"{base}/v1/health/live")
        _expect(response, 200)
        _from_api(response)
        if _json(response).get("status") != "ok":
            raise SmokeError("unexpected liveness state")
        return "live"

    def ready(self, base: str) -> str:
        response = self._client.get(f"{base}/v1/health/ready")
        _expect(response, 200)
        _from_api(response)
        body = _json(response)
        if body.get("status") != "ready" or body.get("schema_revision") != "ok":
            raise SmokeError("database or schema not ready")
        return "database and schema ready"

    def authentication(self, base: str, require_hsts: bool) -> str:
        response = self._client.get(f"{base}/v1/me")
        _expect(response, 401)
        _from_api(response)
        error = _json(response).get("error")
        if not isinstance(error, dict) or error.get("code") != "authentication_required":
            raise SmokeError("unexpected error code")
        if response.headers.get("x-content-type-options") != "nosniff":
            raise SmokeError("missing X-Content-Type-Options")
        if require_hsts and "max-age" not in response.headers.get("strict-transport-security", ""):
            raise SmokeError("missing HSTS")
        return "401 authentication_required with security headers"

    def production_surface(self, base: str) -> str:
        for path in ("/openapi.json", "/docs"):
            if self._client.get(f"{base}{path}").status_code != 404:
                raise SmokeError("API documentation is served")
        return "API documentation not served"

    def worker(self, metrics_url: str, max_age: float) -> str:
        response = self._client.get(metrics_url)
        _expect(response, 200)
        found = _HEARTBEAT.search(response.text)
        if found is None:
            raise SmokeError("no worker heartbeat metric")
        age = self._clock() - float(found.group(1))
        if not 0 <= age <= max_age:
            raise SmokeError(f"heartbeat {age:.0f} s old (limit {max_age:.0f} s)")
        return f"heartbeat {age:.0f} s old"


def run_smoke(
    client: httpx.Client,
    url: str,
    *,
    instances: list[str],
    worker_metrics: str | None,
    worker_max_age: float,
    production: bool,
) -> list[Result]:
    origin = _origin(url)
    api = f"{origin}/api"
    smoke = Smoke(client)
    smoke.check("web through the ingress", lambda: smoke.web(origin))
    smoke.check("api liveness through the ingress", lambda: smoke.live(api))
    smoke.check("api readiness through the ingress", lambda: smoke.ready(api))
    smoke.check(
        "api authentication enforced",
        lambda: smoke.authentication(api, production and origin.startswith("https://")),
    )
    if production:
        smoke.check("production API surface", lambda: smoke.production_surface(api))
    for instance in instances:
        base = _origin(instance)
        host = urlsplit(base).netloc
        smoke.check(f"api instance {host} liveness", partial(smoke.live, base))
        smoke.check(f"api instance {host} readiness", partial(smoke.ready, base))
    if worker_metrics is not None:
        metrics_url = _origin(worker_metrics)
        smoke.check("worker heartbeat", lambda: smoke.worker(metrics_url, worker_max_age))
    return smoke.results


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    smoke = commands.add_parser("smoke", help="Read-only check that a deployment is usable")
    smoke.add_argument(
        "--url", required=True, help="The public origin, e.g. https://lattice.example.com"
    )
    smoke.add_argument(
        "--instance",
        action="append",
        default=[],
        help="An API instance's private base URL (repeatable), e.g. http://api1:8000",
    )
    smoke.add_argument(
        "--worker-metrics", help="The worker's private metrics URL, e.g. http://worker:9465/metrics"
    )
    smoke.add_argument("--worker-max-age", type=float, default=300.0)
    smoke.add_argument("--ca-file", type=Path, help="CA bundle for a private ingress certificate")
    smoke.add_argument(
        "--production", action="store_true", help="Also check the production-only API surface"
    )


def run(args: argparse.Namespace) -> int:
    verify: bool | str = True
    if args.ca_file is not None:
        try:
            args.ca_file.read_bytes()
        except OSError:
            print("error: --ca-file is not a readable file")
            return 2
        verify = str(args.ca_file)
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, verify=verify, follow_redirects=False) as client:
            results = run_smoke(
                client,
                args.url,
                instances=args.instance,
                worker_metrics=args.worker_metrics,
                worker_max_age=args.worker_max_age,
                production=args.production,
            )
    except ValueError as exc:
        print(f"error: URL {exc}")
        return 2
    for result in results:
        print(f"{'PASS' if result.ok else 'FAIL'}  {result.name}: {result.detail}")
    failed = sum(not r.ok for r in results)
    print(f"{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0
