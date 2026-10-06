"""An in-process OIDC identity provider for tests: real keys, real signatures, and a
token endpoint that checks the PKCE verifier, the redirect URI and client authentication.

The test drives the browser's part: it reads Lattice's redirect to ``/authorize``,
"signs the person in" with :meth:`FakeIdp.authorize` (which returns the callback URL with
a one-time code), and follows it back to Lattice. Each ID-token claim and header can be
overridden to build defective tokens."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass
class Grant:
    subject: Any
    nonce: str
    challenge: str
    redirect_uri: str
    client_id: str


@dataclass
class FakeIdp:
    issuer: str = "https://idp.halden.example"
    client_id: str = "lattice"
    client_secret: str | None = None
    # Overrides applied to every ID token issued (claims; None removes one).
    claims: dict[str, Any] = field(default_factory=dict)
    # Claims set to the issuing time plus an offset in seconds (clock-skew boundaries).
    claim_offsets: dict[str, int] = field(default_factory=dict)
    header: dict[str, Any] = field(default_factory=dict)
    # "rsa" (RS256), "ec" (ES256), "other-rsa" (a key the JWKS does not hold), "none",
    # "hs256-public" (HMAC keyed with the public key: algorithm confusion).
    signing: str = "rsa"
    discovery_issuer: str | None = None
    token_status: int = 200
    oversized: bool = False
    # An outage: every endpoint is unreachable.
    down: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.grants: dict[str, Grant] = {}

    # --- endpoints ------------------------------------------------------------------

    def jwks(self) -> dict[str, Any]:
        rsa_jwk = RSAAlgorithm.to_jwk(self.rsa.public_key(), as_dict=True)
        ec_jwk = ECAlgorithm.to_jwk(self.ec.public_key(), as_dict=True)
        return {
            "keys": [
                {**rsa_jwk, "kid": "rsa-1", "use": "sig", "alg": "RS256"},
                {**ec_jwk, "kid": "ec-1", "use": "sig", "alg": "ES256"},
            ]
        }

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": self.discovery_issuer or self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "code_challenge_methods_supported": ["S256"],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("identity provider unreachable", request=request)
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            if self.oversized:
                return httpx.Response(200, content=b"{" + b" " * (300 * 1024) + b"}")
            return httpx.Response(200, json=self.discovery())
        if path.endswith("/jwks"):
            return httpx.Response(200, json=self.jwks())
        if path.endswith("/token") and request.method == "POST":
            return self._token(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        if self.token_status != 200:
            return httpx.Response(self.token_status, json={"error": "invalid_grant"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        grant = self.grants.pop(form.get("code", ""), None)
        if grant is None or form.get("grant_type") != "authorization_code":
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = form.get("code_verifier", "")
        if _b64(hashlib.sha256(verifier.encode()).digest()) != grant.challenge:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if form.get("redirect_uri") != grant.redirect_uri:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if self.client_secret is None:
            if form.get("client_id") != self.client_id:
                return httpx.Response(401, json={"error": "invalid_client"})
        else:
            expected = (
                "Basic "
                + base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            )
            if request.headers.get("authorization") != expected:
                return httpx.Response(401, json={"error": "invalid_client"})
        return httpx.Response(
            200,
            json={"access_token": "at", "token_type": "Bearer", "id_token": self._id_token(grant)},
        )

    # --- the browser's part -----------------------------------------------------------

    def authorize(self, authorization_url: str, subject: Any) -> str:
        """Sign ``subject`` in: the callback URL Lattice should receive."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorization_url).query).items()}
        assert query["response_type"] == "code"
        assert query["code_challenge_method"] == "S256"
        assert query["scope"] == "openid email profile"
        code = secrets.token_urlsafe(16)
        self.grants[code] = Grant(
            subject=subject,
            nonce=query["nonce"],
            challenge=query["code_challenge"],
            redirect_uri=query["redirect_uri"],
            client_id=query["client_id"],
        )
        return f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"

    # --- tokens -----------------------------------------------------------------------

    def _id_token(self, grant: Grant) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": grant.subject,
            "aud": grant.client_id,
            "exp": now + 300,
            "iat": now,
            "nonce": grant.nonce,
            "email": "pat@halden.example",
            "name": "Pat Doe",
        }
        for key, offset in self.claim_offsets.items():
            claims[key] = now + offset
        for key, value in self.claims.items():
            if value is None:
                claims.pop(key, None)
            else:
                claims[key] = value
        if self.signing == "ec":
            header = {"kid": "ec-1", **self.header}
            return jwt.encode(claims, self.ec, algorithm="ES256", headers=header)
        if self.signing == "other-rsa":
            header = {"kid": "rsa-1", **self.header}
            return jwt.encode(claims, self.other_rsa, algorithm="RS256", headers=header)
        if self.signing in ("none", "hs256-public"):
            alg = "none" if self.signing == "none" else "HS256"
            head = _b64(json.dumps({"alg": alg, "typ": "JWT", "kid": "rsa-1"}).encode())
            body = _b64(json.dumps(claims).encode())
            if alg == "none":
                return f"{head}.{body}."
            public = self.rsa.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            signature = hmac.new(public, f"{head}.{body}".encode(), hashlib.sha256).digest()
            return f"{head}.{body}.{_b64(signature)}"
        header = {"kid": "rsa-1", **self.header}
        return jwt.encode(claims, self.rsa, algorithm="RS256", headers=header)
