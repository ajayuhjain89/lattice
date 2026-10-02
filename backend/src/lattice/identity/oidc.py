"""OIDC sign-in: authorization code flow with PKCE (Phase 10; D-1, ADR-015).

Flow (``OidcSignIn``):

1. **Start** (``/v1/auth/oidc/<login key>/start``). The login key is an opaque,
   unguessable value naming one active connection (pre-authentication lookup
   ``lattice_oidc_connection``); the organization is never named in the URL. Lattice
   creates ``state``, ``nonce``, a PKCE verifier (S256) and a browser-binding value, stores
   the attempt server-side (hashes of state and binding; ten minutes; single use) and
   redirects to the provider's authorization endpoint from its discovery document.
2. **Callback** (``/v1/auth/oidc/<login key>/callback``). The attempt is *consumed*
   (atomically, in its own transaction) before anything else happens, so a ``state``
   works once, success or not; the browser must present the binding cookie set at the
   start (login CSRF). Then the code is exchanged (PKCE verifier; client secret by
   ``env:`` reference when the client is confidential) and the ID token verified:
   asymmetric signature from the provider's JWKS (``alg`` from an allow-list matching
   the key's type: never ``none``, never HMAC), ``iss`` equal to the configured issuer,
   ``aud`` containing the client ID (``azp`` when several), ``exp``/``iat`` with one
   minute of leeway, and the ``nonce``.
3. **Identity mapping** (deterministic, organization-scoped): the configured claim
   (``sub`` by default) must equal the ``external_id`` of a directory identity of *this*
   connection, in *this* organization (row-level security bound to it), whose
   membership and user are active. No just-in-time creation, no e-mail linking, no other
   organization consulted. Anything else is one generic failure.

Network bounds: timeouts, no redirects followed, responses capped at 256 KiB, HTTPS
required in production. Discovery and keys are cached per issuer for ten minutes (public
data); an unknown key ID refreshes the keys at most once a minute. Nothing from the
provider -- claims, e-mail, error bodies, tokens -- is logged; only fixed outcome codes.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from jwt import PyJWK
from pydantic import SecretStr
from sqlalchemy import text

from lattice import metrics
from lattice.db.engine import Database
from lattice.log import log_event

logger = logging.getLogger(__name__)

ATTEMPT_TTL: Final = timedelta(minutes=10)
CACHE_SECONDS: Final = 600.0
KEY_REFRESH_SECONDS: Final = 60.0
MAX_RESPONSE_BYTES: Final = 256 * 1024
LEEWAY_SECONDS: Final = 60
SCOPES: Final = "openid email profile"
# Asymmetric only, each with the key type it needs.
ALGORITHMS: Final = {
    "RS256": "RSA",
    "RS384": "RSA",
    "RS512": "RSA",
    "PS256": "RSA",
    "PS384": "RSA",
    "PS512": "RSA",
    "ES256": "EC",
    "ES384": "EC",
    "ES512": "EC",
    "EdDSA": "OKP",
}
_SECRET_REF: Final = re.compile(r"^env:(LATTICE_OIDC_SECRET_[A-Z0-9_]{1,64})$")
_TOKEN: Final = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_CONNECTION_SQL = text("SELECT * FROM lattice_oidc_connection(:k)")


class OidcFailure(Exception):
    """A sign-in that did not succeed. ``outcome`` is a fixed code (metrics, logs);
    the user sees one of three generic messages (``public_error``)."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome

    @property
    def public_error(self) -> str:
        return "sso_unavailable" if self.outcome == "provider_error" else "sso_failed"


@dataclass(frozen=True)
class Connection:
    id: uuid.UUID
    organization_id: uuid.UUID
    issuer: str
    client_id: str
    client_secret_ref: str | None
    subject_claim: str


@dataclass(frozen=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True)
class Started:
    authorization_url: str
    binding: str


@dataclass(frozen=True)
class SignedIn:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    return_to: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii")).digest()


def safe_return_to(value: str | None) -> str:
    """A same-origin path, or ``/ask``."""
    if (
        value
        and value.startswith("/")
        and not value.startswith(("//", "/\\"))
        and len(value) <= 512
        and not re.search(r"[\x00-\x1f\x7f\\]", value)
    ):
        return value
    return "/ask"


def resolve_client_secret(reference: str | None) -> SecretStr | None:
    """``env:LATTICE_OIDC_SECRET_<NAME>`` only: an OIDC reference can never name a
    provider credential, the database URL or any other secret of this process."""
    if reference is None:
        return None
    found = _SECRET_REF.fullmatch(reference)
    value = os.environ.get(found.group(1), "") if found else ""
    if not value:
        raise OidcFailure("provider_error")
    return SecretStr(value)


class OidcClient:
    """Discovery, keys and the token exchange, with bounded time and size."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        require_https: bool,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout = timeout_seconds
        self._require_https = require_https
        self._transport = transport
        self._clock = clock
        self._metadata: dict[str, tuple[float, ProviderMetadata]] = {}
        self._keys: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._refreshed: dict[str, float] = {}

    def _check_url(self, url: Any) -> str:
        if not isinstance(url, str) or len(url) > 2000:
            raise OidcFailure("provider_error")
        parts = urlsplit(url)
        allowed = ("https",) if self._require_https else ("https", "http")
        if parts.scheme not in allowed or not parts.netloc or "@" in parts.netloc:
            raise OidcFailure("provider_error")
        return url

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        self._check_url(url)
        try:
            async with (
                httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=False, transport=self._transport
                ) as client,
                client.stream(method, url, **kwargs) as response,
            ):
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise OidcFailure("provider_error")
                if response.status_code != 200:
                    raise OidcFailure("provider_error")
        except httpx.HTTPError:
            raise OidcFailure("provider_error") from None
        try:
            return json.loads(bytes(body))
        except ValueError:
            raise OidcFailure("provider_error") from None

    async def metadata(self, issuer: str) -> ProviderMetadata:
        cached = self._metadata.get(issuer)
        if cached is not None and cached[0] > self._clock():
            return cached[1]
        document = await self._request(
            "GET", issuer.rstrip("/") + "/.well-known/openid-configuration"
        )
        if not isinstance(document, dict) or document.get("issuer") != issuer:
            # The discovery document must name exactly the configured issuer.
            raise OidcFailure("provider_error")
        methods = document.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise OidcFailure("provider_error")
        found = ProviderMetadata(
            issuer=issuer,
            authorization_endpoint=self._check_url(document.get("authorization_endpoint")),
            token_endpoint=self._check_url(document.get("token_endpoint")),
            jwks_uri=self._check_url(document.get("jwks_uri")),
        )
        self._metadata[issuer] = (self._clock() + CACHE_SECONDS, found)
        return found

    async def keys(self, jwks_uri: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        now = self._clock()
        cached = self._keys.get(jwks_uri)
        if cached is not None and cached[0] > now and not refresh:
            return cached[1]
        if (
            refresh
            and cached is not None
            and now - self._refreshed.get(jwks_uri, -1e9) < (KEY_REFRESH_SECONDS)
        ):
            return cached[1]
        document = await self._request("GET", jwks_uri)
        found = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(found, list):
            raise OidcFailure("provider_error")
        usable = [k for k in found if isinstance(k, dict) and k.get("use", "sig") == "sig"]
        self._keys[jwks_uri] = (now + CACHE_SECONDS, usable)
        if refresh:
            self._refreshed[jwks_uri] = now
        return usable

    async def exchange(
        self,
        metadata: ProviderMetadata,
        connection: Connection,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> str:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        secret = resolve_client_secret(connection.client_secret_ref)
        auth: httpx.BasicAuth | None = None
        if secret is None:
            form["client_id"] = connection.client_id
        else:
            auth = httpx.BasicAuth(connection.client_id, secret.get_secret_value())
        document = await self._request(
            "POST",
            metadata.token_endpoint,
            data=form,
            auth=auth,
            headers={"Accept": "application/json"},
        )
        id_token = document.get("id_token") if isinstance(document, dict) else None
        if not isinstance(id_token, str) or not id_token:
            raise OidcFailure("provider_error")
        return id_token

    async def verify(
        self,
        id_token: str,
        metadata: ProviderMetadata,
        connection: Connection,
        *,
        nonce: str,
        now: float | None = None,
    ) -> Mapping[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise OidcFailure("token_invalid") from None
        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm not in ALGORITHMS:
            raise OidcFailure("token_invalid")
        key = self._select(await self.keys(metadata.jwks_uri), header, algorithm)
        if key is None:
            key = self._select(await self.keys(metadata.jwks_uri, refresh=True), header, algorithm)
        if key is None:
            raise OidcFailure("token_invalid")
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=PyJWK(key, algorithm).key,
                algorithms=[algorithm],
                audience=connection.client_id,
                issuer=metadata.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            raise OidcFailure("token_invalid") from None
        current = time.time() if now is None else now
        issued = claims.get("iat")
        if not isinstance(issued, (int, float)) or issued > current + LEEWAY_SECONDS:
            raise OidcFailure("token_invalid")
        audience = claims.get("aud")
        azp = claims.get("azp")
        several = isinstance(audience, list) and len(audience) > 1
        if (several or azp is not None) and azp != connection.client_id:
            raise OidcFailure("token_invalid")
        found_nonce = claims.get("nonce")
        if not isinstance(found_nonce, str) or not hmac.compare_digest(found_nonce, nonce):
            raise OidcFailure("token_invalid")
        return claims

    @staticmethod
    def _select(
        keys: list[dict[str, Any]], header: Mapping[str, Any], algorithm: str
    ) -> dict[str, Any] | None:
        kid = header.get("kid")
        candidates = [
            k
            for k in keys
            if k.get("kty") == ALGORITHMS[algorithm]
            and k.get("alg", algorithm) == algorithm
            and (kid is None or k.get("kid") == kid)
        ]
        # Without a kid, only an unambiguous key is used.
        return candidates[0] if len(candidates) == 1 else None


class OidcSignIn:
    def __init__(self, db: Database, client: OidcClient, *, web_origin: str) -> None:
        self._db = db
        self._client = client
        self._web_origin = web_origin

    def redirect_uri(self, login_key: str) -> str:
        return f"{self._web_origin}/api/v1/auth/oidc/{login_key}/callback"

    async def _connection(self, login_key: str) -> Connection:
        if not re.fullmatch(r"[A-Za-z0-9_-]{22,64}", login_key):
            raise OidcFailure("unknown_connection")
        async with self._db.unscoped_transaction() as session:
            row = (await session.execute(_CONNECTION_SQL, {"k": login_key})).one_or_none()
        if row is None:
            raise OidcFailure("unknown_connection")
        return Connection(
            id=row.connection_id,
            organization_id=row.organization_id,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret_ref=row.client_secret_ref,
            subject_claim=row.subject_claim,
        )

    async def start(self, login_key: str, return_to: str | None) -> Started:
        connection = await self._connection(login_key)
        metadata = await self._client.metadata(connection.issuer)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        binding = secrets.token_urlsafe(32)
        async with self._db.tenant_transaction(connection.organization_id) as session:
            # Housekeeping: finished attempts of this organization go first.
            await session.execute(
                text(
                    "DELETE FROM oidc_login_attempts WHERE organization_id = :o "
                    "AND (consumed_at IS NOT NULL OR expires_at <= now())"
                ),
                {"o": connection.organization_id},
            )
            await session.execute(
                text(
                    "INSERT INTO oidc_login_attempts (organization_id, connection_id, "
                    "state_hash, binding_hash, nonce, code_verifier, return_to, expires_at) "
                    "VALUES (:o, :c, :s, :b, :n, :v, :r, now() + :ttl)"
                ),
                {
                    "o": connection.organization_id,
                    "c": connection.id,
                    "s": _sha256(state),
                    "b": _sha256(binding),
                    "n": nonce,
                    "v": verifier,
                    "r": safe_return_to(return_to),
                    "ttl": ATTEMPT_TTL,
                },
            )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": connection.client_id,
                "redirect_uri": self.redirect_uri(login_key),
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": _b64url(hashlib.sha256(verifier.encode("ascii")).digest()),
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if "?" in metadata.authorization_endpoint else "?"
        metrics.SSO_SIGN_INS.labels(outcome="started").inc()
        log_event(
            logger,
            logging.INFO,
            "auth.oidc.started",
            connection_id=str(connection.id),
            organization_id=str(connection.organization_id),
        )
        return Started(f"{metadata.authorization_endpoint}{separator}{query}", binding)

    async def complete(
        self,
        login_key: str,
        *,
        code: str | None,
        state: str | None,
        binding: str | None,
        provider_error: bool,
    ) -> SignedIn:
        connection = await self._connection(login_key)
        if state is None or not _TOKEN.fullmatch(state):
            raise OidcFailure("invalid_state")
        # Consume first, in its own transaction: this state never works again.
        async with self._db.tenant_transaction(connection.organization_id) as session:
            attempt = (
                await session.execute(
                    text(
                        "UPDATE oidc_login_attempts SET consumed_at = now() "
                        "WHERE organization_id = :o AND connection_id = :c "
                        "AND state_hash = :s AND consumed_at IS NULL AND expires_at > now() "
                        "RETURNING binding_hash, nonce, code_verifier, return_to"
                    ),
                    {"o": connection.organization_id, "c": connection.id, "s": _sha256(state)},
                )
            ).one_or_none()
        if attempt is None:
            raise OidcFailure("invalid_state")
        if binding is None or not _TOKEN.fullmatch(binding):
            raise OidcFailure("invalid_state")
        if not hmac.compare_digest(bytes(attempt.binding_hash), _sha256(binding)):
            raise OidcFailure("invalid_state")
        if provider_error or code is None or not 1 <= len(code) <= 2048:
            raise OidcFailure("provider_error")
        metadata = await self._client.metadata(connection.issuer)
        id_token = await self._client.exchange(
            metadata,
            connection,
            code=code,
            verifier=attempt.code_verifier,
            redirect_uri=self.redirect_uri(login_key),
        )
        claims = await self._client.verify(id_token, metadata, connection, nonce=attempt.nonce)
        subject = claims.get(connection.subject_claim)
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise OidcFailure("token_invalid")
        user_id = await self._member(connection, subject)
        return SignedIn(
            user_id=user_id,
            organization_id=connection.organization_id,
            return_to=safe_return_to(attempt.return_to),
        )

    async def _member(self, connection: Connection, subject: str) -> uuid.UUID:
        """The active member this connection's subject maps to, in its organization."""
        async with self._db.tenant_snapshot(connection.organization_id) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT m.user_id, m.status = 'active' AND u.status = 'active' AS usable "
                        "FROM directory_identities d "
                        "JOIN memberships m ON m.organization_id = d.organization_id "
                        "AND m.id = d.membership_id JOIN users u ON u.id = m.user_id "
                        "WHERE d.organization_id = :o AND d.connection_id = :c "
                        "AND d.external_id = :x"
                    ),
                    {"o": connection.organization_id, "c": connection.id, "x": subject},
                )
            ).one_or_none()
        if row is None:
            raise OidcFailure("not_provisioned")
        if not row.usable:
            raise OidcFailure("inactive")
        user_id: uuid.UUID = row.user_id
        return user_id
