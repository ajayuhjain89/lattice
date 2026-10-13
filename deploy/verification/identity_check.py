# ruff: noqa: S101, S603 - a verification drill: asserts and the operator's own commands
"""Identity verification against the reference stack (Phase 11, P11.3; plan §15.3).

LOCAL VERIFICATION ONLY. Run by ``make prod-identity`` after ``make prod-up IDP=1``:
the stack in production mode (LATTICE_ENV=production: OIDC only, ``__Host-`` cookies,
https-only identity provider), two API instances behind Caddy, and the verification
identity provider (deploy/verification/idp.py). This script is the browser and the
operator:

* the browser (httpx, one cookie jar per person) talks to https://localhost:8443 (Caddy,
  its internal CA) and to the provider's authorization endpoint on https://localhost:9443;
* the operator runs the audited commands in the stack's ``ops`` container
  (``docker compose run ops lattice …``), rolls the API instances for a secret rotation
  and stops the provider for the outage case;
* each API instance's internal metrics (published on 127.0.0.1 by the overlay only) tell
  which instance served a step, so "started on one instance, finished on the other" and
  "refused by both instances" are observed, not assumed.

Personas are provisioned through SCIM over the ingress, under names unique to the run.
Every case prints PASS/FAIL; the exit status is 1 if any failed. No secret is printed.

    LATTICE_COMPOSE="docker compose --env-file … -f … -f …" \
      uv run --project backend python deploy/verification/identity_check.py
"""

import json
import os
import re
import secrets
import shlex
import ssl
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
IDP = "https://localhost:9443"
METRICS = {"api1": "http://127.0.0.1:19464/metrics", "api2": "http://127.0.0.1:19465/metrics"}
ORG = "halden"
CLIENT_ID = "lattice-verify"
CSRF = {"X-Lattice-Request": "1"}
SCIM = f"{BASE}/api/scim/v2"
FAILED = "/sign-in?error=sso_failed"
UNAVAILABLE = "/sign-in?error=sso_unavailable"
RUN = time.strftime("%m%d%H%M%S")

START_ROUTE = "/v1/auth/oidc/{login_key}/start"
CALLBACK_ROUTE = "/v1/auth/oidc/{login_key}/callback"


def _env_file() -> dict[str, str]:
    values = {}
    for line in (ROOT / "deploy/.env.reference").read_text().splitlines():
        name, _, value = line.partition("=")
        if name and not name.startswith("#"):
            values[name.strip()] = value.strip()
    return values


ENV = _env_file()
SECRET = ENV["VERIFY_OIDC_CLIENT_SECRET"]
SECRET_NEXT = ENV["VERIFY_OIDC_CLIENT_SECRET_NEXT"]
ADMIN = {"Authorization": f"Bearer {ENV['VERIFY_IDP_ADMIN_TOKEN']}"}


# --- infrastructure ----------------------------------------------------------------------


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


def ops(*args: str, test_env: bool = False) -> str:
    extra = ["-e", "LATTICE_ENV=test"] if test_env else []
    return compose("run", "--rm", "--no-deps", "-T", *extra, "ops", *args)


def tls() -> ssl.SSLContext:
    ca = ROOT / "deploy/.reference-ca.crt"
    compose("cp", "caddy:/data/caddy/pki/authorities/local/root.crt", str(ca))
    context = ssl.create_default_context(cafile=str(ca))
    context.load_verify_locations(cafile=str(ROOT / "deploy/.verification/ca.crt"))
    return context


TLS = tls()


def idp_state(clients: dict[str, list[str] | None], offsets: dict[str, int] | None = None) -> None:
    body: dict[str, Any] = {"clients": clients, "offsets": offsets or {}}
    response = httpx.put(f"{IDP}/admin/state", json=body, headers=ADMIN, verify=TLS, timeout=10)
    response.raise_for_status()


def counts(name: str = "lattice_http_requests_total") -> dict[tuple[str, str, str], float]:
    """(instance, route, status) -> requests, from each instance's own metrics."""
    found: dict[tuple[str, str, str], float] = {}
    for instance, url in METRICS.items():
        text = httpx.get(url, timeout=5).text
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                if sample.name == name:
                    key = (
                        instance,
                        sample.labels.get("route", ""),
                        sample.labels.get("status", ""),
                    )
                    found[key] = found.get(key, 0) + sample.value
    return found


def served(
    action: Callable[[], Any], route: str, status: str | None = None
) -> tuple[Any, set[str]]:
    """Run ``action``; the instances whose counter for ``route`` (and ``status``) grew."""
    before = counts()
    result = action()
    after = counts()
    grew = {
        instance
        for (instance, r, s), value in after.items()
        if r == route
        and (status is None or s == status)
        and value > before.get((instance, r, s), 0)
    }
    return result, grew


def gauge(name: str) -> dict[str, float]:
    values = {}
    for instance, url in METRICS.items():
        for family in text_string_to_metric_families(httpx.get(url, timeout=5).text):
            for sample in family.samples:
                if sample.name == name:
                    values[instance] = sample.value
    return values


# --- the browser -------------------------------------------------------------------------


@dataclass
class Browser:
    client: httpx.Client = field(
        default_factory=lambda: httpx.Client(verify=TLS, timeout=30, follow_redirects=False)
    )

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.get(f"{BASE}{path}", **kwargs)

    @property
    def session(self) -> str | None:
        return self.client.cookies.get("__Host-lattice_session")

    def use(self, token: str) -> None:
        self.client.cookies.clear()
        self.client.cookies.set("__Host-lattice_session", token, domain="localhost.local")

    def authorize(self, key: str, persona: str) -> tuple[str, str]:
        """Start sign-in and let the provider approve: (callback URL path, start instance)."""
        start, where = served(lambda: self.get(f"/api/v1/auth/oidc/{key}/start"), START_ROUTE)
        assert start.status_code == 303, start.status_code
        location = start.headers["location"]
        if location.startswith("/sign-in"):
            return location, ",".join(sorted(where))
        approve = self.client.get(f"{location}&persona={persona}")
        assert approve.status_code == 302, approve.status_code
        callback = approve.headers["location"]
        assert callback.startswith(f"{BASE}/api/"), "callback leaves the public origin"
        return callback.removeprefix(BASE), ",".join(sorted(where))

    def finish(self, callback: str) -> tuple[str, str]:
        if callback.startswith("/sign-in"):
            return callback, ""
        done, where = served(lambda: self.get(callback), CALLBACK_ROUTE)
        assert done.status_code == 303, done.status_code
        return done.headers["location"], ",".join(sorted(where))

    def sign_in(self, key: str, persona: str) -> str:
        for attempt in range(2):
            callback, _ = self.authorize(key, persona)
            location, _ = self.finish(callback)
            if "rate_limited" not in location or attempt:
                return location
            time.sleep(61)  # the pre-authentication limit (per client, per minute)
        return location

    def me_everywhere(self, token: str | None = None) -> dict[str, int]:
        """/v1/me on both instances (round robin through the ingress), with the same
        token each time: {instance: status}."""
        token = token or self.session
        statuses: dict[str, int] = {}
        for _ in range(6):
            if token is not None:
                self.use(token)
            response, where = served(lambda: self.get("/api/v1/me"), "/v1/me")
            for instance in where:
                statuses[instance] = response.status_code
            if len(statuses) == 2:
                break
        if token is not None:
            self.use(token)
        return statuses


def scim(method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"}
    return httpx.request(method, f"{SCIM}{path}", headers=headers, verify=TLS, timeout=30, **kwargs)


def scim_everywhere(token: str) -> dict[str, int]:
    statuses: dict[str, int] = {}
    for _ in range(6):
        response, where = served(lambda: scim("GET", "/Users", token), "/scim/v2/Users")
        for instance in where:
            statuses[instance] = response.status_code
        if len(statuses) == 2:
            break
    return statuses


# --- reporting ---------------------------------------------------------------------------

RESULTS: list[tuple[str, bool, str]] = []


def case(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}", flush=True)


def both(statuses: dict[str, int], code: int) -> bool:
    return set(statuses) == {"api1", "api2"} and set(statuses.values()) == {code}


# --- set-up ------------------------------------------------------------------------------


def connection() -> tuple[str, str]:
    """The organization's active connection to the verification provider, created or
    reset to the canonical client (ID, login key). The provider is a private address on
    the stack's network: since P12.4 sign-in reaches it only as a target the operator
    registered for this organization (idempotent)."""
    ops(
        "outbound",
        "allow",
        "--org",
        ORG,
        "--url",
        "https://idp:9443",
        "--purpose",
        "oidc",
        "--reason",
        "Verification identity provider",
    )
    shown = ops("sso", "show", "--org", ORG)
    if "no active OIDC connection" in shown:
        listing = ops("sso", "list", "--org", ORG)
        mine = [line.split()[0] for line in listing.splitlines() if "https://idp:9443" in line]
        if mine:
            ops("sso", "enable", "--org", ORG, "--connection-id", mine[-1], "--reason", "verify")
        else:
            ops(
                "sso",
                "add",
                "--org",
                ORG,
                "--issuer",
                "https://idp:9443",
                "--client-id",
                CLIENT_ID,
                "--client-secret-ref",
                "env:LATTICE_OIDC_SECRET_VERIFY",
                "--reason",
                "P11.3 identity verification",
            )
        shown = ops("sso", "show", "--org", ORG)
    if f"client ID:       {CLIENT_ID} (confidential)" not in shown:
        ops(
            "sso",
            "update",
            "--org",
            ORG,
            "--client-id",
            CLIENT_ID,
            "--client-secret-ref",
            "env:LATTICE_OIDC_SECRET_VERIFY",
            "--reason",
            "reset for verification",
        )
        shown = ops("sso", "show", "--org", ORG)
    key = re.search(r"/sso/([A-Za-z0-9_-]+)", shown)
    ident = re.search(r"OIDC connection ([0-9a-f-]{36})", shown)
    assert key, shown
    assert ident, shown
    return ident.group(1), key.group(1)


def scim_token() -> tuple[str, str]:
    out = ops("scim", "token", "create", "--org", ORG, "--reason", "P11.3 verification")
    token = next(line for line in out.splitlines() if line.startswith("lsc_"))
    token_id = re.search(r"SCIM token ([0-9a-f-]{36})", out)
    assert token_id
    return token_id.group(1), token


def provision(token: str, name: str) -> tuple[str, str, str]:
    """SCIM-provision a run-unique persona: (subject, e-mail, SCIM resource ID)."""
    subject, email = f"verify-{RUN}-{name}", f"{name}.{RUN}@halden.example"
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "externalId": subject,
        "userName": email,
        "name": {"givenName": name.title(), "familyName": "Verify"},
        "emails": [{"value": email, "type": "work", "primary": True}],
        "active": True,
    }
    response = scim("POST", "/Users", token, content=json.dumps(body))
    assert response.status_code == 201, response.status_code
    return subject, email, response.json()["id"]


def roll_api(secret: str) -> float:
    """A rolling restart of the API instances with a new value for the connection's
    secret reference (the secret store changed): one instance at a time, readiness-gated."""
    started = time.monotonic()
    for instance in ("api1", "api2"):
        compose(
            "up", "-d", "--no-deps", "--wait", instance, env={"VERIFY_OIDC_CLIENT_SECRET": secret}
        )
    return time.monotonic() - started


# --- the cases ---------------------------------------------------------------------------


def main() -> int:
    t0 = time.monotonic()
    ops(
        "seed",
        "--require-synthetic",
        "--manifest",
        "/srv/lattice/data/synthetic/halden/org.yaml",
        test_env=True,
    )
    idp_state({CLIENT_ID: [SECRET]})
    connection_id, key = connection()
    token_id, token = scim_token()
    ana, _, _ = provision(token, "ana")
    ben, _, ben_id = provision(token, "ben")
    _, cy_email, _ = provision(token, "cy")
    cy = f"verify-{RUN}-cy"
    dan, _, dan_id = provision(token, "dan")
    print(f"set-up: connection {connection_id}, 4 personas provisioned via SCIM", flush=True)

    # 1. Sign-in started on one instance, finished on the other.
    browser = Browser()
    callback, start_on = browser.authorize(key, ana)
    location, finish_on = browser.finish(callback)
    case(
        "sign-in: start and callback on different instances",
        location == "/ask" and {start_on, finish_on} == {"api1", "api2"},
        f"start {start_on}, callback {finish_on}",
    )
    case("session valid on both instances", both(browser.me_everywhere(), 200))

    # 2. Replay and binding.
    replay, where = served(lambda: browser.get(callback), CALLBACK_ROUTE)
    case("replayed callback refused", replay.headers.get("location") == FAILED, f"on {where}")
    victim = Browser()
    stolen, _ = victim.authorize(key, ana)
    attacker = Browser()
    attacker.authorize(key, ana)  # a binding cookie of its own
    location, _ = attacker.finish(stolen)
    case("callback from another browser refused", location == FAILED)
    location, _ = Browser().finish(stolen)
    case("callback without the binding cookie refused", location == FAILED)

    # 3. ID-token time boundaries (60 s leeway), early in a second.
    for claim, offset, accepted in (
        ("exp", -59, True),
        ("exp", -61, False),
        ("iat", 59, True),
        ("iat", 61, False),
    ):
        idp_state({CLIENT_ID: [SECRET]}, {claim: offset})
        if time.time() % 1 > 0.5:
            time.sleep(1.05 - time.time() % 1)
        location = Browser().sign_in(key, ana)
        case(
            f"ID token {claim} {offset:+d} s",
            location == ("/ask" if accepted else FAILED),
            location,
        )
    idp_state({CLIENT_ID: [SECRET]})

    # 4. Logout ends the session on both instances.
    out = Browser()
    out.sign_in(key, ana)
    kept = out.session
    assert kept is not None
    logout = out.client.post(f"{BASE}/api/v1/auth/logout", headers=CSRF)
    case("logout", logout.status_code == 204 and both(out.me_everywhere(kept), 401))

    # 5. Operator suspension and reinstatement (audited commands).
    cyb = Browser()
    cyb.sign_in(key, cy)
    live = cyb.session
    assert live is not None
    ops("member", "suspend", "--org", ORG, "--email", cy_email, "--reason", "verification")
    case("member suspend: live session 401 on both", both(cyb.me_everywhere(live), 401))
    case("member suspend: sign-in refused", Browser().sign_in(key, cy) == FAILED)
    ops("member", "reinstate", "--org", ORG, "--email", cy_email, "--reason", "verification")
    case("reinstate: old session stays revoked", both(cyb.me_everywhere(live), 401))
    case("reinstate: sign-in works again", Browser().sign_in(key, cy) == "/ask")

    # 6. SCIM deactivation and removal.
    for subject, resource, ending in ((ben, ben_id, "deactivate"), (dan, dan_id, "remove")):
        person = Browser()
        person.sign_in(key, subject)
        live = person.session
        assert live is not None
        if ending == "deactivate":
            patch = {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "path": "active", "value": False}],
            }
            changed = scim("PATCH", f"/Users/{resource}", token, content=json.dumps(patch))
        else:
            changed = scim("DELETE", f"/Users/{resource}", token)
        case(
            f"SCIM {ending}: accepted", changed.status_code in (200, 204), str(changed.status_code)
        )
        case(f"SCIM {ending}: live session 401 on both", both(person.me_everywhere(live), 401))
        case(f"SCIM {ending}: sign-in refused", Browser().sign_in(key, subject) == FAILED)

    # 7. SCIM token rotation (overlap) and revocation.
    _, second = scim_token()
    case(
        "SCIM rotation: both tokens work",
        both(scim_everywhere(token), 200) and both(scim_everywhere(second), 200),
    )
    ops("scim", "token", "revoke", "--org", ORG, "--id", token_id, "--reason", "rotated")
    case("SCIM revoke: old token 401 on both", both(scim_everywhere(token), 401))
    case("SCIM revoke: new token still works", both(scim_everywhere(second), 200))

    # 8. Identity-provider outage: the token exchange fails; sessions continue.
    survivor = Browser()
    survivor.sign_in(key, ana)
    held = survivor.session
    pending = Browser()
    callback, _ = pending.authorize(key, ana)
    compose("stop", "idp")
    location, _ = pending.finish(callback)
    case("IdP outage: sign-in is sso_unavailable", location == UNAVAILABLE, location)
    case("IdP outage: existing session continues on both", both(survivor.me_everywhere(held), 200))
    compose("start", "idp")
    compose("up", "-d", "--wait", "--no-deps", "idp")
    idp_state({CLIENT_ID: [SECRET]})
    time.sleep(61)  # new signing key: each instance refetches the JWKS at most once a minute
    case("IdP restored (new signing key): sign-in works", Browser().sign_in(key, ana) == "/ask")

    # 9. SSO disable revokes; enable restores the same members.
    out = ops("sso", "disable", "--org", ORG, "--reason", "verification")
    revoked = re.search(r"(\d+) session\(s\) it issued revoked", out)
    case(
        "sso disable: sessions revoked",
        bool(revoked) and int(revoked.group(1)) >= 1,
        f"{revoked.group(1) if revoked else '?'} revoked",
    )
    case("sso disable: session 401 on both", both(survivor.me_everywhere(held), 401))
    case("sso disable: sign-in refused", Browser().sign_in(key, ana) == FAILED)
    case("sso disable: SCIM token refused", both(scim_everywhere(second), 401))
    ops("sso", "enable", "--org", ORG, "--reason", "verification")
    case("sso enable: same member signs in", Browser().sign_in(key, ana) == "/ask")
    case("sso enable: revoked session stays revoked", both(survivor.me_everywhere(held), 401))
    case("sso enable: SCIM token works again", both(scim_everywhere(second), 200))

    # 10. Client ID update in place.
    steady = Browser()
    steady.sign_in(key, ana)
    steady_token = steady.session
    idp_state({CLIENT_ID: [SECRET], f"{CLIENT_ID}-2": [SECRET]})
    ops("sso", "update", "--org", ORG, "--client-id", f"{CLIENT_ID}-2", "--reason", "verification")
    idp_state({f"{CLIENT_ID}-2": [SECRET]})
    case("sso update --client-id: sign-in works", Browser().sign_in(key, ana) == "/ask")
    case("sso update --client-id: sessions kept", both(steady.me_everywhere(steady_token), 200))
    ops("sso", "update", "--org", ORG, "--client-id", CLIENT_ID, "--reason", "verification")
    idp_state({CLIENT_ID: [SECRET]})

    # 11. Client secret rotation, same reference (rolling restart) -- and back.
    rotated = secrets.token_urlsafe(24)
    idp_state({CLIENT_ID: [SECRET, rotated]})  # the provider accepts both meanwhile
    seconds = roll_api(rotated)
    idp_state({CLIENT_ID: [rotated]})
    case(
        "secret rotation (same reference): sign-in works",
        Browser().sign_in(key, ana) == "/ask",
        f"rolling restart {seconds:.0f} s",
    )
    case(
        "secret rotation: sessions survive the restarts",
        both(steady.me_everywhere(steady_token), 200),
    )
    idp_state({CLIENT_ID: [rotated, SECRET]})
    roll_api(SECRET)
    idp_state({CLIENT_ID: [SECRET]})
    case("secret rotation back: sign-in works", Browser().sign_in(key, ana) == "/ask")

    # 12. Client secret rotation, new reference (already in every instance's environment).
    idp_state({CLIENT_ID: [SECRET_NEXT]})
    case(
        "provider rotated first: sign-in sso_unavailable",
        Browser().sign_in(key, ana) == UNAVAILABLE,
    )
    ops(
        "sso",
        "update",
        "--org",
        ORG,
        "--client-secret-ref",
        "env:LATTICE_OIDC_SECRET_VERIFY_NEXT",
        "--reason",
        "verification",
    )
    case("secret rotation (new reference): sign-in works", Browser().sign_in(key, ana) == "/ask")
    case(
        "secret rotation (new reference): sessions kept",
        both(steady.me_everywhere(steady_token), 200),
    )
    ops(
        "sso",
        "update",
        "--org",
        ORG,
        "--client-secret-ref",
        "env:LATTICE_OIDC_SECRET_VERIFY",
        "--reason",
        "verification reset",
    )
    idp_state({CLIENT_ID: [SECRET]})

    # 13. Clock skew gauge on both instances.
    skew = gauge("lattice_clock_skew_seconds")
    case(
        "clock skew gauge on both instances",
        set(skew) == {"api1", "api2"} and all(abs(v) < 2 for v in skew.values()),
        json.dumps({k: round(v, 3) for k, v in skew.items()}),
    )

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed in {time.monotonic() - t0:.0f} s")
    for name in failed:
        print(f"FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
