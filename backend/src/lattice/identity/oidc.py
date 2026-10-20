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
   start (login CSRF). Then the code is exchanged (PKCE verifier; the client secret from
   its ``env:`` reference or ``vault:`` ciphertext when the client is confidential) and the
   ID token verified: asymmetric signature from the provider's JWKS (``alg`` from an
   allow-list matching the key's type: never ``none``, never HMAC), ``iss`` equal to the
   configured issuer, ``aud`` containing the client ID (``azp`` when several),
   ``exp``/``iat`` with one minute of leeway, and the ``nonce``.
3. **Identity mapping** (deterministic, organization-scoped): the configured claim
   (``sub`` by default) must equal the ``external_id`` of a directory identity of *this*
   connection, in *this* organization (row-level security bound to it), whose
   membership and user are active. No just-in-time creation, no e-mail linking, no other
   organization consulted. Anything else is one generic failure.

**Outbound policy (P12.4, phase-12-plan C4).** Every request -- discovery, keys, the
token exchange -- goes through ``lattice.outbound`` under the connection's organization's
policy for the ``oidc`` purpose: a public issuer is ``https`` on port 443 and every address
it resolves to must be globally routable, checked at connect time (DNS rebinding cannot
pass), redirects are refused and environment proxies ignored; a private or self-hosted
issuer works only as the exact target an operator registered for that organization
(``lattice outbound allow``), and stops working when the registration is revoked. The
endpoints a discovery document names are re-checked under the same class as the issuer.
Nothing is special-cased for localhost.

**Test sign-in (P12.4, C5).** An administrator may sign in through the connection --
or through proposed new issuer / identifying-claim values -- with purpose ``test``: the
attempt is bound to their membership; the callback creates **no session**, records the
outcome (``matched`` when the identifier under the tested values equals their own
directory identity on this connection) and their own identifier, and returns them to the
identity settings. A ``matched`` test under proposed values is the proof an issuer or
claim change requires (``lattice_oidc_change_identity``).

**Egress step-up (P12.5, D-34; phase-12-plan C6.4).** Raising what may leave Lattice
needs a fresh re-authentication of the administrator at the identity provider: purpose
``step_up``, bound to their membership **and their Lattice session** and carrying the exact
previewed change, its reason and the policy version it was previewed on; ``max_age=0`` and
``prompt=login`` (plus ``acr_values`` when the operator configured an assurance
requirement for the connection). The callback must arrive in the same browser session
(the session cookie names the bound session; anything else is ``session_mismatch``,
checked before the code is exchanged), verifies the ID token as for sign-in, requires the
identifier to equal the administrator's own directory identity, requires a verified
``auth_time`` not before the attempt began and at most five minutes old (an absent,
malformed or stale ``auth_time`` is ``not_fresh`` -- ``prompt=login`` alone proves
nothing), and the configured ``acr`` when one is required (``assurance_insufficient``).
Then ``lattice_create_step_up_grant`` re-checks freshness, the session and the
administrator in the database and creates a single-use grant (five minutes at most). It
creates **no session** and changes none; the browser returns to the AI providers page.
A fresh authentication is not by itself multi-factor authentication: Lattice claims only
what the provider asserted (``acr``) against what the operator required.

Sign-in facts (P12.1, C12): a successful sign-in records the connection's
``last_success_at`` (the setup record's "verified"); a failure on the provider's side, of
the outbound policy or of the client credentials records ``last_failure_at`` and its code.
Timestamps and codes only, at most once a minute. A rejected authorization code (400 from
the token endpoint) is anyone's to send and never marks the provider.

Bounds: 10 s per request, no redirects, responses capped at 256 KiB. Discovery and keys
are cached for ten minutes per issuer and policy (public data); an unknown key ID
refreshes the keys at most once a minute. Nothing from the provider -- claims, e-mail,
error bodies, tokens -- is logged; only fixed outcome codes.
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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice import metrics
from lattice.db.engine import Database
from lattice.log import log_event
from lattice.orgs.outbound_targets import policy_for
from lattice.outbound import OutboundPolicy, OutboundRefused, Resolver, fetch, system_resolver
from lattice.providers.secrets import SecretStore, SecretUnavailable
from lattice.vault.records import load_sealed

logger = logging.getLogger(__name__)

ATTEMPT_TTL: Final = timedelta(minutes=10)
CACHE_SECONDS: Final = 600.0
KEY_REFRESH_SECONDS: Final = 60.0
MAX_RESPONSE_BYTES: Final = 256 * 1024
LEEWAY_SECONDS: Final = 60
SCOPES: Final = "openid email profile"
TEST_RETURN: Final = "/settings/identity"
STEP_UP_RETURN: Final = "/settings/providers"
# D-34: a step-up's authentication must be this fresh, with this much clock leeway.
STEP_UP_MAX_AGE_SECONDS: Final = 300
STEP_UP_LEEWAY_SECONDS: Final = 60
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
_FROM_REFERENCE: Final = object()
# The setup record's sign-in facts are written at most once a minute per connection.
_SUCCESS_SQL = text(
    "UPDATE oidc_connections SET last_success_at = now() WHERE organization_id = :o "
    "AND id = :c AND (last_success_at IS NULL OR last_success_at < now() - interval '1 minute')"
)
_FAILURE_SQL = text(
    "UPDATE oidc_connections SET last_failure_at = now(), last_failure_code = :code "
    "WHERE organization_id = :o AND id = :c AND (last_failure_at IS NULL "
    "OR last_failure_at < now() - interval '1 minute' OR last_failure_code IS DISTINCT FROM :code)"
)
# Outcomes that are the connection's (or the provider's) fault, recorded as a failure fact.
FAILURE_CODES: Final = {
    "provider_error": "provider_unavailable",
    "invalid_configuration": "invalid_configuration",
    "outbound_blocked": "outbound_blocked",
    "credentials_rejected": "credentials_rejected",
}
# The outbound policy's refusals that are the policy's (not the provider's).
_BLOCKED: Final = frozenset({"target_invalid", "target_not_public"})
# OAuth errors (RFC 6749 §5.2) that may be read from a token-endpoint error body.
_OAUTH_ERRORS: Final = frozenset(
    {
        "invalid_request",
        "invalid_client",
        "invalid_grant",
        "unauthorized_client",
        "unsupported_grant_type",
        "invalid_scope",
    }
)


class OidcFailure(Exception):
    """A sign-in that did not succeed. ``outcome`` is a fixed code (metrics, logs);
    the user sees one of three generic messages (``public_error``)."""

    def __init__(
        self, outcome: str, *, status_code: int | None = None, error: str | None = None
    ) -> None:
        super().__init__(outcome)
        self.outcome = outcome
        # The provider's HTTP status and OAuth error code when it answered with an error
        # (never shown, never logged beyond the fixed outcome).
        self.status_code = status_code
        self.error = error

    @property
    def public_error(self) -> str:
        return "sso_unavailable" if self.outcome in FAILURE_CODES else "sso_failed"

    @property
    def rejected_code(self) -> bool:
        """The token endpoint refused the authorization code itself (400, not the client):
        anyone can send a bogus or reused code, so it is never the provider's fault."""
        return self.outcome == "provider_error" and self.status_code == 400


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
    attempt_id: uuid.UUID | None = None


@dataclass(frozen=True)
class SignedIn:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    connection_id: uuid.UUID
    return_to: str


@dataclass(frozen=True)
class StepUpCompleted:
    """An egress step-up finished (whatever its outcome): no session; back to providers."""

    attempt_id: uuid.UUID
    organization_id: uuid.UUID
    outcome: str
    return_to: str = STEP_UP_RETURN


@dataclass(frozen=True)
class TestCompleted:
    """A test sign-in finished (whatever its outcome): no session; back to settings."""

    attempt_id: uuid.UUID
    organization_id: uuid.UUID
    outcome: str
    return_to: str = TEST_RETURN


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
    provider credential, the database URL or any other secret of this process.
    (``vault:`` references are opened by ``OidcSignIn``, which reads the ciphertext.)"""
    if reference is None:
        return None
    found = _SECRET_REF.fullmatch(reference)
    value = os.environ.get(found.group(1), "") if found else ""
    if not value:
        raise OidcFailure("provider_error")
    return SecretStr(value)


def _oauth_error(body: bytes) -> str | None:
    """The OAuth ``error`` code of an error response, when it is one of RFC 6749's."""
    try:
        document = json.loads(body)
    except ValueError:
        return None
    error = document.get("error") if isinstance(document, dict) else None
    return error if isinstance(error, str) and error in _OAUTH_ERRORS else None


def _policy_key(policy: OutboundPolicy) -> frozenset[Any]:
    return frozenset(policy.registered)


class OidcClient:
    """Discovery, keys and the token exchange under the outbound policy, with bounded
    time and size. ``transport`` (tests only) replaces the network after the same policy
    decision (shape and resolution through ``resolver``)."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = system_resolver,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport
        self.resolver = resolver
        self._clock = clock
        self._metadata: dict[tuple[str, frozenset[Any]], tuple[float, ProviderMetadata]] = {}
        self._keys: dict[tuple[str, frozenset[Any]], tuple[float, list[dict[str, Any]]]] = {}
        self._refreshed: dict[tuple[str, frozenset[Any]], float] = {}

    async def _send(
        self, policy: OutboundPolicy, method: str, url: str, *, base: bool, **kwargs: Any
    ) -> tuple[int, bytes]:
        if self._transport is None:
            response = await fetch(
                policy,
                method,
                url,
                base=base,
                max_bytes=MAX_RESPONSE_BYTES,
                total_timeout=self._timeout,
                **kwargs,
            )
            return response.status_code, response.body
        # Tests: the policy's decision exactly as in production, then the in-process IdP.
        await policy.check_target(url, base=base)
        async with (
            httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False, transport=self._transport
            ) as client,
            client.stream(method, url, **kwargs) as streamed,
        ):
            if 300 <= streamed.status_code < 400:
                raise OutboundRefused("redirect_refused")
            body = bytearray()
            async for chunk in streamed.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise OutboundRefused("response_too_large")
            return streamed.status_code, bytes(body)

    async def _request(
        self, policy: OutboundPolicy, method: str, url: Any, *, base: bool = False, **kwargs: Any
    ) -> Any:
        if not isinstance(url, str):
            raise OidcFailure("invalid_configuration")
        try:
            status, body = await self._send(policy, method, url, base=base, **kwargs)
        except OutboundRefused as refused:
            outcome = "outbound_blocked" if refused.code in _BLOCKED else "provider_error"
            raise OidcFailure(outcome) from None
        except httpx.HTTPError:
            raise OidcFailure("provider_error") from None
        if status != 200:
            raise OidcFailure("provider_error", status_code=status, error=_oauth_error(body))
        try:
            return json.loads(body)
        except ValueError:
            raise OidcFailure("invalid_configuration") from None

    async def metadata(
        self, policy: OutboundPolicy, issuer: str, *, fresh: bool = False
    ) -> ProviderMetadata:
        """``fresh``: fetch now even when cached (an administrator's check)."""
        key = (issuer, _policy_key(policy))
        cached = self._metadata.get(key)
        if cached is not None and cached[0] > self._clock() and not fresh:
            return cached[1]
        document = await self._request(
            policy, "GET", issuer.rstrip("/") + "/.well-known/openid-configuration", base=True
        )
        if not isinstance(document, dict) or document.get("issuer") != issuer:
            # The discovery document must name exactly the configured issuer.
            raise OidcFailure("invalid_configuration")
        methods = document.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise OidcFailure("invalid_configuration")
        endpoints = [
            document.get("authorization_endpoint"),
            document.get("token_endpoint"),
            document.get("jwks_uri"),
        ]
        if not all(isinstance(e, str) and e for e in endpoints):
            raise OidcFailure("invalid_configuration")
        try:
            # The same class as the issuer: a public issuer cannot point Lattice at a
            # private address (C4).
            await policy.check_discovery_endpoints(issuer, endpoints)
        except OutboundRefused as refused:
            outcome = "outbound_blocked" if refused.code in _BLOCKED else "provider_error"
            raise OidcFailure(outcome) from None
        found = ProviderMetadata(issuer, *(str(e) for e in endpoints))
        self._metadata[key] = (self._clock() + CACHE_SECONDS, found)
        return found

    async def keys(
        self, policy: OutboundPolicy, jwks_uri: str, *, refresh: bool = False, fresh: bool = False
    ) -> list[dict[str, Any]]:
        key = (jwks_uri, _policy_key(policy))
        now = self._clock()
        cached = self._keys.get(key)
        if cached is not None and cached[0] > now and not refresh and not fresh:
            return cached[1]
        if (
            refresh
            and cached is not None
            and now - self._refreshed.get(key, -1e9) < (KEY_REFRESH_SECONDS)
        ):
            return cached[1]
        document = await self._request(policy, "GET", jwks_uri)
        found = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(found, list):
            raise OidcFailure("invalid_configuration")
        usable = [k for k in found if isinstance(k, dict) and k.get("use", "sig") == "sig"]
        self._keys[key] = (now + CACHE_SECONDS, usable)
        if refresh:
            self._refreshed[key] = now
        return usable

    async def exchange(
        self,
        policy: OutboundPolicy,
        metadata: ProviderMetadata,
        connection: Connection,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
        client_secret: object = _FROM_REFERENCE,
    ) -> str:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        secret = (
            resolve_client_secret(connection.client_secret_ref)
            if client_secret is _FROM_REFERENCE
            else client_secret
        )
        auth: httpx.BasicAuth | None = None
        if not isinstance(secret, SecretStr):
            form["client_id"] = connection.client_id
        else:
            auth = httpx.BasicAuth(connection.client_id, secret.get_secret_value())
        try:
            document = await self._request(
                policy,
                "POST",
                metadata.token_endpoint,
                data=form,
                auth=auth,
                headers={"Accept": "application/json"},
            )
        except OidcFailure as failure:
            # The client itself was refused: its ID or secret is wrong (RFC 6749 §5.2).
            if failure.status_code == 401 or failure.error == "invalid_client":
                raise OidcFailure("credentials_rejected", status_code=failure.status_code) from None
            raise
        id_token = document.get("id_token") if isinstance(document, dict) else None
        if not isinstance(id_token, str) or not id_token:
            raise OidcFailure("invalid_configuration")
        return id_token

    async def verify(
        self,
        policy: OutboundPolicy,
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
        key = self._select(await self.keys(policy, metadata.jwks_uri), header, algorithm)
        if key is None:
            key = self._select(
                await self.keys(policy, metadata.jwks_uri, refresh=True), header, algorithm
            )
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


def _identifier(claims: Mapping[str, Any], claim: str) -> str:
    value = claims.get(claim)
    if not isinstance(value, str) or not 1 <= len(value) <= 255:
        raise OidcFailure("token_invalid")
    return value


@dataclass(frozen=True)
class _Attempt:
    id: uuid.UUID
    binding_hash: bytes
    nonce: str
    code_verifier: str
    return_to: str
    purpose: str
    membership_id: uuid.UUID | None
    proposed_issuer: str | None
    proposed_subject_claim: str | None
    session_id: uuid.UUID | None = None
    created_at: datetime | None = None


# Test outcomes (an OidcFailure's outcome -> the recorded one).
_TEST_FAILURES: Final = {
    "provider_error": "provider_unavailable",
    "invalid_configuration": "invalid_configuration",
    "outbound_blocked": "outbound_blocked",
    "credentials_rejected": "credentials_rejected",
    "token_invalid": "token_invalid",
}


class OidcSignIn:
    def __init__(
        self,
        db: Database,
        client: OidcClient,
        *,
        web_origin: str,
        secrets: SecretStore | None = None,
    ) -> None:
        self._db = db
        self._client = client
        self._web_origin = web_origin
        # Opens vault: client secrets (the API holds the master keys, C3.4).
        self._secrets = secrets

    @property
    def client(self) -> OidcClient:
        return self._client

    async def policy(self, organization_id: uuid.UUID) -> OutboundPolicy:
        """The organization's outbound policy for OIDC (its live registrations)."""
        async with self._db.tenant_snapshot(organization_id) as session:
            return await policy_for(
                session, organization_id, "oidc", resolver=self._client.resolver
            )

    async def client_secret(self, connection: Connection) -> SecretStr | None:
        """The client secret to present, from its env: reference or its vault:
        ciphertext bound to this connection; ``provider_error`` when unavailable."""
        reference = connection.client_secret_ref
        if reference is None or not reference.startswith("vault:"):
            return resolve_client_secret(reference)
        if self._secrets is None:
            raise OidcFailure("provider_error")
        async with self._db.tenant_snapshot(connection.organization_id) as session:
            sealed = await load_sealed(
                session,
                reference,
                organization_id=connection.organization_id,
                owner_kind="oidc_client_secret",
                owner_id=connection.id,
            )
        try:
            return self._secrets.resolve(reference, sealed)
        except SecretUnavailable:
            raise OidcFailure("provider_error") from None

    async def _record(
        self,
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        *,
        succeeded: bool,
        outcome: str = "provider_error",
    ) -> None:
        """Best effort: a sign-in fact for the setup record (never fails a sign-in)."""
        try:
            async with self._db.tenant_transaction(organization_id) as session:
                await session.execute(
                    _SUCCESS_SQL if succeeded else _FAILURE_SQL,
                    {
                        "o": organization_id,
                        "c": connection_id,
                        **({} if succeeded else {"code": FAILURE_CODES[outcome]}),
                    },
                )
        except (SQLAlchemyError, OSError):
            log_event(logger, logging.WARNING, "auth.oidc.fact_not_recorded")

    async def record_success(self, organization_id: uuid.UUID, connection_id: uuid.UUID) -> None:
        """Called once the session exists: the connection is verified (C1, C12)."""
        await self._record(organization_id, connection_id, succeeded=True)

    async def _record_failure(self, connection: Connection, failure: OidcFailure) -> None:
        if failure.outcome in FAILURE_CODES and not failure.rejected_code:
            await self._record(
                connection.organization_id,
                connection.id,
                succeeded=False,
                outcome=failure.outcome,
            )

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

    async def _authorize(
        self,
        connection: Connection,
        login_key: str,
        *,
        return_to: str,
        membership_id: uuid.UUID | None = None,
        proposed_issuer: str | None = None,
        proposed_subject_claim: str | None = None,
        purpose: str = "sign_in",
        step_up: dict[str, Any] | None = None,
        acr_values: list[str] | None = None,
    ) -> Started:
        """Store an attempt and build the provider's authorization URL. The provider is
        the connection's issuer, or the proposed one of a test. ``step_up``: the session,
        change, reason and policy version a step-up attempt is bound to."""
        policy = await self.policy(connection.organization_id)
        issuer = proposed_issuer or connection.issuer
        try:
            metadata = await self._client.metadata(policy, issuer)
        except OidcFailure as failure:
            if proposed_issuer is None:
                await self._record_failure(connection, failure)
            raise
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        binding = secrets.token_urlsafe(32)
        fresh = purpose in ("test", "step_up")
        bound = step_up or {}
        async with self._db.tenant_transaction(connection.organization_id) as session:
            # Housekeeping: finished sign-in attempts and expired ones go first.
            await session.execute(
                text(
                    "DELETE FROM oidc_login_attempts WHERE organization_id = :o "
                    "AND ((purpose = 'sign_in' AND consumed_at IS NOT NULL) "
                    "OR expires_at <= now())"
                ),
                {"o": connection.organization_id},
            )
            attempt_id: uuid.UUID = (
                await session.execute(
                    text(
                        "INSERT INTO oidc_login_attempts (organization_id, connection_id, "
                        "state_hash, binding_hash, nonce, code_verifier, return_to, expires_at, "
                        "purpose, membership_id, proposed_issuer, proposed_subject_claim, "
                        "session_id, change, reason, policy_version) "
                        "VALUES (:o, :c, :s, :b, :n, :v, :r, now() + :ttl, :purpose, :m, :pi, "
                        ":pc, :sid, CAST(:change AS jsonb), :reason, :pv) RETURNING id"
                    ),
                    {
                        "o": connection.organization_id,
                        "c": connection.id,
                        "s": _sha256(state),
                        "b": _sha256(binding),
                        "n": nonce,
                        "v": verifier,
                        "r": return_to,
                        "ttl": ATTEMPT_TTL,
                        "purpose": purpose,
                        "m": membership_id,
                        "pi": proposed_issuer,
                        "pc": proposed_subject_claim,
                        "sid": bound.get("session_id"),
                        "change": None if "change" not in bound else json.dumps(bound["change"]),
                        "reason": bound.get("reason"),
                        "pv": bound.get("policy_version"),
                    },
                )
            ).scalar_one()
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
                # A test or a step-up always asks the provider to authenticate again.
                **({"prompt": "login", "max_age": "0"} if fresh else {}),
                **({"acr_values": " ".join(acr_values)} if acr_values else {}),
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
            purpose=purpose,
        )
        return Started(
            f"{metadata.authorization_endpoint}{separator}{query}",
            binding,
            attempt_id if fresh else None,
        )

    async def start(self, login_key: str, return_to: str | None) -> Started:
        connection = await self._connection(login_key)
        return await self._authorize(connection, login_key, return_to=safe_return_to(return_to))

    async def start_test(
        self,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        *,
        proposed_issuer: str | None = None,
        proposed_subject_claim: str | None = None,
    ) -> Started:
        """An administrator's test sign-in through the organization's active connection,
        optionally under proposed issuer / claim values. ``unknown_connection`` when there
        is no active connection."""
        async with self._db.tenant_snapshot(organization_id) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, login_key, issuer, client_id, client_secret_ref, "
                        "subject_claim FROM oidc_connections "
                        "WHERE organization_id = :o AND status = 'active'"
                    ),
                    {"o": organization_id},
                )
            ).one_or_none()
        if row is None:
            raise OidcFailure("unknown_connection")
        connection = Connection(
            id=row.id,
            organization_id=organization_id,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret_ref=row.client_secret_ref,
            subject_claim=row.subject_claim,
        )
        return await self._authorize(
            connection,
            row.login_key,
            return_to=TEST_RETURN,
            purpose="test",
            membership_id=membership_id,
            proposed_issuer=proposed_issuer if proposed_issuer != connection.issuer else None,
            proposed_subject_claim=(
                proposed_subject_claim
                if proposed_subject_claim != connection.subject_claim
                else None
            ),
        )

    async def step_up_connection(
        self, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> tuple[Connection, str, list[str] | None] | None:
        """The active connection, its login key and the operator's step-up assurance
        requirement -- when the administrator has a directory identity on it (a step-up
        proves who they are there). None otherwise: no step-up is possible."""
        async with self._db.tenant_snapshot(organization_id) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT c.id, c.login_key, c.issuer, c.client_id, c.client_secret_ref, "
                        "c.subject_claim, c.step_up_acr_values, EXISTS (SELECT 1 FROM "
                        "directory_identities d WHERE d.organization_id = c.organization_id "
                        "AND d.connection_id = c.id AND d.membership_id = :m) AS bound "
                        "FROM oidc_connections c WHERE c.organization_id = :o "
                        "AND c.status = 'active'"
                    ),
                    {"o": organization_id, "m": membership_id},
                )
            ).one_or_none()
        if row is None or not row.bound:
            return None
        connection = Connection(
            id=row.id,
            organization_id=organization_id,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret_ref=row.client_secret_ref,
            subject_claim=row.subject_claim,
        )
        acr = list(row.step_up_acr_values) if row.step_up_acr_values else None
        return connection, row.login_key, acr

    async def start_step_up(
        self,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        session_id: uuid.UUID,
        *,
        change: dict[str, Any],
        reason: str,
        policy_version: int,
    ) -> Started:
        """An egress step-up (D-34): a fresh authentication of this administrator, in
        this session, for exactly this change. ``unknown_connection`` when no step-up is
        possible (no active connection, or no directory identity on it)."""
        found = await self.step_up_connection(organization_id, membership_id)
        if found is None:
            raise OidcFailure("unknown_connection")
        connection, login_key, acr = found
        return await self._authorize(
            connection,
            login_key,
            return_to=STEP_UP_RETURN,
            purpose="step_up",
            membership_id=membership_id,
            step_up={
                "session_id": session_id,
                "change": change,
                "reason": reason,
                "policy_version": policy_version,
            },
            acr_values=acr,
        )

    async def _consume(self, connection: Connection, state: str) -> _Attempt | None:
        # Consume first, in its own transaction: this state never works again.
        async with self._db.tenant_transaction(connection.organization_id) as session:
            row = (
                await session.execute(
                    text(
                        "UPDATE oidc_login_attempts SET consumed_at = now() "
                        "WHERE organization_id = :o AND connection_id = :c "
                        "AND state_hash = :s AND consumed_at IS NULL AND expires_at > now() "
                        "RETURNING id, binding_hash, nonce, code_verifier, return_to, purpose, "
                        "membership_id, proposed_issuer, proposed_subject_claim, session_id, "
                        "created_at"
                    ),
                    {"o": connection.organization_id, "c": connection.id, "s": _sha256(state)},
                )
            ).one_or_none()
        if row is None:
            return None
        return _Attempt(
            row.id,
            bytes(row.binding_hash),
            row.nonce,
            row.code_verifier,
            row.return_to,
            row.purpose,
            row.membership_id,
            row.proposed_issuer,
            row.proposed_subject_claim,
            row.session_id,
            row.created_at,
        )

    async def _claims(
        self,
        policy: OutboundPolicy,
        connection: Connection,
        login_key: str,
        attempt: _Attempt,
        code: str,
    ) -> Mapping[str, Any]:
        metadata = await self._client.metadata(policy, connection.issuer)
        secret = await self.client_secret(connection)
        id_token = await self._client.exchange(
            policy,
            metadata,
            connection,
            code=code,
            verifier=attempt.code_verifier,
            redirect_uri=self.redirect_uri(login_key),
            client_secret=secret,
        )
        return await self._client.verify(
            policy, id_token, metadata, connection, nonce=attempt.nonce
        )

    async def complete(
        self,
        login_key: str,
        *,
        code: str | None,
        state: str | None,
        binding: str | None,
        provider_error: bool,
        current_session: Callable[[], Awaitable[uuid.UUID | None]] | None = None,
    ) -> SignedIn | TestCompleted | StepUpCompleted:
        """``current_session``: the Lattice session the callback's browser presents (read
        only for a step-up, which must come back to the session it was bound to)."""
        connection = await self._connection(login_key)
        if state is None or not _TOKEN.fullmatch(state):
            raise OidcFailure("invalid_state")
        attempt = await self._consume(connection, state)
        if attempt is None:
            raise OidcFailure("invalid_state")
        if binding is None or not _TOKEN.fullmatch(binding):
            raise OidcFailure("invalid_state")
        if not hmac.compare_digest(attempt.binding_hash, _sha256(binding)):
            raise OidcFailure("invalid_state")
        if attempt.purpose == "test":
            return await self._complete_test(
                connection, login_key, attempt, code=code, provider_error=provider_error
            )
        if attempt.purpose == "step_up":
            presented = await current_session() if current_session is not None else None
            return await self._complete_step_up(
                connection,
                login_key,
                attempt,
                code=code,
                provider_error=provider_error,
                session_id=presented,
            )
        if provider_error or code is None or not 1 <= len(code) <= 2048:
            raise OidcFailure("provider_error")
        policy = await self.policy(connection.organization_id)
        try:
            claims = await self._claims(policy, connection, login_key, attempt, code)
        except OidcFailure as failure:
            await self._record_failure(connection, failure)
            raise
        subject = _identifier(claims, connection.subject_claim)
        user_id = await self._member(connection, subject)
        return SignedIn(
            user_id=user_id,
            organization_id=connection.organization_id,
            connection_id=connection.id,
            return_to=safe_return_to(attempt.return_to),
        )

    async def _complete_test(
        self,
        connection: Connection,
        login_key: str,
        attempt: _Attempt,
        *,
        code: str | None,
        provider_error: bool,
    ) -> TestCompleted:
        """Exchange and verify under the tested values; compare the administrator's own
        identifier with their directory identity on this connection. Never a session."""
        tested = replace(
            connection,
            issuer=attempt.proposed_issuer or connection.issuer,
            subject_claim=attempt.proposed_subject_claim or connection.subject_claim,
        )
        proposed = attempt.proposed_issuer is not None or attempt.proposed_subject_claim is not None
        identifier: str | None = None
        if provider_error or code is None or not 1 <= len(code) <= 2048:
            outcome = "sign_in_failed"
        else:
            policy = await self.policy(connection.organization_id)
            try:
                claims = await self._claims(policy, tested, login_key, attempt, code)
                identifier = _identifier(claims, tested.subject_claim)
            except OidcFailure as failure:
                outcome = (
                    "sign_in_failed"
                    if failure.rejected_code
                    else _TEST_FAILURES.get(failure.outcome, "sign_in_failed")
                )
                if not proposed:
                    await self._record_failure(connection, failure)
            else:
                outcome = await self._match(connection, attempt, identifier)
        async with self._db.tenant_transaction(connection.organization_id) as session:
            await session.execute(
                text(
                    "UPDATE oidc_login_attempts SET outcome = :r, observed_identifier = :x, "
                    "completed_at = now() WHERE organization_id = :o AND id = :a"
                ),
                {
                    "r": outcome,
                    "x": identifier if outcome in ("matched", "mismatched") else None,
                    "o": connection.organization_id,
                    "a": attempt.id,
                },
            )
            if not proposed:
                # A test of the connection as configured is its latest test.
                await session.execute(
                    text(
                        "UPDATE oidc_connections SET last_test_at = now(), "
                        "last_test_kind = 'sign_in', last_test_result = :r "
                        "WHERE organization_id = :o AND id = :c"
                    ),
                    {"r": outcome, "o": connection.organization_id, "c": connection.id},
                )
            assert attempt.membership_id is not None  # noqa: S101 - purpose test
            await record_test_event(
                session,
                connection.organization_id,
                connection.id,
                actor=f"member:{attempt.membership_id}",
                kind="sign_in",
                result=outcome,
                proposed=proposed,
            )
        if outcome == "matched" and not proposed:
            await self.record_success(connection.organization_id, connection.id)
        metrics.SSO_SIGN_INS.labels(outcome="test_completed").inc()
        log_event(
            logger,
            logging.INFO,
            "auth.oidc.test_completed",
            connection_id=str(connection.id),
            organization_id=str(connection.organization_id),
            outcome=outcome,
        )
        return TestCompleted(attempt.id, connection.organization_id, outcome)

    async def _complete_step_up(
        self,
        connection: Connection,
        login_key: str,
        attempt: _Attempt,
        *,
        code: str | None,
        provider_error: bool,
        session_id: uuid.UUID | None,
    ) -> StepUpCompleted:
        """Verify a fresh re-authentication of the bound administrator in the bound
        session and, when it holds, create the single-use grant. Never a session."""
        acr_required: list[str] | None = None
        grant: tuple[datetime, str | None] | None = None
        if provider_error or code is None or not 1 <= len(code) <= 2048:
            outcome = "sign_in_failed"
        elif session_id is None or session_id != attempt.session_id:
            # Checked before the code is used: a step-up completes only in the browser
            # session that started it.
            outcome = "session_mismatch"
        else:
            policy = await self.policy(connection.organization_id)
            found = await self.step_up_connection(
                connection.organization_id, attempt.membership_id or uuid.uuid4()
            )
            acr_required = found[2] if found is not None and found[0].id == connection.id else None
            try:
                claims = await self._claims(policy, connection, login_key, attempt, code)
                identifier = _identifier(claims, connection.subject_claim)
            except OidcFailure as failure:
                outcome = (
                    "sign_in_failed"
                    if failure.rejected_code
                    else _TEST_FAILURES.get(failure.outcome, "sign_in_failed")
                )
            else:
                outcome = await self._match(connection, attempt, identifier)
                if outcome == "matched":
                    outcome, grant = self._fresh(claims, attempt, acr_required)
        if grant is not None:
            outcome = await self._grant(connection, attempt, *grant)
        if outcome != "granted":
            async with self._db.tenant_transaction(connection.organization_id) as session:
                await session.execute(
                    text(
                        "UPDATE oidc_login_attempts SET outcome = :r, completed_at = now() "
                        "WHERE organization_id = :o AND id = :a AND outcome IS NULL"
                    ),
                    {"r": outcome, "o": connection.organization_id, "a": attempt.id},
                )
        metrics.SSO_SIGN_INS.labels(outcome="step_up_completed").inc()
        metrics.STEP_UPS.labels(outcome=metrics.bounded(outcome, metrics.STEP_UP_OUTCOMES)).inc()
        log_event(
            logger,
            logging.INFO,
            "auth.oidc.step_up_completed",
            connection_id=str(connection.id),
            organization_id=str(connection.organization_id),
            outcome=outcome,
        )
        return StepUpCompleted(attempt.id, connection.organization_id, outcome)

    @staticmethod
    def _fresh(
        claims: Mapping[str, Any], attempt: _Attempt, acr_required: list[str] | None
    ) -> tuple[str, tuple[datetime, str | None] | None]:
        """D-34: a verified ``auth_time`` from this attempt's authentication, at most five
        minutes old; the required ``acr`` when the operator configured one."""
        auth_time = claims.get("auth_time")
        now = time.time()
        started = attempt.created_at.timestamp() if attempt.created_at is not None else now
        if (
            not isinstance(auth_time, (int, float))
            or isinstance(auth_time, bool)
            or auth_time > now + STEP_UP_LEEWAY_SECONDS
            or now - auth_time > STEP_UP_MAX_AGE_SECONDS
            or auth_time < started - STEP_UP_LEEWAY_SECONDS
        ):
            return "not_fresh", None
        acr = claims.get("acr")
        if acr_required is not None and (not isinstance(acr, str) or acr not in acr_required):
            return "assurance_insufficient", None
        asserted = acr if acr_required is not None and isinstance(acr, str) else None
        return "matched", (datetime.fromtimestamp(auth_time, UTC), asserted)

    async def _grant(
        self, connection: Connection, attempt: _Attempt, auth_time: datetime, acr: str | None
    ) -> str:
        """The database's half: freshness, session and administrator re-checked, the grant
        created and its event recorded in one transaction."""
        from lattice.providers.stepup import create_grant  # an import cycle otherwise

        assert attempt.membership_id is not None  # noqa: S101 - a step-up attempt
        assert attempt.session_id is not None  # noqa: S101 - a step-up attempt
        return await create_grant(
            self._db,
            connection.organization_id,
            membership_id=attempt.membership_id,
            session_id=attempt.session_id,
            attempt_id=attempt.id,
            auth_time=auth_time,
            acr=acr,
        )

    async def _match(self, connection: Connection, attempt: _Attempt, identifier: str) -> str:
        async with self._db.tenant_snapshot(connection.organization_id) as session:
            stored = (
                await session.execute(
                    text(
                        "SELECT external_id FROM directory_identities WHERE organization_id = :o "
                        "AND connection_id = :c AND membership_id = :m"
                    ),
                    {
                        "o": connection.organization_id,
                        "c": connection.id,
                        "m": attempt.membership_id,
                    },
                )
            ).scalar_one_or_none()
        if stored is None:
            return "not_provisioned"
        return "matched" if hmac.compare_digest(stored, identifier) else "mismatched"

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


# At most one ``sso.connection_tested`` event per connection, kind and result an hour: a
# test is audited for its result, not for every click (C11).
_TEST_EVENT_SQL = text(
    """
    INSERT INTO audit_events (organization_id, actor, action, target, before, after, reason)
    SELECT :o, :actor, 'sso.connection_tested', CAST(:target AS jsonb), NULL,
           CAST(:after AS jsonb), :reason
    WHERE NOT EXISTS (
      SELECT 1 FROM audit_events e
      WHERE e.organization_id = :o AND e.action = 'sso.connection_tested'
        AND e.target ->> 'connection_id' = :c
        AND e.after ->> 'kind' = :kind AND e.after ->> 'result' = :result
        AND (e.after ->> 'proposed')::boolean = :proposed
        AND e.created_at > now() - interval '1 hour'
    )
    """
)


async def record_test_event(
    session: AsyncSession,
    organization_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    actor: str,
    kind: str,
    result: str,
    proposed: bool,
) -> None:
    await session.execute(
        _TEST_EVENT_SQL,
        {
            "o": organization_id,
            "actor": actor,
            "target": json.dumps({"connection_id": str(connection_id)}),
            "after": json.dumps({"kind": kind, "result": result, "proposed": proposed}),
            "reason": "Identity provider test"
            if kind == "check"
            else "Identity provider test sign-in",
            "c": str(connection_id),
            "kind": kind,
            "result": result,
            "proposed": proposed,
        },
    )
