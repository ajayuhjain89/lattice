"""Shared plumbing for the P11.7 stack drivers (authz_check.py, drills_check.py).

LOCAL VERIFICATION ONLY. The reference stack runs with the verification overlay
(VERIFY=1: development persona sign-in, fake providers) and the identity overlay (IDP=1),
whose only use here is publishing each API instance's metrics on 127.0.0.1 so the
instance that served a request is observed. Requests go through Caddy, round robin over
api1 and api2. Every case prints PASS/FAIL; ``finish`` returns the exit status.
"""

# ruff: noqa: S101, S603, S608 - verification drivers: asserts, the operator's own commands,
# and read-only queries over IDs the API just returned
import os
import shlex
import ssl
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

ROOT = Path(__file__).resolve().parents[2]
# The Makefile's compose command line; leading NAME=value words are its environment.
_WORDS = shlex.split(os.environ["LATTICE_COMPOSE"])
COMPOSE_ENV = dict(w.split("=", 1) for w in _WORDS if "=" in w and not w.startswith("-"))
COMPOSE = [w for w in _WORDS if not ("=" in w and not w.startswith("-"))]
BASE = "https://localhost:8443"
METRICS = {"api1": "http://127.0.0.1:19464/metrics", "api2": "http://127.0.0.1:19465/metrics"}
CSRF = {"X-Lattice-Request": "1"}
QUESTION = "What does the onboarding checklist say?"
RESULTS: list[tuple[str, bool, str]] = []


def env_file() -> dict[str, str]:
    values = {}
    for line in (ROOT / "deploy/.env.reference").read_text().splitlines():
        name, _, value = line.partition("=")
        if name and not name.startswith("#"):
            values[name.strip()] = value.strip()
    return values


def compose(*args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    done = subprocess.run(
        [*COMPOSE, *args],
        cwd=ROOT,
        env={**os.environ, **COMPOSE_ENV, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )
    if check and done.returncode != 0:
        raise RuntimeError(f"compose {args[:4]} failed ({done.returncode}): {done.stderr[-600:]}")
    return done.stdout


def ops(*args: str) -> str:
    """An operator command (``lattice …``) as lattice_owner, in the verification overlay."""
    return compose("run", "--rm", "--no-deps", "-T", "ops", *args)


def sql(statement: str) -> str:
    """A query as the superuser inside the database container (drill only)."""
    return compose(
        "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "lattice", "-Atc", statement
    ).strip()


def _tls() -> ssl.SSLContext:
    ca = ROOT / "deploy/.reference-ca.crt"
    compose("cp", "caddy:/data/caddy/pki/authorities/local/root.crt", str(ca))
    return ssl.create_default_context(cafile=str(ca))


TLS = _tls()


def case(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}", flush=True)
    return ok


def finish(started: float) -> int:
    failed = [name for name, ok, _ in RESULTS if not ok]
    took = time.monotonic() - started
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed in {took:.0f} s")
    return 1 if failed else 0


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, verify=TLS, timeout=200, follow_redirects=False)


def sign_in(email: str) -> httpx.Client:
    browser = client()
    response = browser.post("/api/v1/auth/dev-login", json={"subject": email}, headers=CSRF)
    if response.status_code == 429:  # an earlier burst owns this pre-auth window
        time.sleep(int(response.headers["retry-after"]) + 1)
        response = browser.post("/api/v1/auth/dev-login", json={"subject": email}, headers=CSRF)
    assert response.status_code == 200, (email, response.status_code)
    return browser


def counts(
    names: tuple[str, ...] = ("lattice_http_requests_total",),
) -> dict[tuple[str, ...], float]:
    """(instance, metric, route, status) -> value, from each reachable instance's metrics."""
    found: dict[tuple[str, ...], float] = {}
    for instance, url in METRICS.items():
        try:
            text = httpx.get(url, timeout=5).text
        except httpx.HTTPError:
            continue  # a stopped instance
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                if sample.name in names:
                    key = (
                        instance,
                        sample.name,
                        sample.labels.get("route", sample.labels.get("dependency", "")),
                        sample.labels.get("status", ""),
                    )
                    found[key] = found.get(key, 0) + sample.value
    return found


def served(action: Callable[[], Any], route: str) -> tuple[Any, set[str]]:
    """Run ``action``; the instances whose request counter for ``route`` grew."""
    before = counts()
    result = action()
    after = counts()
    return result, {
        key[0] for key, value in after.items() if key[2] == route and value > before.get(key, 0)
    }


def wait_for(predicate: Callable[[], bool], timeout: float, step: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def org_id(slug: str) -> str:
    return sql(f"SELECT id FROM organizations WHERE slug = '{slug}'")


def member_status(slug: str, email: str) -> str:
    return sql(
        "SELECT m.status FROM memberships m JOIN users u ON u.id = m.user_id "
        f"WHERE m.organization_id = '{org_id(slug)}' AND u.email = '{email}'"
    )


def ensure_active(slug: str, email: str, why: str) -> None:
    """Earlier drills leave members suspended on purpose (P11.5): reinstate, audited."""
    if member_status(slug, email) != "active":
        ops("member", "reinstate", "--org", slug, "--email", email, "--reason", why)


def fake_generation(slug: str, why: str) -> None:
    """The fake model (synthetic data only) as the organization's generation provider."""
    ops(
        "provider",
        "configure",
        "--org",
        slug,
        "--purpose",
        "generation",
        "--provider",
        "fake",
        "--model",
        "fake",
        "--deployment",
        "external_api",
        "--may-use-content",
        "--reason",
        why,
    )


def ready() -> None:
    """Both API instances healthy again (after a drill stopped one)."""
    compose("up", "-d", "--no-deps", "--wait", "api1", "api2")
