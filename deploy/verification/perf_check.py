"""Performance sanity through the ingress (P11.7). LOCAL ONLY -- not a benchmark.

Run by ``make prod-perf`` on the reference stack started with ``FAKE_DELAY=0``
(``make prod-up VERIFY=1 IDP=1``): sequential requests through Caddy, round robin over
api1 and api2, signed in with the verification overlay's persona sign-in, Halden's
synthetic data, fake embeddings and the fake model (so an Ask measures retrieval,
authorization, the trace and the pipeline -- never a model). One laptop; the numbers are
a regression signal, not a capacity claim.

    LATTICE_COMPOSE="docker compose …" uv run --project backend python \\
        deploy/verification/perf_check.py
"""

import statistics
import sys
import time
from collections.abc import Callable
from functools import partial

import httpx
from stack import CSRF, QUESTION, ensure_active, sign_in

READS = 200
# The Ask limit is 10 a minute per member: 10 Asks each from four members.
ASKERS = (
    "maya.okafor@halden.example",
    "priya.natarajan@halden.example",
    "arjun.mehta@halden.example",
    "daniel.reyes@halden.example",
)


def summary(name: str, seconds: list[float], statuses: list[int]) -> None:
    ms = sorted(s * 1000 for s in seconds)
    p95 = ms[min(len(ms) - 1, int(0.95 * len(ms)))]
    ok = sum(1 for s in statuses if s == 200)
    print(
        f"{name:<18} n={len(ms):<4} p50 {statistics.median(ms):7.1f} ms  p95 {p95:7.1f} ms  "
        f"max {ms[-1]:7.1f} ms  ({ok}/{len(statuses)} HTTP 200)",
        flush=True,
    )


def timed(call: Callable[[], httpx.Response]) -> tuple[float, int]:
    started = time.perf_counter()
    status = call().status_code
    return time.perf_counter() - started, status


def main() -> int:
    for email in ASKERS:
        ensure_active("halden", email, "P11.7 performance sanity")
    browser = sign_in(ASKERS[0])
    for _ in range(10):  # warm both instances' pools and caches
        browser.get("/api/v1/me")
        browser.get("/api/v1/sources")
    for path in ("/api/v1/me", "/api/v1/sources"):
        runs = [timed(partial(browser.get, path)) for _ in range(READS)]
        summary(path, [t for t, _ in runs], [s for _, s in runs])
    asks: list[tuple[float, int]] = []
    for email in ASKERS:
        asker = sign_in(email)
        for _ in range(10):
            asks.append(
                timed(partial(asker.post, "/api/v1/ask", json={"question": QUESTION}, headers=CSRF))
            )
    summary("/api/v1/ask", [t for t, _ in asks], [s for _, s in asks])
    return 0 if all(s == 200 for _, s in asks) else 1


if __name__ == "__main__":
    sys.exit(main())
