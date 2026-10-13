"""The organization's identity configuration operated in the product (Phase 12, P12.4;
phase-12-plan C3, C4, C5; ADR-017).

Every function runs bound to the administrator's organization (row-level security); the
caller checks the administrator capability first (E7). Writes go through the 0020 fenced
functions only -- the API role has no table write on connections or tokens -- each with
its audit event in the same transaction (the deferred coupling triggers refuse to commit
one without the other).

**The connection.** One active OIDC connection per organization (unchanged). An
administrator may add one when none is active (and no disabled one still signs members
in: re-enable it instead -- a new connection would strand them), change the client ID,
set, replace or remove the client secret (sessions kept), disable it (sign-in stops; its
sessions end at once) and enable it again (the same connection and members). The issuer
and the identifying claim define whose subjects the directory identities hold: changing
either needs the administrator's own test sign-in under exactly the new values to match
their directory identity (the proof), a typed confirmation and a reason, and ends every
session of the connection. A provider whose subjects differ is a changed-subject identity
migration: not supported (post-v1, D-37B), never guessed by e-mail.

**The client secret** is sealed here (AES-256-GCM; the API holds the master keys, C3.4)
for this connection and stored by ``lattice_oidc_set_secret``, which destroys the previous
ciphertext. It is accepted only in the request, never returned, logged, audited or
traced; responses say whether one is set, how it is kept and when it was set.

**Tests.** *Check* runs discovery, keys and a client-credentials probe through the
outbound policy and records the result; *test sign-in* is an authorization-code flow with
purpose ``test`` (``lattice.identity.oidc``). Results are facts on the connection and an
``sso.connection_tested`` event at most once an hour per result.

**SCIM tokens.** 256-bit random, shown once in the creating response, stored as SHA-256
with their last four characters; listed with times only; rotated (a replacement, the old
one revoked in the same transaction) and revoked through the fenced functions.
"""

import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice import metrics
from lattice.access.audit import record_event
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorDetail, LatticeError
from lattice.identity.check import check_connection
from lattice.identity.oidc import Connection, OidcFailure, OidcSignIn, record_test_event
from lattice.identity.settings_models import (
    CheckOut,
    ClientIdRequest,
    ClientSecretInfo,
    ClientSecretRequest,
    IdentityChangePreview,
    IdentityChangePreviewRequest,
    IdentityChangeRequest,
    IdentityProof,
    IdentitySettings,
    IdentitySetupStatus,
    LastFailure,
    LastTest,
    NewConnectionRequest,
    OidcConnectionOut,
    RemoveSecretRequest,
    RevealedScimToken,
    RevokePreview,
    ScimSettings,
    ScimTokenOut,
    StatusPreview,
    StatusRequest,
    TestSignInOut,
    TestSignInRequest,
)
from lattice.orgs.outbound_targets import access_class, policy_for
from lattice.outbound import OutboundRefused
from lattice.provisioning.tokens import hash_token, new_token
from lattice.setup.derive import (
    Category,
    SetupFacts,
    identity_category,
    provisioning_category,
)
from lattice.setup.facts import identity_facts
from lattice.vault.errors import SecretUnavailable
from lattice.vault.keys import Keyring
from lattice.vault.sealing import Binding, seal

_FENCED_SQLSTATE: Final = "LA001"
SCIM_PREFIX: Final = "/api/scim/v2"
CREATED: Final = "sso.connection_created"
UPDATED: Final = "sso.connection_updated"
DISABLED: Final = "sso.connection_disabled"
ENABLED: Final = "sso.connection_enabled"
# Audit actions (not secrets).
TOKEN_CREATED: Final = "scim.token_created"  # noqa: S105
TOKEN_ROTATED: Final = "scim.token_rotated"  # noqa: S105
TOKEN_REVOKED: Final = "scim.token_revoked"  # noqa: S105

_REFUSALS: Final[dict[str, tuple[ErrorCode, str, str]]] = {
    # reason: (error code, field, issue)
    "connection_changed": (ErrorCode.CONFLICT, "version", "changed"),
    "connection_exists": (ErrorCode.CONFLICT, "connection", "connection_exists"),
    "identity_namespace_in_use": (
        ErrorCode.VALIDATION_FAILED,
        "connection",
        "identity_namespace_in_use",
    ),
    "identity_unverified": (ErrorCode.VALIDATION_FAILED, "proof_id", "identity_unverified"),
    "connection_disabled": (ErrorCode.VALIDATION_FAILED, "connection", "connection_disabled"),
    "no_change": (ErrorCode.VALIDATION_FAILED, "change", "no_change"),
    "no_active_connection": (
        ErrorCode.VALIDATION_FAILED,
        "connection",
        "no_active_connection",
    ),
    "token_not_live": (ErrorCode.CONFLICT, "token", "token_not_live"),
    "secret_invalid": (ErrorCode.VALIDATION_FAILED, "client_secret", "invalid"),
}


def refusal(reason: str) -> LatticeError:
    if reason == "not_administrator":
        return LatticeError(ErrorCode.PERMISSION_DENIED)
    if reason in ("connection_not_found", "token_not_found"):
        return LatticeError(ErrorCode.NOT_FOUND)
    code, field, issue = _REFUSALS.get(reason, (ErrorCode.VALIDATION_FAILED, "change", reason))
    return LatticeError(code, details=[ErrorDetail(field=field, issue=issue)])


def _invalid(field: str, issue: str) -> LatticeError:
    return LatticeError(
        ErrorCode.VALIDATION_FAILED, details=[ErrorDetail(field=field, issue=issue)]
    )


def _fenced_reason(exc: DBAPIError) -> str | None:
    orig = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        if candidate is not None and getattr(candidate, "sqlstate", None) == _FENCED_SQLSTATE:
            message = getattr(candidate, "message", None) or str(candidate)
            return str(message).strip().splitlines()[0]
    return None


@asynccontextmanager
async def _fenced() -> AsyncIterator[None]:
    try:
        yield
    except DBAPIError as exc:
        reason = _fenced_reason(exc)
        if reason is not None:
            raise refusal(reason) from None
        raise


async def _call(session: AsyncSession, sql: str, params: dict[str, Any]) -> Any:
    """One fenced function call, its refusal raised as the API's error."""
    async with _fenced(), session.begin_nested():
        return (await session.execute(text(sql), params)).scalar_one()


# --- reads -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _ConnectionRow:
    id: uuid.UUID
    status: str
    login_key: str
    issuer: str
    client_id: str
    subject_claim: str
    client_secret_ref: str | None
    updated_at: datetime
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_failure_code: str | None
    last_test_at: datetime | None
    last_test_kind: str | None
    last_test_result: str | None

    def for_org(self, organization_id: uuid.UUID) -> Connection:
        return Connection(
            id=self.id,
            organization_id=organization_id,
            issuer=self.issuer,
            client_id=self.client_id,
            client_secret_ref=self.client_secret_ref,
            subject_claim=self.subject_claim,
        )


_CONNECTION_COLUMNS = (
    "id, status, login_key, issuer, client_id, subject_claim, client_secret_ref, updated_at, "
    "last_success_at, last_failure_at, last_failure_code, last_test_at, last_test_kind, "
    "last_test_result"
)


async def _shown_connection(session: AsyncSession, org: uuid.UUID) -> _ConnectionRow | None:
    """The active connection, else the most recently changed disabled one."""
    row = (
        await session.execute(
            text(
                f"SELECT {_CONNECTION_COLUMNS} FROM oidc_connections "  # noqa: S608 - fixed
                "WHERE organization_id = :o "
                "ORDER BY (status = 'active') DESC, updated_at DESC, id LIMIT 1"
            ),
            {"o": org},
        )
    ).one_or_none()
    return None if row is None else _ConnectionRow(**row._mapping)


async def _connection(
    session: AsyncSession, org: uuid.UUID, connection_id: uuid.UUID
) -> _ConnectionRow:
    row = (
        await session.execute(
            text(
                f"SELECT {_CONNECTION_COLUMNS} FROM oidc_connections "  # noqa: S608 - fixed
                "WHERE organization_id = :o AND id = :c"
            ),
            {"o": org, "c": connection_id},
        )
    ).one_or_none()
    if row is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    return _ConnectionRow(**row._mapping)


def _secret_state(reference: str | None) -> str:
    if reference is None:
        return "none"
    return "encrypted" if reference.startswith("vault:") else "environment"


async def _secret_set_at(
    session: AsyncSession, org: uuid.UUID, connection_id: uuid.UUID
) -> datetime | None:
    found: datetime | None = (
        await session.execute(
            text(
                "SELECT created_at FROM encrypted_secrets WHERE organization_id = :o "
                "AND owner_kind = 'oidc_client_secret' AND owner_id = :c "
                "AND destroyed_at IS NULL"
            ),
            {"o": org, "c": connection_id},
        )
    ).scalar_one_or_none()
    return found


async def _counts(
    session: AsyncSession, org: uuid.UUID, connection_id: uuid.UUID
) -> tuple[int, int]:
    """(members bound, live sessions) of a connection."""
    row = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM directory_identities d JOIN memberships m "
                "ON m.organization_id = d.organization_id AND m.id = d.membership_id "
                "WHERE d.organization_id = :o AND d.connection_id = :c "
                "AND m.status <> 'removed') AS members, "
                "(SELECT count(*) FROM sessions s WHERE s.organization_id = :o "
                "AND s.oidc_connection_id = :c AND s.revoked_at IS NULL "
                "AND s.expires_at > now()) AS live"
            ),
            {"o": org, "c": connection_id},
        )
    ).one()
    return int(row.members), int(row.live)


def _status(category: Category) -> IdentitySetupStatus:
    return IdentitySetupStatus(
        state=category.state, reason=category.reason, message=category.message
    )


def _token(row: Any, active: uuid.UUID | None) -> ScimTokenOut:
    return ScimTokenOut(
        id=row.id,
        hint=row.hint,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        current_connection=active is not None and row.connection_id == active,
    )


_TOKENS_SQL = text(
    "SELECT id, connection_id, hint, created_at, revoked_at, last_used_at FROM scim_tokens "
    "WHERE organization_id = :o ORDER BY (revoked_at IS NULL) DESC, created_at DESC, id "
    "LIMIT 50"
)


async def identity_settings(
    session: AsyncSession, org: uuid.UUID, *, web_origin: str
) -> IdentitySettings:
    shown = await _shown_connection(session, org)
    facts = await identity_facts(session, org)
    setup = SetupFacts(**facts.__dict__)
    oidc = None
    active: uuid.UUID | None = None
    if shown is not None:
        policy = await policy_for(session, org, "oidc")
        members, live = await _counts(session, org, shown.id)
        active = shown.id if shown.status == "active" else None
        oidc = OidcConnectionOut(
            id=shown.id,
            status=shown.status,
            issuer=shown.issuer,
            client_id=shown.client_id,
            subject_claim=shown.subject_claim,
            client_secret=ClientSecretInfo(
                state=_secret_state(shown.client_secret_ref),
                set_at=await _secret_set_at(session, org, shown.id),
            ),
            issuer_access=access_class(policy, shown.issuer),
            sign_in_url=f"{web_origin}/sso/{shown.login_key}",
            redirect_uri=f"{web_origin}/api/v1/auth/oidc/{shown.login_key}/callback",
            last_success_at=shown.last_success_at,
            last_failure=(
                None
                if shown.last_failure_at is None or shown.last_failure_code is None
                else LastFailure(code=shown.last_failure_code, at=shown.last_failure_at)
            ),
            last_test=(
                None
                if shown.last_test_at is None
                or shown.last_test_kind is None
                or shown.last_test_result is None
                else LastTest(
                    kind=shown.last_test_kind,
                    result=shown.last_test_result,
                    at=shown.last_test_at,
                )
            ),
            members_bound=members,
            live_sessions=live,
            version=shown.updated_at,
        )
    stranded = await _stranded(session, org)
    rows = (await session.execute(_TOKENS_SQL, {"o": org})).all()
    scim = ScimSettings(
        base_url=f"{web_origin}{SCIM_PREFIX}",
        identifying_claim=shown.subject_claim if shown is not None else None,
        tokens=[_token(r, active) for r in rows],
        provisioned_members=facts.provisioned_members,
        refused_recently=facts.refused_recently,
        status=_status(provisioning_category(setup)),
    )
    return IdentitySettings(
        oidc=oidc,
        can_add_connection=active is None and stranded == 0,
        sign_in_status=_status(identity_category(setup)),
        scim=scim,
    )


async def _stranded(session: AsyncSession, org: uuid.UUID) -> int:
    """Members (not removed) bound to a disabled connection."""
    found: int = (
        await session.execute(
            text(
                "SELECT count(*) FROM directory_identities d JOIN oidc_connections c "
                "ON c.organization_id = d.organization_id AND c.id = d.connection_id "
                "AND c.status = 'disabled' JOIN memberships m "
                "ON m.organization_id = d.organization_id AND m.id = d.membership_id "
                "AND m.status <> 'removed' WHERE d.organization_id = :o"
            ),
            {"o": org},
        )
    ).scalar_one()
    return found


# --- the outbound policy for administrator-entered issuers --------------------------------


async def _check_issuer(sign_in: OidcSignIn, org: uuid.UUID, issuer: str) -> None:
    """The issuer must pass the organization's outbound policy (shape and resolution):
    a public address, or a target the operator registered. Fixed issues only."""
    policy = await sign_in.policy(org)
    try:
        await policy.check_target(issuer)
    except OutboundRefused as refused:
        issue = (
            "outbound_blocked"
            if refused.code in ("target_invalid", "target_not_public")
            else "issuer_unreachable"
        )
        raise _invalid("issuer", issue) from None


def _actor(membership_id: uuid.UUID) -> str:
    return f"member:{membership_id}"


def _shape(issuer: str, client_id: str, claim: str, secret: str) -> dict[str, Any]:
    return {
        "issuer": issuer,
        "client_id": client_id,
        "subject_claim": claim,
        "client_secret": secret,
    }


# --- the connection ---------------------------------------------------------------------------


async def add_connection(
    db: Database,
    sign_in: OidcSignIn,
    keyring: Keyring,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    request: NewConnectionRequest,
) -> None:
    if request.client_secret is not None and keyring.active is None:
        raise _invalid("client_secret", "secret_storage_unavailable")
    await _check_issuer(sign_in, organization_id, request.issuer)
    async with db.tenant_transaction(organization_id) as session:
        connection_id: uuid.UUID = await _call(
            session,
            "SELECT lattice_oidc_create(:a, :k, :i, :c, :claim)",
            {
                "a": actor_membership_id,
                "k": secrets.token_urlsafe(24),
                "i": request.issuer,
                "c": request.client_id,
                "claim": request.subject_claim,
            },
        )
        if request.client_secret is not None:
            version = (await _connection(session, organization_id, connection_id)).updated_at
            await _store_secret(
                session,
                keyring,
                organization_id,
                actor_membership_id,
                connection_id,
                version,
                request.client_secret,
            )
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=CREATED,
            target={"connection_id": str(connection_id)},
            before=None,
            after=_shape(
                request.issuer,
                request.client_id,
                request.subject_claim,
                "encrypted" if request.client_secret is not None else "none",
            ),
            reason=request.reason,
        )


async def _store_secret(
    session: AsyncSession,
    keyring: Keyring,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    connection_id: uuid.UUID,
    version: datetime,
    value: SecretStr,
) -> str:
    secret_id = uuid.uuid4()
    try:
        sealed = seal(
            keyring,
            value,
            Binding(organization_id, secret_id, "oidc_client_secret", connection_id),
        )
    except (SecretUnavailable, ValueError):
        raise _invalid("client_secret", "secret_storage_unavailable") from None
    done: str = await _call(
        session,
        "SELECT lattice_oidc_set_secret(:a, :c, :v, :s, :k, :n, :ct)",
        {
            "a": actor_membership_id,
            "c": connection_id,
            "v": version,
            "s": secret_id,
            "k": sealed.key_id,
            "n": sealed.nonce,
            "ct": sealed.ciphertext,
        },
    )
    return done


async def update_client_id(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    connection_id: uuid.UUID,
    request: ClientIdRequest,
) -> None:
    async with db.tenant_transaction(organization_id) as session:
        current = await _connection(session, organization_id, connection_id)
        await _call(
            session,
            "SELECT lattice_oidc_set_client(:a, :c, :v, :id)",
            {
                "a": actor_membership_id,
                "c": connection_id,
                "v": request.version,
                "id": request.client_id,
            },
        )
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=UPDATED,
            target={"connection_id": str(connection_id)},
            before={"client_id": current.client_id},
            after={"client_id": request.client_id},
            reason=request.reason,
        )


async def set_client_secret(
    db: Database,
    keyring: Keyring,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    connection_id: uuid.UUID,
    request: ClientSecretRequest,
) -> None:
    """Set or replace the client secret: a new ciphertext, the previous one destroyed in
    the same transaction. Sessions are kept."""
    async with db.tenant_transaction(organization_id) as session:
        current = await _connection(session, organization_id, connection_id)  # 404 first
        if keyring.active is None:
            raise _invalid("client_secret", "secret_storage_unavailable")
        done = await _store_secret(
            session,
            keyring,
            organization_id,
            actor_membership_id,
            connection_id,
            request.version,
            request.client_secret,
        )
        before = _secret_state(current.client_secret_ref)
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=UPDATED,
            target={"connection_id": str(connection_id)},
            before={"client_secret": before},
            after={"client_secret": "encrypted (replaced)" if done == "replaced" else "encrypted"},
            reason=request.reason,
        )


async def remove_client_secret(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    connection_id: uuid.UUID,
    request: RemoveSecretRequest,
) -> None:
    """A public client from now on (PKCE only): the ciphertext is destroyed."""
    async with db.tenant_transaction(organization_id) as session:
        current = await _connection(session, organization_id, connection_id)
        await _call(
            session,
            "SELECT lattice_oidc_clear_secret(:a, :c, :v)",
            {"a": actor_membership_id, "c": connection_id, "v": request.version},
        )
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=UPDATED,
            target={"connection_id": str(connection_id)},
            before={"client_secret": _secret_state(current.client_secret_ref)},
            after={"client_secret": "none"},
            reason=request.reason,
        )


async def preview_status(
    session: AsyncSession, org: uuid.UUID, connection_id: uuid.UUID, *, enabled: bool
) -> StatusPreview:
    current = await _connection(session, org, connection_id)
    if (current.status == "active") == enabled:
        raise refusal("no_change")
    if enabled and await _shown_active_other(session, org, connection_id):
        raise refusal("connection_exists")
    members, live = await _counts(session, org, connection_id)
    return StatusPreview(
        enabled=enabled, sessions_ending=0 if enabled else live, members_bound=members
    )


async def _shown_active_other(
    session: AsyncSession, org: uuid.UUID, connection_id: uuid.UUID
) -> bool:
    found: bool = (
        await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM oidc_connections WHERE organization_id = :o "
                "AND status = 'active' AND id <> :c)"
            ),
            {"o": org, "c": connection_id},
        )
    ).scalar_one()
    return found


async def set_status(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    connection_id: uuid.UUID,
    request: StatusRequest,
) -> int:
    """Disable (sign-in stops, every session of the connection ends at once) or enable
    (the same connection and members). Returns the sessions ended."""
    async with db.tenant_transaction(organization_id) as session:
        revoked: int = await _call(
            session,
            "SELECT lattice_oidc_set_status(:a, :c, :v, :e)",
            {
                "a": actor_membership_id,
                "c": connection_id,
                "v": request.version,
                "e": request.enabled,
            },
        )
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=ENABLED if request.enabled else DISABLED,
            target={"connection_id": str(connection_id)},
            before={"status": "disabled" if request.enabled else "active"},
            after={
                "status": "active" if request.enabled else "disabled",
                **({} if request.enabled else {"sessions_revoked": revoked}),
            },
            reason=request.reason,
        )
    metrics.sessions_revoked("sso_disabled", revoked)
    return revoked


# --- issuer and identifying claim -------------------------------------------------------------


_PROOF_SQL = text(
    "SELECT id, completed_at, expires_at FROM oidc_login_attempts "
    "WHERE organization_id = :o AND connection_id = :c AND purpose = 'test' "
    "AND membership_id = :m AND outcome = 'matched' AND proof_used_at IS NULL "
    "AND expires_at > now() AND coalesce(proposed_issuer, :ci) = :i "
    "AND coalesce(proposed_subject_claim, :cc) = :claim "
    "AND (proposed_issuer IS NOT NULL OR proposed_subject_claim IS NOT NULL) "
    "ORDER BY completed_at DESC LIMIT 1"
)


async def preview_identity_change(
    session: AsyncSession,
    org: uuid.UUID,
    membership_id: uuid.UUID,
    connection_id: uuid.UUID,
    request: IdentityChangePreviewRequest,
) -> IdentityChangePreview:
    current = await _connection(session, org, connection_id)
    if current.status != "active":
        raise refusal("connection_disabled")
    issuer = request.issuer or current.issuer
    claim = request.subject_claim or current.subject_claim
    if issuer == current.issuer and claim == current.subject_claim:
        raise refusal("no_change")
    policy = await policy_for(session, org, "oidc")
    members, live = await _counts(session, org, connection_id)
    proof = (
        await session.execute(
            _PROOF_SQL,
            {
                "o": org,
                "c": connection_id,
                "m": membership_id,
                "ci": current.issuer,
                "cc": current.subject_claim,
                "i": issuer,
                "claim": claim,
            },
        )
    ).one_or_none()
    return IdentityChangePreview(
        issuer=issuer,
        subject_claim=claim,
        issuer_changed=issuer != current.issuer,
        claim_changed=claim != current.subject_claim,
        issuer_access=access_class(policy, issuer),
        sessions_ending=live,
        members_bound=members,
        proof=(
            None
            if proof is None
            else IdentityProof(
                test_id=proof.id, completed_at=proof.completed_at, expires_at=proof.expires_at
            )
        ),
    )


async def change_identity(
    db: Database,
    sign_in: OidcSignIn,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    connection_id: uuid.UUID,
    request: IdentityChangeRequest,
) -> int:
    """Change the issuer and/or identifying claim with the administrator's proof (their
    matched test sign-in under exactly these values); every session of the connection
    ends. Returns the sessions ended."""
    async with db.tenant_snapshot(organization_id) as session:
        current = await _connection(session, organization_id, connection_id)
    expected = request.issuer if request.issuer != current.issuer else request.subject_claim
    if request.confirmation.strip() != expected:
        raise _invalid("confirmation", "mismatch")
    if request.issuer != current.issuer:
        await _check_issuer(sign_in, organization_id, request.issuer)
    async with db.tenant_transaction(organization_id) as session:
        revoked: int = await _call(
            session,
            "SELECT lattice_oidc_change_identity(:a, :c, :v, :i, :claim, :p)",
            {
                "a": actor_membership_id,
                "c": connection_id,
                "v": request.version,
                "i": request.issuer,
                "claim": request.subject_claim,
                "p": request.proof_id,
            },
        )
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=UPDATED,
            target={"connection_id": str(connection_id)},
            before={"issuer": current.issuer, "subject_claim": current.subject_claim},
            after={
                "issuer": request.issuer,
                "subject_claim": request.subject_claim,
                "sessions_revoked": revoked,
                "verified_by_test_sign_in": True,
            },
            reason=request.reason,
        )
    metrics.sessions_revoked("sso_identity_changed", revoked)
    return revoked


# --- tests ----------------------------------------------------------------------------------


async def run_check(
    db: Database,
    sign_in: OidcSignIn,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> CheckOut:
    async with db.tenant_snapshot(organization_id) as session:
        current = await _connection(session, organization_id, connection_id)
    if current.status != "active":
        raise refusal("connection_disabled")
    connection = current.for_org(organization_id)
    found = await check_connection(
        sign_in, connection, redirect_uri=sign_in.redirect_uri(current.login_key)
    )
    checked_at = datetime.now(UTC)
    async with db.tenant_transaction(organization_id) as session:
        await session.execute(
            text(
                "UPDATE oidc_connections SET last_test_at = :t, last_test_kind = 'check', "
                "last_test_result = :r WHERE organization_id = :o AND id = :c"
            ),
            {"t": checked_at, "r": found.result, "o": organization_id, "c": connection_id},
        )
        await record_test_event(
            session,
            organization_id,
            connection_id,
            actor=_actor(actor_membership_id),
            kind="check",
            result=found.result,
            proposed=False,
        )
    return CheckOut(result=found.result, step=found.step, checked_at=checked_at)


async def start_test_sign_in(
    sign_in: OidcSignIn,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    request: TestSignInRequest,
) -> tuple[uuid.UUID, str, str]:
    """(test ID, authorization URL, browser binding)."""
    if request.issuer is not None:
        await _check_issuer(sign_in, organization_id, request.issuer)
    try:
        started = await sign_in.start_test(
            organization_id,
            actor_membership_id,
            proposed_issuer=request.issuer,
            proposed_subject_claim=request.subject_claim,
        )
    except OidcFailure as failure:
        if failure.outcome == "unknown_connection":
            raise refusal("no_active_connection") from None
        raise _invalid("connection", failure.outcome) from None
    assert started.attempt_id is not None  # noqa: S101 - a test attempt
    return started.attempt_id, started.authorization_url, started.binding


async def test_sign_in_result(
    session: AsyncSession, org: uuid.UUID, membership_id: uuid.UUID, test_id: uuid.UUID
) -> TestSignInOut:
    """A test sign-in's result, for the administrator who started it only."""
    row = (
        await session.execute(
            text(
                "SELECT a.id, a.outcome, a.observed_identifier, a.proposed_issuer, "
                "a.proposed_subject_claim, a.completed_at, a.expires_at, a.proof_used_at, "
                "a.expires_at <= now() AS expired, c.issuer, c.subject_claim "
                "FROM oidc_login_attempts a JOIN oidc_connections c "
                "ON c.organization_id = a.organization_id AND c.id = a.connection_id "
                "WHERE a.organization_id = :o AND a.id = :t AND a.purpose = 'test' "
                "AND a.membership_id = :m"
            ),
            {"o": org, "t": test_id, "m": membership_id},
        )
    ).one_or_none()
    if row is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    proposed = row.proposed_issuer is not None or row.proposed_subject_claim is not None
    status = "completed" if row.outcome is not None else "expired" if row.expired else "pending"
    return TestSignInOut(
        test_id=row.id,
        status=status,
        outcome=row.outcome,
        identifier=row.observed_identifier,
        issuer=row.proposed_issuer or row.issuer,
        subject_claim=row.proposed_subject_claim or row.subject_claim,
        proposed=proposed,
        completed_at=row.completed_at,
        expires_at=row.expires_at,
        proof_available=(
            proposed and row.outcome == "matched" and row.proof_used_at is None and not row.expired
        ),
    )


# --- SCIM tokens ------------------------------------------------------------------------------


async def _token_out(session: AsyncSession, org: uuid.UUID, token_id: uuid.UUID) -> ScimTokenOut:
    row = (
        await session.execute(
            text(
                "SELECT t.id, t.connection_id, t.hint, t.created_at, t.revoked_at, "
                "t.last_used_at, c.status = 'active' AS current FROM scim_tokens t "
                "JOIN oidc_connections c ON c.organization_id = t.organization_id "
                "AND c.id = t.connection_id WHERE t.organization_id = :o AND t.id = :t"
            ),
            {"o": org, "t": token_id},
        )
    ).one_or_none()
    if row is None:
        raise LatticeError(ErrorCode.NOT_FOUND)
    return ScimTokenOut(
        id=row.id,
        hint=row.hint,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        current_connection=bool(row.current),
    )


async def create_token(
    db: Database, *, organization_id: uuid.UUID, actor_membership_id: uuid.UUID, reason: str
) -> RevealedScimToken:
    token = new_token()
    hint = token[-4:]
    async with db.tenant_transaction(organization_id) as session:
        token_id: uuid.UUID = await _call(
            session,
            "SELECT lattice_create_scim_token(:a, :h, :hint)",
            {"a": actor_membership_id, "h": hash_token(token), "hint": hint},
        )
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=TOKEN_CREATED,
            target={"token_id": str(token_id)},
            before=None,
            after={"hint": hint},
            reason=reason,
        )
        shown = await _token_out(session, organization_id, token_id)
    return RevealedScimToken(token=shown, revealed_token=token)


async def rotate_token(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    token_id: uuid.UUID,
    reason: str,
) -> RevealedScimToken:
    """A replacement now; the old token is refused from the same moment (no overlap: the
    provider fails until the new token is pasted in -- for an overlap, create a second
    token, switch the provider, then revoke the old one)."""
    token = new_token()
    hint = token[-4:]
    async with db.tenant_transaction(organization_id) as session:
        await _token_out(session, organization_id, token_id)  # 404 when unknown
        new_id: uuid.UUID = await _call(
            session,
            "SELECT lattice_rotate_scim_token(:a, :t, :h, :hint)",
            {"a": actor_membership_id, "t": token_id, "h": hash_token(token), "hint": hint},
        )
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=TOKEN_ROTATED,
            target={"token_id": str(new_id), "replaced_token_id": str(token_id)},
            before={"revoked": False},
            after={"hint": hint, "replaced_token_revoked": True},
            reason=reason,
        )
        shown = await _token_out(session, organization_id, new_id)
    return RevealedScimToken(token=shown, revealed_token=token)


async def preview_revoke(
    session: AsyncSession, org: uuid.UUID, token_id: uuid.UUID
) -> RevokePreview:
    token = await _token_out(session, org, token_id)
    if token.revoked_at is not None:
        raise refusal("token_not_live")
    row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE revoked_at IS NULL) AS live FROM scim_tokens "
                "WHERE organization_id = :o"
            ),
            {"o": org},
        )
    ).one()
    facts = await identity_facts(session, org)
    return RevokePreview(
        last_live_token=int(row.live) == 1, provisioned_members=facts.provisioned_members
    )


async def revoke_token(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    token_id: uuid.UUID,
    reason: str,
) -> ScimTokenOut:
    async with db.tenant_transaction(organization_id) as session:
        await _token_out(session, organization_id, token_id)  # 404 when unknown
        await _call(
            session,
            "SELECT lattice_revoke_scim_token(:a, :t)",
            {"a": actor_membership_id, "t": token_id},
        )
        await record_event(
            session,
            organization_id,
            actor=_actor(actor_membership_id),
            action=TOKEN_REVOKED,
            target={"token_id": str(token_id)},
            before={"revoked": False},
            after={"revoked": True},
            reason=reason,
        )
        return await _token_out(session, organization_id, token_id)
