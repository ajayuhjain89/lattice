"""Verification OIDC identity provider for the local reference stack (Phase 11, P11.3).

LOCAL VERIFICATION ONLY. It runs from the backend image with this file mounted
(deploy/compose.idp.yaml); it is never part of an image, a release or production. It is
the in-process test provider (backend/tests/identity/fake_idp.py) as a small HTTPS
server, so personas sign in to the deployed stack through a real OIDC authorization-code
flow: discovery, JWKS, PKCE (S256), state and nonce round-trips, client authentication
(client_secret_basic, or a public client) and RS256 ID tokens.

There is no login page. The "browser" (deploy/verification/identity_check.py) names the
person by adding ``persona=<subject>`` to the authorization request; the provider
approves at once and redirects to the client's redirect URI with a one-time code.
Every authorization is therefore a fresh authentication: the ID token's ``auth_time`` is
the moment of the authorization request (P12.5, D-34: ``max_age`` is honoured by
construction; ``prompt=login`` changes nothing). ``IDP_ACR`` (optional) is asserted as
``acr`` -- a label for drills, not a real authentication method.

An operator endpoint drives the drills: ``PUT /admin/state`` (bearer
``IDP_ADMIN_TOKEN``) replaces the registered clients ``{client_id: secret | null}`` and
``{client_id: [secret, ...] | null}`` (several secrets: the overlap of a rotation; null:
a public client) and the ID-token time offsets ``{"iat": s, "exp": s}``. It holds no
persistent state: a restart forgets every code and generates a new signing key under a
new key ID (clients refetch the JWKS).

Environment: IDP_ISSUER (https://idp:9443), IDP_BROWSER_ORIGIN (the authorization
endpoint's origin as the browser reaches it), IDP_CLIENT_ID, IDP_CLIENT_SECRET (empty: a
public client), IDP_ADMIN_TOKEN, IDP_CERT, IDP_KEY, IDP_PORT (9443).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import ssl
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = os.environ["IDP_ISSUER"].rstrip("/")
BROWSER_ORIGIN = os.environ.get("IDP_BROWSER_ORIGIN", ISSUER).rstrip("/")
ADMIN_TOKEN = os.environ["IDP_ADMIN_TOKEN"]
CODE_TTL_SECONDS = 120
TOKEN_TTL_SECONDS = 300


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass
class Grant:
    subject: str
    nonce: str
    challenge: str
    redirect_uri: str
    client_id: str
    expires: float
    auth_time: int


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.kid = hashlib.sha256(public).hexdigest()[:16]
        secret = os.environ.get("IDP_CLIENT_SECRET")
        self.clients: dict[str, list[str] | None] = {
            os.environ["IDP_CLIENT_ID"]: [secret] if secret else None
        }
        self.offsets: dict[str, int] = {}
        self.grants: dict[str, Grant] = {}

    def jwks(self) -> dict[str, Any]:
        jwk = RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]}


STATE = State()


def discovery() -> dict[str, Any]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{BROWSER_ORIGIN}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "none"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "lattice-verification-idp"

    def log_message(self, format: str, *args: Any) -> None:
        # One line per request, without the query string (codes, states, nonces).
        print(json.dumps({"idp": self.command, "path": urlsplit(self.path).path}), flush=True)

    def _json(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(min(length, 64 * 1024))

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path == "/.well-known/openid-configuration":
            self._json(200, discovery())
        elif parts.path == "/jwks":
            self._json(200, STATE.jwks())
        elif parts.path == "/healthz":
            self._json(200, {"status": "ok"})
        elif parts.path == "/authorize":
            self._authorize({k: v[0] for k, v in parse_qs(parts.query).items()})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path == "/token":
            self._token()
        else:
            self._json(404, {"error": "not_found"})

    def do_PUT(self) -> None:
        if urlsplit(self.path).path != "/admin/state":
            self._json(404, {"error": "not_found"})
            return
        given = self.headers.get("Authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {ADMIN_TOKEN}".encode()):
            self._json(401, {"error": "unauthorized"})
            return
        body = json.loads(self._body() or b"{}")
        with STATE.lock:
            if "clients" in body:
                STATE.clients = {
                    str(k): ([str(x) for x in v] if v else None) for k, v in body["clients"].items()
                }
            if "offsets" in body:
                STATE.offsets = {
                    k: int(v)
                    for k, v in body["offsets"].items()
                    if k in ("iat", "exp", "auth_time")
                }
        self._json(200, {"clients": sorted(STATE.clients), "offsets": STATE.offsets})

    def _authorize(self, query: dict[str, str]) -> None:
        required = (
            "response_type",
            "client_id",
            "redirect_uri",
            "state",
            "nonce",
            "code_challenge",
            "code_challenge_method",
            "persona",
        )
        if any(not query.get(name) for name in required):
            self._json(400, {"error": "invalid_request"})
            return
        if (
            query["response_type"] != "code"
            or query["code_challenge_method"] != "S256"
            or query["client_id"] not in STATE.clients
            or "openid" not in query.get("scope", "").split()
        ):
            self._json(400, {"error": "invalid_request"})
            return
        code = secrets.token_urlsafe(24)
        with STATE.lock:
            STATE.grants[code] = Grant(
                subject=query["persona"],
                nonce=query["nonce"],
                challenge=query["code_challenge"],
                redirect_uri=query["redirect_uri"],
                client_id=query["client_id"],
                expires=time.time() + CODE_TTL_SECONDS,
                auth_time=int(time.time()),
            )
        location = f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _client(self, form: dict[str, str]) -> str | None:
        """The authenticated client ID, or None."""
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                client_id, _, secret = base64.b64decode(header[6:]).decode().partition(":")
            except ValueError:
                return None
            accepted = STATE.clients.get(client_id) or []
            if not any(hmac.compare_digest(secret.encode(), a.encode()) for a in accepted):
                return None
            return client_id
        client_id = form.get("client_id", "")
        if client_id in STATE.clients and STATE.clients[client_id] is None:
            return client_id
        return None

    def _token(self) -> None:
        form = {k: v[0] for k, v in parse_qs(self._body().decode()).items()}
        with STATE.lock:
            grant = STATE.grants.pop(form.get("code", ""), None)
            client_id = self._client(form)
            offsets = dict(STATE.offsets)
        if client_id is None:
            self._json(401, {"error": "invalid_client"})
            return
        verifier = form.get("code_verifier", "")
        if (
            grant is None
            or grant.expires < time.time()
            or form.get("grant_type") != "authorization_code"
            or grant.client_id != client_id
            or form.get("redirect_uri") != grant.redirect_uri
            or _b64(hashlib.sha256(verifier.encode()).digest()) != grant.challenge
        ):
            self._json(400, {"error": "invalid_grant"})
            return
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "sub": grant.subject,
            "aud": client_id,
            "iat": now + offsets.get("iat", 0),
            "exp": now + offsets.get("exp", TOKEN_TTL_SECONDS),
            "nonce": grant.nonce,
            "email": f"{grant.subject}@verification.invalid",
            # The person authenticated when they were sent here (no IdP session to reuse).
            "auth_time": grant.auth_time + offsets.get("auth_time", 0),
            **({"acr": os.environ["IDP_ACR"]} if os.environ.get("IDP_ACR") else {}),
        }
        id_token = jwt.encode(claims, STATE.key, algorithm="RS256", headers={"kid": STATE.kid})
        self._json(
            200,
            {
                "access_token": secrets.token_urlsafe(16),
                "token_type": "Bearer",
                "expires_in": TOKEN_TTL_SECONDS,
                "id_token": id_token,
            },
        )


def main() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(os.environ["IDP_CERT"], os.environ["IDP_KEY"])
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("IDP_PORT", "9443"))), Handler)  # noqa: S104 - private network
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(json.dumps({"idp": "listening", "issuer": ISSUER}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
