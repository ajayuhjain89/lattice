"""Members and administrators operated in the product (Phase 12, P12.2; phase-12-plan
C2.1-C2.3; ADR-017).

* **Add a member by hand** (D-30): a person bound to the exact identifier the
  organization's active OIDC connection sends for them, with least access. Never an
  invitation, never matched by e-mail, never just-in-time at sign-in.
* **Administrator capability** (D-31): granted or revoked for *another* member, with a
  reason; it never changes content access. The last active administrator cannot be
  taken away by a product action.
* **Reinstate a removed member** (D-36): the same membership and directory identity,
  active again with least access -- nothing they had is restored. (The operator's
  ``lattice member reinstate`` restores prior access: it reverses an incident; this is a
  re-hire.)

Every write runs through a fenced database function (migration 0017) in one
transaction with its ``access.member_changed`` event; the 0013 audit triggers refuse to
commit one without the other. The functions repeat the rules this module checks first,
so the database stays authoritative: a refusal from either answers the same.

**The administrator set** is serialised per organization by a transaction-level
advisory lock: lock, then count (READ COMMITTED: the count sees every transaction that
committed before the lock was granted). The fenced functions and the Access status
change take it before counting; a deferred trigger takes it again at commit for any API
write that takes an active administrator out of the set. The identity provider (SCIM)
and operators are not bound: a person who left loses access even when they were the
last administrator, and the setup record then reads *No active administrator*.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice import metrics
from lattice.access.audit import MEMBER_CHANGED, record_event
from lattice.access.directory import AccessSnapshot, access_snapshot, entity_names
from lattice.access.models import (
    AccessPreview,
    AdministratorPreview,
    AdministratorRequest,
    IdentityRequirement,
    NewMemberRequest,
    ReinstateRequest,
)
from lattice.access.preview import context_after, document_delta, effects
from lattice.authz import AuthorizationContext, resolve_member_context
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorDetail, LatticeError

# Advisory-lock classes (pg_advisory_xact_lock(class, hashtext(org))); 0017 uses the same.
ADMIN_SET_LOCK_CLASS: Final = int.from_bytes(b"LADM", "big")
MEMBER_CREATE_LOCK_CLASS: Final = int.from_bytes(b"LMEM", "big")
_ADMIN_SET_LOCK_SQL = text(
    f"SELECT pg_advisory_xact_lock({ADMIN_SET_LOCK_CLASS}, hashtext(CAST(:o AS uuid)::text))"
)
_ACTIVE_ADMINS_SQL = text(
    "SELECT count(*) FROM memberships m JOIN users u ON u.id = m.user_id "
    "WHERE m.organization_id = :o AND m.is_admin AND m.status = 'active' "
    "AND u.status = 'active'"
)
_FENCED_SQLSTATE: Final = "LA001"

REASON_CREATED: Final = "Added by an administrator"


# --- refusals ---------------------------------------------------------------------------


def _invalid(field: str, issue: str) -> LatticeError:
    return LatticeError(
        ErrorCode.VALIDATION_FAILED, details=[ErrorDetail(field=field, issue=issue)]
    )


def refusal(reason: str) -> LatticeError:
    """The API's answer to a fixed refusal reason (from this module or a fenced function)."""
    if reason == "not_administrator":
        return LatticeError(ErrorCode.PERMISSION_DENIED)
    if reason == "own_access":
        return LatticeError(
            ErrorCode.PERMISSION_DENIED,
            "You can't change your own access. Ask another administrator.",
            details=[ErrorDetail(field="membership", issue="own_access")],
        )
    if reason == "member_not_found":
        return LatticeError(ErrorCode.NOT_FOUND)
    if reason in ("identifier_taken", "email_taken"):
        field = "identifier" if reason == "identifier_taken" else "email"
        return LatticeError(
            ErrorCode.CONFLICT,
            "A member of this organization already has this "
            + ("identifier." if field == "identifier" else "e-mail."),
            details=[ErrorDetail(field=field, issue="taken")],
        )
    field = {
        "no_active_connection": "identity",
        "identity_not_current": "identity",
        "user_inactive": "user",
        "not_removed": "status",
        "member_removed": "membership",
        "last_administrator": "membership",
        "identifier_invalid": "identifier",
        "email_invalid": "email",
        "name_invalid": "name",
    }.get(reason, "membership")
    return _invalid(field, reason)


def _fenced_reason(exc: DBAPIError) -> str | None:
    """The fixed reason of a fenced function's refusal (SQLSTATE LA001), else None."""
    orig = exc.orig
    cause = getattr(orig, "__cause__", None)
    for candidate in (orig, cause):
        if candidate is None:
            continue
        if getattr(candidate, "sqlstate", None) == _FENCED_SQLSTATE:
            message = getattr(candidate, "message", None) or str(candidate)
            return str(message).strip().splitlines()[0]
    return None


def _unique_violation(exc: DBAPIError) -> str | None:
    """The unique constraint a concurrent writer won (23505), else None."""
    orig = exc.orig
    cause = getattr(orig, "__cause__", None)
    for candidate in (orig, cause):
        if candidate is not None and getattr(candidate, "sqlstate", None) == "23505":
            return str(getattr(candidate, "constraint_name", "") or "")
    return None


@asynccontextmanager
async def fenced() -> AsyncIterator[None]:
    """Translate a fenced function's or trigger's refusal into the API's error."""
    try:
        yield
    except DBAPIError as exc:
        reason = _fenced_reason(exc)
        if reason is not None:
            raise refusal(reason) from None
        constraint = _unique_violation(exc)
        if constraint is not None:
            # A concurrent SCIM create of the same person won the race.
            raise refusal(
                "identifier_taken" if "external_id" in constraint else "email_taken"
            ) from None
        raise


# --- the administrator set ---------------------------------------------------------------


async def lock_administrator_set(session: AsyncSession, organization_id: uuid.UUID) -> None:
    await session.execute(_ADMIN_SET_LOCK_SQL, {"o": organization_id})


async def _lock_member(
    session: AsyncSession, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> None:
    # A removed membership cannot be locked by the API role (0012's policy fences it);
    # the fenced function locks it itself.
    await session.execute(
        text(
            "SELECT 1 FROM memberships WHERE organization_id = :o AND id = :m "
            "AND status <> 'removed' FOR UPDATE"
        ),
        {"o": organization_id, "m": membership_id},
    )


async def active_administrators(session: AsyncSession, organization_id: uuid.UUID) -> int:
    return int((await session.execute(_ACTIVE_ADMINS_SQL, {"o": organization_id})).scalar_one())


async def _actor_is_active_administrator(
    session: AsyncSession, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> bool:
    found: bool = (
        await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.organization_id = :o AND m.id = :m AND m.is_admin "
                "AND m.status = 'active' AND u.status = 'active')"
            ),
            {"o": organization_id, "m": membership_id},
        )
    ).scalar_one()
    return found


async def guard_administrator_set(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    actor_membership_id: uuid.UUID,
    before: AccessSnapshot,
    after: AccessSnapshot,
    target_user_active: bool,
) -> None:
    """For a product status change: when it takes an active administrator out of the
    set, lock the set, re-check the actor and refuse leaving none (ADR-017). Call
    inside the change's transaction, before writing."""
    was = before.is_admin and before.status == "active" and target_user_active
    stays = after.is_admin and after.status == "active"
    if not was or stays:
        return
    await lock_administrator_set(session, organization_id)
    if not await _actor_is_active_administrator(session, organization_id, actor_membership_id):
        raise refusal("not_administrator")
    if await active_administrators(session, organization_id) <= 1:
        raise refusal("last_administrator")


# --- identity requirement ----------------------------------------------------------------


async def identity_requirement(
    session: AsyncSession, organization_id: uuid.UUID
) -> IdentityRequirement:
    row = (
        await session.execute(
            text(
                "SELECT subject_claim, issuer FROM oidc_connections "
                "WHERE organization_id = :o AND status = 'active'"
            ),
            {"o": organization_id},
        )
    ).one_or_none()
    if row is None:
        return IdentityRequirement(available=False, subject_claim=None, issuer=None)
    return IdentityRequirement(available=True, subject_claim=row.subject_claim, issuer=row.issuer)


# --- add a member by hand ----------------------------------------------------------------


async def create_member(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    request: NewMemberRequest,
) -> uuid.UUID:
    """The new membership's ID. Conflicts inside this organization are 409; nothing in
    another organization is read, so it can neither collide nor be revealed."""
    async with fenced(), db.tenant_transaction(organization_id) as session:
        membership_id: uuid.UUID = (
            await session.execute(
                text("SELECT lattice_create_member(:actor, :email, :name, :given, :family, :x)"),
                {
                    "actor": actor_membership_id,
                    "email": request.email,
                    "name": request.name,
                    "given": request.given_name,
                    "family": request.family_name,
                    "x": request.identifier,
                },
            )
        ).scalar_one()
        found = await access_snapshot(session, organization_id, membership_id)
        assert found is not None  # noqa: S101 - created above
        metrics.member_changed(None, "active", "administrator")
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=MEMBER_CHANGED,
            target={"membership_id": str(membership_id)},
            before=None,
            # The binding itself is recorded (C18): who was bound to which identifier.
            after={
                **found[0].as_json(),
                "identity": {"identifier": request.identifier, "email": request.email},
            },
            reason=request.reason,
        )
    return membership_id


# --- administrator capability -------------------------------------------------------------


async def _target(
    session: AsyncSession, viewer: AuthorizationContext, membership_id: uuid.UUID
) -> tuple[AccessSnapshot, object]:
    found = await access_snapshot(session, viewer.organization_id, membership_id)
    if found is None:
        raise refusal("member_not_found")
    if membership_id == viewer.membership_id:
        raise refusal("own_access")
    return found


async def preview_administrator(
    session: AsyncSession,
    viewer: AuthorizationContext,
    membership_id: uuid.UUID,
    administrator: bool,
) -> AdministratorPreview:
    """The same refusals as the change (without the lock: the change re-checks)."""
    before, _ = await _target(session, viewer, membership_id)
    if before.status == "removed":
        raise refusal("member_removed")
    if (
        before.is_admin
        and not administrator
        and before.status == "active"
        and await active_administrators(session, viewer.organization_id) <= 1
    ):
        raise refusal("last_administrator")
    return AdministratorPreview(now=before.is_admin, after=administrator)


async def set_administrator(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    membership_id: uuid.UUID,
    request: AdministratorRequest,
) -> None:
    async with fenced(), db.tenant_transaction(organization_id) as session:
        if membership_id == actor_membership_id:
            raise refusal("own_access")
        # Lock order everywhere: the administrator set, then the membership row.
        await lock_administrator_set(session, organization_id)
        await _lock_member(session, organization_id, membership_id)
        found = await access_snapshot(session, organization_id, membership_id)
        if found is None:
            raise refusal("member_not_found")
        before, version = found
        if version != request.version:
            raise LatticeError(ErrorCode.CONFLICT)
        if before.is_admin == request.administrator:
            raise _invalid("change", "no_change")
        changed: bool = (
            await session.execute(
                text("SELECT lattice_set_admin(:actor, :m, :a)"),
                {"actor": actor_membership_id, "m": membership_id, "a": request.administrator},
            )
        ).scalar_one()
        if not changed:  # pragma: no cover - checked under the lock above
            raise _invalid("change", "no_change")
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=MEMBER_CHANGED,
            target={"membership_id": str(membership_id)},
            before=before.as_json(),
            after=replace(before, is_admin=request.administrator).as_json(),
            reason=request.reason,
        )


# --- reinstatement ---------------------------------------------------------------------------


def least_access() -> AccessSnapshot:
    """What a reinstated member starts with (D-36): the D-20 defaults, nothing assigned."""
    return AccessSnapshot(
        status="active",
        clearance="internal",
        account_scope="assigned",
        accounts=frozenset(),
        departments=frozenset(),
        groups=frozenset(),
        is_admin=False,
    )


async def preview_reinstatement(
    session: AsyncSession, viewer: AuthorizationContext, membership_id: uuid.UUID
) -> AccessPreview:
    org = viewer.organization_id
    before, _ = await _target(session, viewer, membership_id)
    if before.status != "removed":
        raise refusal("not_removed")
    blocked = await reinstate_block(session, org, membership_id)
    if blocked is not None:
        raise refusal(blocked)
    after = least_access()
    current = await resolve_member_context(
        session, organization_id=org, membership_id=membership_id
    )
    if current is None:
        raise refusal("member_not_found")
    names = await entity_names(session, org, before.accounts | before.departments | before.groups)
    gained, lost = await document_delta(
        session, viewer, current, context_after(current, before, after)
    )
    return AccessPreview(
        effects=effects(before, after, names),
        documents_gained=gained,
        documents_lost=lost,
    )


async def reinstate_block(
    session: AsyncSession, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> str | None:
    """Why a removed member cannot be reinstated now (the function's own checks, read
    without locks for the page), or None."""
    row = (
        await session.execute(
            text(
                "SELECT u.status AS user_status, u.email, EXISTS ("
                "  SELECT 1 FROM directory_identities d JOIN oidc_connections c"
                "    ON c.organization_id = d.organization_id AND c.id = d.connection_id"
                "   AND c.status = 'active'"
                "  WHERE d.organization_id = m.organization_id AND d.membership_id = m.id"
                ") AS current_identity, EXISTS ("
                "  SELECT 1 FROM memberships o JOIN users ou ON ou.id = o.user_id"
                "  WHERE o.organization_id = m.organization_id AND o.id <> m.id"
                "    AND lower(ou.email) = lower(u.email)"
                ") AS email_taken "
                "FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.organization_id = :o AND m.id = :m"
            ),
            {"o": organization_id, "m": membership_id},
        )
    ).one_or_none()
    if row is None:
        return "member_not_found"
    if not row.current_identity:
        return "identity_not_current"
    if row.user_status != "active":
        return "user_inactive"
    if row.email_taken:
        return "email_taken"
    return None


async def reinstate_member(
    db: Database,
    *,
    organization_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    membership_id: uuid.UUID,
    request: ReinstateRequest,
) -> None:
    async with fenced(), db.tenant_transaction(organization_id) as session:
        if membership_id == actor_membership_id:
            raise refusal("own_access")
        found = await access_snapshot(session, organization_id, membership_id)
        if found is None:
            raise refusal("member_not_found")
        before, version = found
        if version != request.version:
            raise LatticeError(ErrorCode.CONFLICT)
        if before.status != "removed":
            raise refusal("not_removed")
        await session.execute(
            text("SELECT lattice_reinstate_member(:actor, :m)"),
            {"actor": actor_membership_id, "m": membership_id},
        )
        metrics.member_changed(before.status, "active", "administrator")
        await record_event(
            session,
            organization_id,
            actor=f"member:{actor_membership_id}",
            action=MEMBER_CHANGED,
            target={"membership_id": str(membership_id)},
            before=before.as_json(),
            after=least_access().as_json(),
            reason=request.reason,
        )
