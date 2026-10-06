"""Shared limits, retention and worker drills against the reference stack (P11.4).

LOCAL VERIFICATION ONLY. Run by ``make prod-limits`` after
``make prod-up VERIFY=1 IDP=1 FAKE_DELAY=20`` and ``make prod-seed VERIFY=1 IDP=1``: the
verification overlay (development persona sign-in, fake providers, a fake model that
waits FAKE_DELAY seconds, a 30 s worker lease) plus the identity overlay, whose only use
here is publishing each API instance's metrics on 127.0.0.1 so a step's instance is
observed. Through Caddy, round robin over api1 and api2:

* the in-flight Ask limit (2) holds across instances: three concurrent Asks of one member
  give two answers and one 429 -- refused by an instance holding at most one of them;
* the pre-authentication limit (30/min) holds across instances: exactly 30 sign-ins are
  accepted in a fresh window, then both instances refuse;
* retention ran in the worker: every task reports a last success;
* a worker killed mid-job: the lease expires, the job is retried and succeeds, and the
  interrupted run is recorded ``aborted``; a worker stopped with SIGTERM finishes its job.

    LATTICE_COMPOSE="docker compose …" uv run --project backend python \\
        deploy/verification/limits_check.py
"""

# ruff: noqa: S101, S603, S608 - a verification drill: asserts, the operator's own commands,
# and read-only queries over IDs the API just returned
import concurrent.futures
import os
import shlex
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

ROOT = Path(__file__).resolve().parents[2]
_WORDS = shlex.split(os.environ["LATTICE_COMPOSE"])
COMPOSE_ENV = dict(w.split("=", 1) for w in _WORDS if "=" in w and not w.startswith("-"))
COMPOSE = [w for w in _WORDS if not ("=" in w and not w.startswith("-"))]
BASE = "https://localhost:8443"
METRICS = {"api1": "http://127.0.0.1:19464/metrics", "api2": "http://127.0.0.1:19465/metrics"}
CSRF = {"X-Lattice-Request": "1"}
MEMBER = "maya.okafor@halden.example"
ADMIN = "lena.brandt@halden.example"
RESULTS: list[tuple[str, bool, str]] = []


def compose(*args: str, check: bool = True) -> str:
    done = subprocess.run(
        [*COMPOSE, *args],
        cwd=ROOT,
        env={**os.environ, **COMPOSE_ENV},
        capture_output=True,
        text=True,
        check=False,
    )
    if check and done.returncode != 0:
        raise RuntimeError(f"compose {args[:3]} failed: {done.stderr[-500:]}")
    return done.stdout


def sql(statement: str) -> str:
    """A read-only query as the superuser inside the database container (drill only)."""
    return compose(
        "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "lattice", "-Atc", statement
    ).strip()


def tls() -> ssl.SSLContext:
    ca = ROOT / "deploy/.reference-ca.crt"
    compose("cp", "caddy:/data/caddy/pki/authorities/local/root.crt", str(ca))
    return ssl.create_default_context(cafile=str(ca))


TLS = tls()


def case(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}", flush=True)


def counts() -> dict[tuple[str, str, str], float]:
    found: dict[tuple[str, str, str], float] = {}
    for instance, url in METRICS.items():
        for family in text_string_to_metric_families(httpx.get(url, timeout=5).text):
            for sample in family.samples:
                if sample.name == "lattice_http_requests_total":
                    key = (instance, sample.labels["route"], sample.labels["status"])
                    found[key] = found.get(key, 0) + sample.value
    return found


def delta(
    before: dict[Any, float], after: dict[Any, float], route: str
) -> dict[str, dict[str, int]]:
    """{instance: {status: requests}} for ``route`` between two readings."""
    out: dict[str, dict[str, int]] = {}
    for (instance, r, status), value in after.items():
        grew = int(value - before.get((instance, r, status), 0))
        if r == route and grew:
            out.setdefault(instance, {})[status] = grew
    return out


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, verify=TLS, timeout=120, follow_redirects=False)


def sign_in(email: str) -> httpx.Client:
    browser = client()
    response = browser.post("/api/v1/auth/dev-login", json={"subject": email}, headers=CSRF)
    if response.status_code == 429:  # an earlier run's burst owns this window
        time.sleep(int(response.headers["retry-after"]) + 1)
        response = browser.post("/api/v1/auth/dev-login", json={"subject": email}, headers=CSRF)
    assert response.status_code == 200, response.status_code
    return browser


# --- cases --------------------------------------------------------------------------------


def retention() -> None:
    text = compose(
        "exec",
        "-T",
        "worker",
        "python",
        "-c",
        "import urllib.request; print(urllib.request.urlopen("
        "'http://127.0.0.1:9465/metrics', timeout=5).read().decode())",
    )
    tasks = set()
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == "lattice_maintenance_last_success_timestamp_seconds":
                tasks.add(sample.labels["task"])
    expected = {
        "jobs",
        "query_traces",
        "source_sync_runs",
        "sessions",
        "oidc_login_attempts",
        "rate_limit_counters",
        "pre_auth_rate_limits",
        "ask_slots",
    }
    case(
        "retention: every task succeeded in the worker", tasks == expected, ", ".join(sorted(tasks))
    )
    floors = sql("SELECT count(*) FROM pg_policies WHERE policyname = 'worker_retention_floor'")
    case("retention: database floors installed", floors == "7", f"{floors} restrictive policies")


def in_flight() -> None:
    browser = sign_in(MEMBER)
    cookies = dict(browser.cookies)
    before = counts()

    def ask(i: int) -> int:
        time.sleep(i * 0.3)  # in order through the round robin
        with client() as c:
            c.cookies.update(cookies)
            return c.post(
                "/api/v1/ask",
                json={"question": "What does the onboarding checklist say?"},
                headers=CSRF,
            ).status_code

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        statuses = sorted(pool.map(ask, range(3)))
    elapsed = time.monotonic() - started
    spread = delta(before, counts(), "/v1/ask")
    refused_on = [i for i, s in spread.items() if "429" in s]
    case(
        "in-flight Asks: 2 answered, 1 refused across instances",
        statuses == [200, 200, 429],
        f"{statuses} in {elapsed:.0f} s; per instance {spread}",
    )
    case(
        "in-flight Asks: the refusing instance held at most one Ask itself",
        len(refused_on) == 1 and spread[refused_on[0]].get("200", 0) <= 1,
        str(refused_on),
    )
    live = sql("SELECT count(*) FROM ask_slots WHERE expires_at > now()")
    case("in-flight Asks: no slot left behind", live == "0", f"{live} live slots")


def wait_for(predicate: Any, timeout: float, step: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def hold_source(source_id: str, seconds: int) -> subprocess.Popen[str]:
    """Hold the source's row lock (as the platform superuser, drill only): a sync blocks
    on it at its last statement, so a job is provably mid-flight while it is held."""
    statement = (
        f"BEGIN; SELECT 1 FROM sources WHERE id = '{source_id}' FOR NO KEY UPDATE; "
        f"SELECT pg_sleep({seconds}); COMMIT;"
    )
    return subprocess.Popen(
        [
            *COMPOSE,
            "exec",
            "-T",
            "-e",
            "PGAPPNAME=lattice-drill-lock",
            "postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            "lattice",
            "-q",
            "-c",
            statement,
        ],
        cwd=ROOT,
        env={**os.environ, **COMPOSE_ENV},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def release_source(holder: subprocess.Popen[str]) -> None:
    sql(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE application_name = 'lattice-drill-lock'"
    )
    holder.wait(timeout=30)


def worker_blocked() -> bool:
    return (
        sql(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE 'lattice-worker%' "
            "AND wait_event_type = 'Lock'"
        )
        != "0"
    )


def worker_drills() -> None:
    admin = sign_in(ADMIN)
    sources = admin.get("/api/v1/admin/sources").json()["sources"]
    support = next(s for s in sources if s["key"] == "support")

    def request_sync() -> str:
        response = admin.post(f"/api/v1/admin/sources/{support['id']}/sync", headers=CSRF)
        assert response.status_code in (200, 202), response.status_code
        job_id: str = response.json()["id"]
        return job_id

    def status(job: str) -> str:
        return sql(f"SELECT status || ':' || attempts FROM jobs WHERE id = '{job}'")

    def runs() -> str:
        return sql(
            "SELECT string_agg(status, ',' ORDER BY started_at DESC) FROM (SELECT status, "
            f"started_at FROM source_sync_runs WHERE source_id = '{support['id']}' "
            "ORDER BY started_at DESC LIMIT 2) r"
        )

    # Killed mid-job: the lease (30 s in this overlay) expires and the job is retried.
    holder = hold_source(support["id"], 120)
    job = request_sync()
    caught = wait_for(lambda: status(job).startswith("running") and worker_blocked(), 30, 0.5)
    compose("kill", "worker")
    killed_at = time.monotonic()
    mid = status(job)
    release_source(holder)
    compose("up", "-d", "--no-deps", "--wait", "worker")
    done = wait_for(lambda: status(job).startswith(("succeeded", "failed")), 150, 1)
    final = status(job)
    case(
        "worker killed mid-job: retried after the lease and succeeded",
        caught and mid == "running:1" and done and final == "succeeded:2",
        f"{mid} at the kill, {final} {time.monotonic() - killed_at:.0f} s later",
    )
    case(
        "worker killed mid-job: the interrupted run is recorded aborted",
        # The support export carries deliberately rejected records: its runs are "partial".
        runs() in ("succeeded,aborted", "partial,aborted"),
        runs(),
    )

    # SIGTERM mid-job: the worker finishes the job, then exits.
    holder = hold_source(support["id"], 120)
    job = request_sync()
    caught = wait_for(lambda: status(job).startswith("running") and worker_blocked(), 30, 0.5)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        stopping = pool.submit(compose, "stop", "worker")
        time.sleep(3)  # SIGTERM delivered; the job is still blocked
        still = status(job)
        release_source(holder)
        stopping.result(timeout=150)
    final = status(job)
    compose("up", "-d", "--no-deps", "--wait", "worker")
    case(
        "worker stopped with SIGTERM mid-job: it finished the job first",
        caught and still == "running:1" and final == "succeeded:1",
        f"{still} -> {final}",
    )


def generation() -> None:
    """The fake model (synthetic data only) as Halden's generation provider, once."""
    listed = compose("run", "--rm", "--no-deps", "-T", "ops", "provider", "list", "--org", "halden")
    if "fake" not in listed:
        compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "ops",
            "provider",
            "configure",
            "--org",
            "halden",
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
            "P11.4 verification (synthetic data, fake model)",
        )


def pre_auth() -> None:
    # A fresh one-minute window (windows are aligned to the minute).
    time.sleep(61 - time.time() % 60)
    before = counts()
    accepted = refused = 0
    with client() as c:
        for _ in range(40):
            response = c.post(
                "/api/v1/auth/dev-login", json={"subject": "nobody@halden.example"}, headers=CSRF
            )
            if response.status_code == 429:
                refused += 1
            else:
                accepted += 1
    spread = delta(before, counts(), "/v1/auth/dev-login")
    case(
        "pre-auth limit shared: exactly 30 accepted across instances",
        (accepted, refused) == (30, 10),
        f"{accepted} accepted, {refused} refused; {spread}",
    )
    case(
        "pre-auth limit: both instances refused",
        all("429" in spread.get(i, {}) for i in METRICS),
        "",
    )


def main() -> int:
    t0 = time.monotonic()
    retention()
    generation()
    in_flight()
    worker_drills()
    pre_auth()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed in {time.monotonic() - t0:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
