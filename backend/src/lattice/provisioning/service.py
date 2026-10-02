"""SCIM provisioning: create, update, deactivate, reactivate and remove members on the
identity provider's instruction (Phase 10; D-20, ADR-015).

Boundaries, each enforced in code *and* by the database (migration 0013):

* **One organization, one connection.** The token resolves (pre-authentication lookup)
  to its organization and connection; every statement then runs bound to that
  organization under row-level security and filtered by the connection. Another
  organization's resources are unknown (404), never listed.
* **Owned users only.** A provisioned person is a new user owned by the organization
  (``home_organization_id``). An e-mail already used by a member of this organization is
  a uniqueness conflict (409); users of *other* organizations are invisible, so they can
  neither collide nor be found, linked or modified.
* **Least access.** A new membership gets the column defaults: not an administrator,
  clearance ``internal``, account scope ``assigned`` with no accounts, no departments,
  groups or grants. SCIM never writes access attributes.
* **Lifecycle.** ``active=false`` -> ``suspended``, ``active=true`` -> ``active``,
  ``DELETE`` -> ``removed`` (terminal). Suspension and removal revoke the member's
  sessions in the same transaction; authorization already refuses a non-active member
  on the next request (SEC-4).
* **Idempotent.** A request that changes nothing writes nothing and records nothing;
  ``DELETE`` of a removed member is 204 again. ``If-Match`` against ``meta.version``.
* **Audited.** ``access.member_changed`` (creation, status) and
  ``access.identity_changed`` (user name, e-mail, display name), actor
  ``scim:<token id>``, in the transaction of the change -- which the database requires.
"""

import uuid
from dataclasses import dataclass, replace
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.audit import IDENTITY_CHANGED, MEMBER_CHANGED, record_event
from lattice.access.directory import access_snapshot
from lattice.db.engine import Database
from lattice.provisioning.scim import (
    MAX_RESULTS,
    NOT_FOUND,
    ScimError,
    StoredUser,
    UserState,
    apply_patch,
    parse_user,
    version_tag,
)
from lattice.provisioning.tokens import hash_token, is_well_formed

_RESOLVE_SQL = text("SELECT * FROM lattice_resolve_scim_token(:h)")

_USER_COLUMNS = """
    d.id, d.external_id, d.user_name, d.given_name, d.family_name, d.version,
    d.created_at, d.updated_at, d.membership_id, m.status, u.id AS user_id, u.email,
    u.display_name
"""
_FROM = """
    FROM directory_identities d
    JOIN memberships m ON m.organization_id = d.organization_id AND m.id = d.membership_id
    JOIN users u ON u.id = m.user_id
    WHERE d.organization_id = :o AND d.connection_id = :c
"""

REASONS: Final = {
    "created": "Provisioned by the identity provider (SCIM)",
    "updated": "Updated by the identity provider (SCIM)",
    "suspended": "Deactivated by the identity provider (SCIM)",
    "active": "Reactivated by the identity provider (SCIM)",
    "removed": "Removed by the identity provider (SCIM)",
}


@dataclass(frozen=True)
class ScimCaller:
    token_id: uuid.UUID
    organization_id: uuid.UUID
    connection_id: uuid.UUID

    @property
    def actor(self) -> str:
        return f"scim:{self.token_id}"


async def resolve_token(db: Database, token: str) -> ScimCaller | None:
    """The token's organization and connection, if it is well formed, not revoked, and
    its connection and organization are active; None otherwise (one outcome)."""
    if not is_well_formed(token):
        return None
    async with db.unscoped_transaction() as session:
        row = (await session.execute(_RESOLVE_SQL, {"h": hash_token(token)})).one_or_none()
    if row is None:
        return None
    return ScimCaller(
        token_id=row.token_id, organization_id=row.organization_id, connection_id=row.connection_id
    )


@dataclass(frozen=True)
class _Row:
    stored: StoredUser
    membership_id: uuid.UUID
    user_id: uuid.UUID
    status: str


def _row(found: Any) -> _Row:
    return _Row(
        stored=StoredUser(
            id=str(found.id),
            state=UserState(
                external_id=found.external_id,
                user_name=found.user_name,
                email=str(found.email),
                display_name=found.display_name,
                given_name=found.given_name,
                family_name=found.family_name,
                active=found.status == "active",
            ),
            version=found.version,
            created=found.created_at,
            last_modified=found.updated_at,
        ),
        membership_id=found.membership_id,
        user_id=found.user_id,
        status=found.status,
    )


def _resource_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ScimError(404, NOT_FOUND) from None


def _check_version(if_match: str | None, version: int) -> None:
    if if_match is None or if_match.strip() == "*":
        return
    tags = {t.strip() for t in if_match.split(",")}
    if version_tag(version) not in tags and f'"{version}"' not in tags:
        raise ScimError(412, "The resource changed since the version you sent.")


def _conflict() -> ScimError:
    return ScimError(409, "A user with this externalId, userName or e-mail exists.", "uniqueness")


def _unique_violation(exc: IntegrityError) -> bool:
    """Only a unique violation (a concurrent create) is a conflict; anything else -- such
    as the database refusing an unaudited access write -- is a bug and stays an error."""
    orig = exc.orig
    states = (
        getattr(orig, "sqlstate", None),
        getattr(orig, "pgcode", None),
        getattr(getattr(orig, "__cause__", None), "sqlstate", None),
    )
    return "23505" in states


class Provisioning:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- reads ------------------------------------------------------------------------

    async def _load(
        self, session: AsyncSession, caller: ScimCaller, resource_id: uuid.UUID, *, lock: bool
    ) -> _Row | None:
        found = (
            await session.execute(
                text(
                    f"SELECT {_USER_COLUMNS} {_FROM} AND d.id = :id"
                    + (" FOR UPDATE OF d, m" if lock else "")
                ),
                {"o": caller.organization_id, "c": caller.connection_id, "id": resource_id},
            )
        ).one_or_none()
        return None if found is None else _row(found)

    async def get_user(self, caller: ScimCaller, resource_id: str) -> StoredUser:
        rid = _resource_id(resource_id)
        async with self._db.tenant_snapshot(caller.organization_id) as session:
            found = await self._load(session, caller, rid, lock=False)
        if found is None or found.status == "removed":
            raise ScimError(404, NOT_FOUND)
        return found.stored

    async def list_users(
        self,
        caller: ScimCaller,
        filter_: tuple[str, str] | None,
        start_index: int,
        count: int,
    ) -> tuple[int, list[StoredUser]]:
        condition = ""
        params: dict[str, Any] = {"o": caller.organization_id, "c": caller.connection_id}
        if filter_ is not None:
            attribute, value = filter_
            condition = (
                " AND d.external_id = :v"
                if attribute == "externalId"
                else " AND lower(d.user_name) = lower(:v)"
            )
            params["v"] = value
        where = f"{_FROM} AND m.status <> 'removed'{condition}"
        async with self._db.tenant_snapshot(caller.organization_id) as session:
            total = int(
                (await session.execute(text(f"SELECT count(*) {where}"), params)).scalar_one()
            )
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_USER_COLUMNS} {where} "
                        "ORDER BY d.created_at, d.id OFFSET :skip LIMIT :limit"
                    ),
                    {**params, "skip": max(start_index, 1) - 1, "limit": min(count, MAX_RESULTS)},
                )
            ).all()
        return total, [_row(r).stored for r in rows]

    # --- writes -----------------------------------------------------------------------

    async def _unique(
        self,
        session: AsyncSession,
        caller: ScimCaller,
        state: UserState,
        *,
        resource_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
    ) -> None:
        """Uniqueness inside this organization only (row-level security hides every
        other organization's users, so they can neither collide nor be revealed)."""
        clashes: bool = (
            await session.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM directory_identities WHERE connection_id = :c "
                    "AND id IS DISTINCT FROM :rid AND (external_id = :x "
                    "OR lower(user_name) = lower(:n))) "
                    "OR EXISTS (SELECT 1 FROM users WHERE email = :e "
                    "AND id IS DISTINCT FROM :uid)"
                ),
                {
                    "c": caller.connection_id,
                    "rid": resource_id,
                    "x": state.external_id,
                    "n": state.user_name,
                    "e": state.email,
                    "uid": user_id,
                },
            )
        ).scalar_one()
        if clashes:
            raise _conflict()

    async def create_user(self, caller: ScimCaller, resource: Any) -> StoredUser:
        state = parse_user(resource)
        org = caller.organization_id
        try:
            async with self._db.tenant_transaction(org) as session:
                await self._unique(session, caller, state, resource_id=None, user_id=None)
                user_id: uuid.UUID = (
                    await session.execute(
                        text(
                            "INSERT INTO users (email, display_name, home_organization_id) "
                            "VALUES (:e, :n, :o) RETURNING id"
                        ),
                        {"e": state.email, "n": state.display_name, "o": org},
                    )
                ).scalar_one()
                status = "active" if state.active else "suspended"
                membership_id: uuid.UUID = (
                    await session.execute(
                        text(
                            "INSERT INTO memberships (organization_id, user_id, status) "
                            "VALUES (:o, :u, :s) RETURNING id"
                        ),
                        {"o": org, "u": user_id, "s": status},
                    )
                ).scalar_one()
                resource_id: uuid.UUID = (
                    await session.execute(
                        text(
                            "INSERT INTO directory_identities (organization_id, connection_id, "
                            "membership_id, external_id, user_name, given_name, family_name) "
                            "VALUES (:o, :c, :m, :x, :n, :g, :f) RETURNING id"
                        ),
                        {
                            "o": org,
                            "c": caller.connection_id,
                            "m": membership_id,
                            "x": state.external_id,
                            "n": state.user_name,
                            "g": state.given_name,
                            "f": state.family_name,
                        },
                    )
                ).scalar_one()
                snapshot = await access_snapshot(session, org, membership_id)
                assert snapshot is not None  # noqa: S101 - inserted above
                await record_event(
                    session,
                    org,
                    actor=caller.actor,
                    action=MEMBER_CHANGED,
                    target={"membership_id": str(membership_id)},
                    before=None,
                    after=snapshot[0].as_json(),
                    reason=REASONS["created"],
                )
                found = await self._load(session, caller, resource_id, lock=False)
        except IntegrityError as exc:
            if not _unique_violation(exc):
                raise
            # A concurrent request created the same person first.
            raise _conflict() from None
        assert found is not None  # noqa: S101
        return found.stored

    async def _write(
        self,
        session: AsyncSession,
        caller: ScimCaller,
        current: _Row,
        new: UserState,
        status: str,
    ) -> bool:
        """Apply ``new`` and ``status`` to ``current``; False when nothing changed."""
        org = caller.organization_id
        old = current.stored.state
        identity_changed = (
            new.user_name,
            new.email,
            new.display_name,
            new.given_name,
            new.family_name,
        ) != (old.user_name, old.email, old.display_name, old.given_name, old.family_name)
        status_changed = status != current.status
        if not identity_changed and not status_changed:
            return False
        if identity_changed:
            await self._unique(
                session,
                caller,
                new,
                resource_id=uuid.UUID(current.stored.id),
                user_id=current.user_id,
            )
            await session.execute(
                text("UPDATE users SET email = :e, display_name = :n WHERE id = :u"),
                {"e": new.email, "n": new.display_name, "u": current.user_id},
            )
        await session.execute(
            text(
                "UPDATE directory_identities SET user_name = :n, given_name = :g, "
                "family_name = :f, version = version + 1 WHERE organization_id = :o AND id = :id"
            ),
            {
                "n": new.user_name,
                "g": new.given_name,
                "f": new.family_name,
                "o": org,
                "id": uuid.UUID(current.stored.id),
            },
        )
        target = {"membership_id": str(current.membership_id)}
        if identity_changed:
            await record_event(
                session,
                org,
                actor=caller.actor,
                action=IDENTITY_CHANGED,
                target=target,
                before={"user_name": old.user_name, "email": old.email, "name": old.display_name},
                after={"user_name": new.user_name, "email": new.email, "name": new.display_name},
                reason=REASONS["updated"],
            )
        if status_changed:
            found = await access_snapshot(session, org, current.membership_id)
            assert found is not None  # noqa: S101 - locked above
            before = found[0]
            await session.execute(
                text(
                    "UPDATE memberships SET status = :s, authz_updated_at = clock_timestamp() "
                    "WHERE organization_id = :o AND id = :m"
                ),
                {"s": status, "o": org, "m": current.membership_id},
            )
            if status != "active":
                await session.execute(
                    text(
                        "UPDATE sessions SET revoked_at = now() WHERE organization_id = :o "
                        "AND user_id = :u AND revoked_at IS NULL"
                    ),
                    {"o": org, "u": current.user_id},
                )
            await record_event(
                session,
                org,
                actor=caller.actor,
                action=MEMBER_CHANGED,
                target=target,
                before=before.as_json(),
                after=replace(before, status=status).as_json(),
                reason=REASONS[status],
            )
        return True

    async def _update(
        self,
        caller: ScimCaller,
        resource_id: str,
        if_match: str | None,
        change: Any,
    ) -> StoredUser:
        rid = _resource_id(resource_id)
        try:
            async with self._db.tenant_transaction(caller.organization_id) as session:
                current = await self._load(session, caller, rid, lock=True)
                if current is None or current.status == "removed":
                    raise ScimError(404, NOT_FOUND)
                _check_version(if_match, current.stored.version)
                new: UserState = change(current.stored.state)
                status = "active" if new.active else "suspended"
                if not await self._write(session, caller, current, new, status):
                    return current.stored
                found = await self._load(session, caller, rid, lock=False)
        except IntegrityError as exc:
            if not _unique_violation(exc):
                raise
            raise _conflict() from None
        assert found is not None  # noqa: S101
        return found.stored

    async def replace_user(
        self, caller: ScimCaller, resource_id: str, resource: Any, if_match: str | None
    ) -> StoredUser:
        return await self._update(
            caller, resource_id, if_match, lambda current: parse_user(resource, current=current)
        )

    async def patch_user(
        self, caller: ScimCaller, resource_id: str, body: Any, if_match: str | None
    ) -> StoredUser:
        return await self._update(
            caller, resource_id, if_match, lambda current: apply_patch(current, body)
        )

    async def delete_user(self, caller: ScimCaller, resource_id: str, if_match: str | None) -> None:
        rid = _resource_id(resource_id)
        async with self._db.tenant_transaction(caller.organization_id) as session:
            current = await self._load(session, caller, rid, lock=True)
            if current is None:
                # A removed membership cannot be locked for update (0013's policy fences
                # it), so look again without the lock: removed -> idempotent 204.
                seen = await self._load(session, caller, rid, lock=False)
                if seen is not None and seen.status == "removed":
                    return  # already removed, nothing recorded
                raise ScimError(404, NOT_FOUND)
            _check_version(if_match, current.stored.version)
            await self._write(session, caller, current, current.stored.state, "removed")
