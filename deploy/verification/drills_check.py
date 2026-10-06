"""Failure drills of plan §18 against the reference stack (P11.7).

LOCAL VERIFICATION ONLY. Run by ``make prod-drills`` after
``make prod-up VERIFY=1 IDP=1 FAKE_DELAY=20`` and ``make prod-seed VERIFY=1 IDP=1``
(the fake model waits 20 s, so an Ask is provably in flight). Synthetic Halden data, the
fake model, no real provider: the provider-outage drill points Halden's generation at a
loopback port where nothing listens.

* an API instance killed (SIGKILL) during an Ask: the Ask on the other instance completes,
  the killed one fails at once at the ingress (no hang), other traffic keeps being served;
  the dead process's slot is held until its lease expires, then free;
* an API instance stopped (SIGTERM) during an Ask: graceful shutdown completes it;
* the database stopped and started: 503 + ``Retry-After`` from the API, readiness fails,
  liveness passes; recovery without restarting the API; the worker resumes;
* the generation provider unreachable: ``failed``/``provider_unavailable`` with evidence
  within the deadline; reconfigured to the fake model, answers again.

    LATTICE_COMPOSE="docker compose …" uv run --project backend python \\
        deploy/verification/drills_check.py
"""

# ruff: noqa: S603, S607, S608 - a verification driver: the operator's own docker commands,
# read-only queries over IDs the API just returned
import concurrent.futures
import json
import subprocess
import sys
import time
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families
from stack import (
    CSRF,
    METRICS,
    QUESTION,
    case,
    client,
    compose,
    counts,
    ensure_active,
    fake_generation,
    finish,
    ops,
    org_id,
    sign_in,
    sql,
    wait_for,
)

WHY = "P11.7 failure drills (synthetic data)"
ADMIN = "lena.brandt@halden.example"
KILLED = ("maya.okafor@halden.example", "priya.natarajan@halden.example")
STOPPED = ("arjun.mehta@halden.example", "daniel.reyes@halden.example")
OUTAGE = "grace.whitfield@halden.example"
# Ollama's port on the API instance's own loopback: nothing listens there (no model server
# runs on the stack), so every call is refused. A `local` record needs no approval and no
# change to the organization's egress ceilings.
NOWHERE = "http://127.0.0.1:11434"


def in_flight() -> dict[str, float]:
    values = {}
    for instance, url in METRICS.items():
        try:
            text = httpx.get(url, timeout=3).text
        except httpx.HTTPError:
            continue
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                if sample.name == "lattice_http_requests_in_flight":
                    values[instance] = sample.value
    return values


def ask(email: str, browser: httpx.Client | None = None) -> tuple[int, str, float]:
    """(HTTP status, answer status or error code, seconds)."""
    started = time.monotonic()
    browser = browser or sign_in(email)
    response = browser.post("/api/v1/ask", json={"question": QUESTION}, headers=CSRF)
    try:
        body = response.json()
        outcome = body.get("status") or body.get("error", {}).get("code", "")
    except ValueError:
        outcome = response.text[:40]
    return response.status_code, outcome, time.monotonic() - started


def concurrently(emails: tuple[str, ...], during: Any) -> tuple[list[tuple[int, str, float]], Any]:
    """Sign each member in, start their Asks 0.4 s apart (one per instance through the
    round robin), run ``during()`` while they are in flight; the Asks' results."""
    browsers = [sign_in(e) for e in emails]
    with concurrent.futures.ThreadPoolExecutor(len(emails)) as pool:
        futures = []
        for email, browser in zip(emails, browsers, strict=True):
            futures.append(pool.submit(ask, email, browser))
            time.sleep(0.4)
        placed = wait_for(lambda: sum(in_flight().values()) >= len(emails), 5, 0.2)
        spread = in_flight()
        side = during(placed, spread)
        return [f.result() for f in futures], side


def container(service: str) -> dict[str, Any]:
    ident = compose("ps", "-q", service).strip()
    inspected = subprocess.run(
        ["docker", "inspect", ident], capture_output=True, text=True, check=True
    ).stdout
    state = json.loads(inspected)[0]["State"]
    return {"started": state["StartedAt"], "restarts": state.get("RestartCount", 0)}


def live_slots(emails: tuple[str, ...]) -> str:
    halden = org_id("halden")
    listed = ",".join(f"'{e}'" for e in emails)
    return sql(
        "SELECT count(*) FROM ask_slots s JOIN memberships m ON m.id = s.membership_id "
        f"JOIN users u ON u.id = m.user_id WHERE s.organization_id = '{halden}' "
        f"AND u.email IN ({listed}) AND s.expires_at > now()"
    )


# --- drills -------------------------------------------------------------------------------


def kill_during_ask() -> str:
    """SIGKILL api1 with one Ask on each instance. Returns the member whose Ask died."""

    def kill(placed: bool, spread: dict[str, float]) -> dict[str, Any]:
        time.sleep(3)
        compose("kill", "api1")
        killed_at = time.monotonic()
        meanwhile = []
        with client() as c:
            for _ in range(10):
                meanwhile.append(c.get("/api/v1/health/ready").status_code)
        return {"spread": spread, "placed": placed, "meanwhile": meanwhile, "at": killed_at}

    results, side = concurrently(KILLED, kill)
    statuses = sorted(r[0] for r in results)
    died = [KILLED[i] for i, r in enumerate(results) if r[0] != 200]
    case(
        "API killed during an Ask: one Ask on each instance when the kill came",
        side["spread"] == {"api1": 1.0, "api2": 1.0},
        f"in flight {side['spread']}",
    )
    case(
        "... the other instance's Ask completed; the killed one failed at the ingress",
        statuses[0] == 200 and statuses[1] >= 500 and len(died) == 1,
        "; ".join(
            f"{e.split('@')[0]}: HTTP {s} {o} in {t:.1f} s"
            for e, (s, o, t) in zip(KILLED, results, strict=True)
        ),
    )
    case(
        "... traffic kept being served while api1 was down",
        all(s == 200 for s in side["meanwhile"]),
        f"{side['meanwhile'].count(200)}/{len(side['meanwhile'])} readiness through Caddy",
    )
    compose("up", "-d", "--no-deps", "--wait", "api1")
    held = live_slots(tuple(died))
    case(
        "... the dead process's slot is still held (until its lease expires)",
        held == "1",
        f"{held} live slot of {died[0].split('@')[0] if died else '?'}",
    )
    if died:
        browser = sign_in(died[0])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            pair = sorted(pool.map(lambda _: ask(died[0], browser)[0], range(2)))
        case(
            "... meanwhile the member has one of two slots: [200, 429]",
            pair == [200, 429],
            str(pair),
        )
    return died[0] if died else ""


def sigterm_during_ask() -> None:
    def stop(placed: bool, spread: dict[str, float]) -> dict[str, Any]:
        time.sleep(3)
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            started = time.monotonic()
            stopping = pool.submit(compose, "stop", "api1")
            time.sleep(1)
            meanwhile = []
            with client() as c:
                while not stopping.done():
                    meanwhile.append(c.get("/api/v1/health/ready").status_code)
                    time.sleep(0.5)
            stopping.result()
            return {"spread": spread, "meanwhile": meanwhile, "took": time.monotonic() - started}

    results, side = concurrently(STOPPED, stop)
    case(
        "API stopped (SIGTERM) during an Ask: one Ask on each instance",
        side["spread"] == {"api1": 1.0, "api2": 1.0},
        f"in flight {side['spread']}",
    )
    case(
        "... graceful shutdown completed both Asks",
        all(r[0] == 200 and r[1] == "answered" for r in results),
        "; ".join(f"HTTP {s} {o} in {t:.1f} s" for s, o, t in results)
        + f"; the stop took {side['took']:.0f} s",
    )
    failed = [s for s in side["meanwhile"] if s != 200]
    case(
        "... no request failed at the ingress while api1 drained",
        not failed,
        f"{len(side['meanwhile']) - len(failed)}/{len(side['meanwhile'])} readiness 200",
    )
    compose("up", "-d", "--no-deps", "--wait", "api1")
    case("... no slot left behind", live_slots(STOPPED) == "0")


DIRECT = """
import json, sys, urllib.request, urllib.error
out = {}
for path in ("/v1/health/live", "/v1/health/ready", "/v1/me"):
    request = urllib.request.Request("http://127.0.0.1:8000" + path,
        headers={"Cookie": "__Host-lattice_session=" + sys.argv[1]})
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            out[path] = [r.status, None, ""]
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b"{}")
        out[path] = [e.code, e.headers.get("Retry-After"), body.get("error", {}).get("code", "")]
print(json.dumps(out))
"""


def direct(service: str, token: str) -> dict[str, list[Any]]:
    """Live, ready and /v1/me straight at one instance, from inside its container."""
    found: dict[str, list[Any]] = json.loads(
        compose("exec", "-T", service, "python", "-c", DIRECT, token)
    )
    return found


def database_outage() -> None:
    browser = sign_in(OUTAGE)
    token = browser.cookies["__Host-lattice_session"]
    before = {s: container(s) for s in ("api1", "api2", "worker")}
    failures = sum(
        v for k, v in counts(("lattice_dependency_failures_total",)).items() if k[2] == "database"
    )
    compose("stop", "postgres")
    stopped_at = time.monotonic()
    edge_first = browser.get("/api/v1/me")
    seen = {s: direct(s, token) for s in ("api1", "api2")}
    time.sleep(12)  # past the ingress's health interval
    edge_later = browser.get("/api/v1/me")
    outage = time.monotonic() - stopped_at
    compose("start", "postgres")
    compose("up", "-d", "--no-deps", "--wait", "postgres")
    back_at = time.monotonic()
    recovered = wait_for(lambda: browser.get("/api/v1/me").status_code == 200, 60, 0.5)
    recovery = time.monotonic() - back_at
    after = {s: container(s) for s in ("api1", "api2", "worker")}

    ok_direct = all(
        v["/v1/health/live"][0] == 200
        and v["/v1/health/ready"][0] == 503
        and v["/v1/me"][0] == 503
        and v["/v1/me"][1] is not None
        and v["/v1/me"][2] == "service_unavailable"
        for v in seen.values()
    )
    case(
        "database down: each instance answers 503 service_unavailable + Retry-After; "
        "readiness 503, liveness 200",
        ok_direct,
        json.dumps(seen),
    )
    case(
        "database down: through the ingress the client gets 503",
        edge_first.status_code == 503 and edge_later.status_code == 503,
        f"at once {edge_first.status_code} Retry-After {edge_first.headers.get('retry-after')}; "
        f"after 12 s {edge_later.status_code} Retry-After "
        f"{edge_later.headers.get('retry-after')} {edge_later.text[:80]!r}",
    )
    case(
        "database back: the same session works again without restarting the API",
        recovered and all(before[s] == after[s] for s in ("api1", "api2")),
        f"outage {outage:.0f} s, served again {recovery:.1f} s after PostgreSQL was healthy; "
        f"api restarts {[after[s]['restarts'] for s in ('api1', 'api2')]}",
    )
    grew = (
        sum(
            v
            for k, v in counts(("lattice_dependency_failures_total",)).items()
            if k[2] == "database"
        )
        - failures
    )
    case(
        "... the database-unavailable alert's input grew",
        grew > 0,
        f"lattice_dependency_failures_total{{dependency=database}} +{grew:.0f}",
    )
    lena = sign_in(ADMIN)
    drive = next(
        s for s in lena.get("/api/v1/admin/sources").json()["sources"] if s["key"] == "drive"
    )
    job = lena.post(f"/api/v1/admin/sources/{drive['id']}/sync", headers=CSRF).json()["id"]
    done = wait_for(
        lambda: sql(f"SELECT status FROM jobs WHERE id = '{job}'") in ("succeeded", "failed"),
        120,
        1,
    )
    case(
        "... the worker resumed: a requested sync succeeded",
        done and sql(f"SELECT status FROM jobs WHERE id = '{job}'") == "succeeded",
        f"worker restarts {before['worker']['restarts']} -> {after['worker']['restarts']}, "
        f"started {'unchanged' if before['worker'] == after['worker'] else 'again'}",
    )


def provider_outage() -> None:
    before = counts(("lattice_generation_calls_total",))
    ops(
        "provider",
        "configure",
        "--org",
        "halden",
        "--purpose",
        "generation",
        "--provider",
        "ollama",
        "--model",
        "drill-nothing-listening",
        "--deployment",
        "local",
        "--endpoint",
        NOWHERE,
        "--reason",
        f"{WHY}: provider outage",
    )
    browser = sign_in(KILLED[0])
    started = time.monotonic()
    response = browser.post("/api/v1/ask", json={"question": QUESTION}, headers=CSRF)
    took = time.monotonic() - started
    body = response.json()
    fake_generation("halden", f"{WHY}: provider restored")
    case(
        "provider unreachable: failed / provider_unavailable, with evidence, within the deadline",
        response.status_code == 200
        and body.get("status") == "failed"
        and body.get("status_reason") == "provider_unavailable"
        and len(body.get("evidence", [])) > 0
        and took < 150,
        f"HTTP {response.status_code} {body.get('status')}/{body.get('status_reason')}, "
        f"{len(body.get('evidence', []))} evidence, {took:.1f} s",
    )
    after = counts(("lattice_generation_calls_total",))
    case(
        "... the provider-surge alert's input grew",
        sum(after.values()) > sum(before.values()),
        f"generation calls +{sum(after.values()) - sum(before.values()):.0f}",
    )
    status, outcome, took = ask(KILLED[0], browser)
    case(
        "... reconfigured to the fake model: answered again",
        (status, outcome) == (200, "answered"),
        f"{status} {outcome} in {took:.1f} s",
    )


def slot_expiry(member: str) -> None:
    if not member:
        case("dead process's slot freed at lease expiry", False, "no Ask died")
        return
    halden = org_id("halden")
    remaining = sql(
        "SELECT ceil(extract(epoch FROM max(expires_at) - now())) FROM ask_slots s "
        "JOIN memberships m ON m.id = s.membership_id JOIN users u ON u.id = m.user_id "
        f"WHERE s.organization_id = '{halden}' AND u.email = '{member}'"
    )
    freed = wait_for(lambda: live_slots((member,)) == "0", 240, 2)
    browser = sign_in(member)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        pair = sorted(pool.map(lambda _: ask(member, browser)[0], range(2)))
    case(
        "dead process's slot freed at lease expiry: both slots usable again",
        freed and pair == [200, 200],
        f"lease expired {remaining} s after the last check; then {pair}",
    )


def main() -> int:
    started = time.monotonic()
    for email in (*KILLED, *STOPPED, OUTAGE):
        ensure_active("halden", email, f"{WHY}: undo an earlier drill's suspension")
    fake_generation("halden", f"{WHY}: start from the fake model")
    died = kill_during_ask()
    sigterm_during_ask()
    database_outage()
    provider_outage()
    slot_expiry(died)
    return finish(started)


if __name__ == "__main__":
    sys.exit(main())
