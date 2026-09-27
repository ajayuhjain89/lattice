"""Operator seed: organizations, departments, accounts, users, memberships and
admin-defined access groups from a YAML manifest (e.g. data/synthetic/halden/org.yaml).

* Runs as lattice_owner (operator tool), but tenant rows are still written through
  row-level security: the organization is bound before any tenant table is touched.
* Idempotent and deterministic: upserts by natural keys (organization slug, department,
  account and access-group keys, user email). Re-running an unchanged manifest writes
  nothing. Department and account assignments, and the members of each listed group,
  are made to match the manifest exactly. Entities absent from the manifest are left
  alone (never deleted).
* Access groups (``origin = 'admin'``) are identified by their stable key (D-9):
  renaming a group in the manifest updates its name in place; a different key is a
  different group, and the group under the old key is left untouched with its members
  and grants -- removing it is a deliberate operator action, not a side effect.
  Documents and grants are not seeded: they arrive through ingestion (Phase 4).
* Creates development identities (issuer ``dev``, subject = email) so the personas can
  use the development login. Refuses to run when LATTICE_ENV=production.
"""

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import ColumnElement, Table, and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from lattice.authz import AccessGroup, AccessGroupMember
from lattice.db.engine import Database, bind_organization
from lattice.identity.dev_provider import DEV_ISSUER, normalize_subject
from lattice.identity.models import ExternalIdentity, User
from lattice.orgs.models import (
    Account,
    AccountAssignment,
    Department,
    Membership,
    MembershipDepartment,
    Organization,
)

_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_key(value: str) -> str:
    if not _KEY.fullmatch(value):
        raise ValueError("must be lowercase letters, digits and hyphens (max 63)")
    return value


class OrganizationSpec(_Strict):
    slug: str
    name: str = Field(min_length=1)
    status: Literal["active", "suspended"] = "active"

    _slug = field_validator("slug")(_check_key)


class DepartmentSpec(_Strict):
    key: str
    name: str = Field(min_length=1)

    _key = field_validator("key")(_check_key)


class AccountSpec(_Strict):
    key: str
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    status: Literal["active", "churned", "prospect"] = "active"

    _key = field_validator("key")(_check_key)


class MemberSpec(_Strict):
    email: str
    display_name: str = Field(min_length=1)
    role_label: str | None = None
    status: Literal["active", "suspended", "removed"] = "active"
    is_admin: bool = False
    clearance: Literal["internal", "confidential"] = "internal"
    account_scope: Literal["all", "assigned"] = "assigned"
    departments: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = normalize_subject(value)
        if not _EMAIL.fullmatch(value):
            raise ValueError("must be an email address")
        return value

    @model_validator(mode="after")
    def _assignments_need_assigned_scope(self) -> Self:
        if self.account_scope == "all" and self.accounts:
            raise ValueError("account assignments are only meaningful with account_scope=assigned")
        return self


class GroupSpec(_Strict):
    """An admin-defined access group, e.g. "Exec staff". Source-synced groups (private
    channels) come from their connector, not from the manifest."""

    key: str
    name: str = Field(min_length=1)
    members: list[str] = Field(default_factory=list)

    _key = field_validator("key")(_check_key)

    @field_validator("members")
    @classmethod
    def _emails(cls, value: list[str]) -> list[str]:
        return [normalize_subject(v) for v in value]


class Manifest(_Strict):
    organization: OrganizationSpec
    departments: list[DepartmentSpec] = Field(default_factory=list)
    accounts: list[AccountSpec] = Field(default_factory=list)
    members: list[MemberSpec] = Field(default_factory=list)
    groups: list[GroupSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        for label, keys in (
            ("department", [d.key for d in self.departments]),
            ("account", [a.key for a in self.accounts]),
            ("member email", [m.email for m in self.members]),
            ("group", [g.key for g in self.groups]),
        ):
            dupes = [k for k, n in Counter(keys).items() if n > 1]
            if dupes:
                raise ValueError(f"duplicate {label}: {', '.join(sorted(dupes))}")
        departments = {d.key for d in self.departments}
        accounts = {a.key for a in self.accounts}
        for member in self.members:
            if missing := set(member.departments) - departments:
                raise ValueError(f"{member.email}: unknown departments {sorted(missing)}")
            if missing := set(member.accounts) - accounts:
                raise ValueError(f"{member.email}: unknown accounts {sorted(missing)}")
        emails = {m.email for m in self.members}
        for group in self.groups:
            if missing := set(group.members) - emails:
                raise ValueError(f"group {group.key}: unknown members {sorted(missing)}")
            if len(set(group.members)) != len(group.members):
                raise ValueError(f"group {group.key}: duplicate members")
        return self


def load_manifest(path: Path) -> Manifest:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Manifest.model_validate(raw)


@dataclass
class SeedReport:
    organization: str
    counts: Counter[str] = field(default_factory=Counter)

    def note(self, entity: str, changed: bool) -> None:
        self.counts[f"{entity}.{'written' if changed else 'unchanged'}"] += 1


def _table(model: type[DeclarativeBase]) -> Table:
    table = model.__table__
    assert isinstance(table, Table)  # noqa: S101 - declarative models map to Tables
    return table


async def _upsert(
    session: AsyncSession,
    table: Table,
    values: dict[str, Any],
    conflict: list[str],
    update_columns: list[str],
) -> bool:
    """INSERT ... ON CONFLICT DO UPDATE only when a column actually differs.

    Returns True if a row was inserted or changed.
    """
    stmt = insert(table).values(**values)
    changed: ColumnElement[bool] | None = None
    if update_columns:
        conditions = [table.c[c].is_distinct_from(stmt.excluded[c]) for c in update_columns]
        changed = or_(*conditions)
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict,
            set_={c: stmt.excluded[c] for c in update_columns},
            where=changed,
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict)
    result = await session.execute(stmt.returning(table.c[conflict[0]]))
    return result.first() is not None


async def _id_of(session: AsyncSession, table: Table, **keys: Any) -> uuid.UUID:
    criteria = [table.c[k] == v for k, v in keys.items()]
    value: object = (await session.execute(select(table.c.id).where(and_(*criteria)))).scalar_one()
    assert isinstance(value, uuid.UUID)  # noqa: S101 - id columns are UUIDs
    return value


async def _sync_links(
    session: AsyncSession,
    table: Table,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    column: str,
    wanted: set[uuid.UUID],
) -> bool:
    current: set[uuid.UUID] = set(
        (
            await session.execute(
                select(table.c[column]).where(table.c.membership_id == membership_id)
            )
        ).scalars()
    )
    if current == wanted:
        return False
    if stale := current - wanted:
        await session.execute(
            delete(table).where(table.c.membership_id == membership_id, table.c[column].in_(stale))
        )
    for target in wanted - current:
        await session.execute(
            insert(table).values(
                organization_id=organization_id, membership_id=membership_id, **{column: target}
            )
        )
    return True


async def seed_manifest(db: Database, manifest: Manifest) -> SeedReport:
    spec = manifest.organization
    report = SeedReport(organization=spec.slug)
    orgs = _table(Organization)
    departments = _table(Department)
    accounts = _table(Account)
    users = _table(User)
    identities = _table(ExternalIdentity)
    memberships = _table(Membership)
    member_departments = _table(MembershipDepartment)
    assignments = _table(AccountAssignment)
    groups = _table(AccessGroup)
    group_members = _table(AccessGroupMember)

    async with db.unscoped_transaction() as session:
        # Organizations: owner_provisioning policy lets the operator find/create tenants.
        existing = (
            await session.execute(select(orgs.c.id).where(orgs.c.slug == spec.slug))
        ).scalar_one_or_none()
        organization_id = existing if isinstance(existing, uuid.UUID) else uuid.uuid4()
        # Bind first: every tenant write below is checked by RLS against this org.
        await bind_organization(session, organization_id)
        report.note(
            "organization",
            await _upsert(
                session,
                orgs,
                {
                    "id": organization_id,
                    "slug": spec.slug,
                    "name": spec.name,
                    "status": spec.status,
                },
                ["id"],
                ["name", "status"],
            ),
        )

        department_ids: dict[str, uuid.UUID] = {}
        for dept in manifest.departments:
            report.note(
                "department",
                await _upsert(
                    session,
                    departments,
                    {"organization_id": organization_id, "key": dept.key, "name": dept.name},
                    ["organization_id", "key"],
                    ["name"],
                ),
            )
            department_ids[dept.key] = await _id_of(
                session, departments, organization_id=organization_id, key=dept.key
            )

        account_ids: dict[str, uuid.UUID] = {}
        for acct in manifest.accounts:
            report.note(
                "account",
                await _upsert(
                    session,
                    accounts,
                    {
                        "organization_id": organization_id,
                        "key": acct.key,
                        "name": acct.name,
                        "aliases": acct.aliases,
                        "status": acct.status,
                    },
                    ["organization_id", "key"],
                    ["name", "aliases", "status"],
                ),
            )
            account_ids[acct.key] = await _id_of(
                session, accounts, organization_id=organization_id, key=acct.key
            )

        membership_ids: dict[str, uuid.UUID] = {}
        for member in manifest.members:
            report.note(
                "user",
                await _upsert(
                    session,
                    users,
                    {"email": member.email, "display_name": member.display_name},
                    ["email"],
                    ["display_name"],
                ),
            )
            user_id = await _id_of(session, users, email=member.email)
            report.note(
                "dev_identity",
                await _upsert(
                    session,
                    identities,
                    {"user_id": user_id, "issuer": DEV_ISSUER, "subject": member.email},
                    ["issuer", "subject"],
                    [],
                ),
            )
            access_columns = ("status", "is_admin", "clearance", "account_scope")
            wanted_access = tuple(getattr(member, c) for c in access_columns)
            before = (
                await session.execute(
                    select(*(memberships.c[c] for c in access_columns)).where(
                        memberships.c.organization_id == organization_id,
                        memberships.c.user_id == user_id,
                    )
                )
            ).first()
            membership_changed = await _upsert(
                session,
                memberships,
                {
                    "organization_id": organization_id,
                    "user_id": user_id,
                    "role_label": member.role_label,
                    **{c: getattr(member, c) for c in access_columns},
                },
                ["organization_id", "user_id"],
                [*access_columns, "role_label"],
            )
            membership_id = await _id_of(
                session, memberships, organization_id=organization_id, user_id=user_id
            )
            membership_ids[member.email] = membership_id
            links_changed = await _sync_links(
                session,
                member_departments,
                organization_id,
                membership_id,
                "department_id",
                {department_ids[k] for k in member.departments},
            )
            links_changed |= await _sync_links(
                session,
                assignments,
                organization_id,
                membership_id,
                "account_id",
                {account_ids[k] for k in member.accounts},
            )
            access_changed = before is not None and (
                tuple(before) != wanted_access or links_changed
            )
            if access_changed:
                # Record access-affecting changes (docs/data-model.md §2, memberships).
                await session.execute(
                    memberships.update()
                    .where(memberships.c.id == membership_id)
                    .values(authz_updated_at=func.now())
                )
            report.note("membership", membership_changed or links_changed)

        for group in manifest.groups:
            group_changed = await _upsert(
                session,
                groups,
                {
                    "organization_id": organization_id,
                    "key": group.key,
                    "origin": "admin",
                    "name": group.name,
                },
                ["organization_id", "key"],
                ["name"],
            )
            group_id, origin = (
                await session.execute(
                    select(groups.c.id, groups.c.origin).where(
                        groups.c.organization_id == organization_id, groups.c.key == group.key
                    )
                )
            ).one()
            if origin != "admin":
                # Never take over a group mirrored from a source system.
                raise ValueError(f"group {group.key!r} exists and is not admin-defined")
            affected = await _sync_group_members(
                session,
                group_members,
                organization_id,
                group_id,
                {membership_ids[email] for email in group.members},
            )
            if affected:
                await session.execute(
                    memberships.update()
                    .where(memberships.c.id.in_(affected))
                    .values(authz_updated_at=func.now())
                )
            report.note("group", group_changed or bool(affected))
    return report


async def _sync_group_members(
    session: AsyncSession,
    table: Table,
    organization_id: uuid.UUID,
    group_id: uuid.UUID,
    wanted: set[uuid.UUID],
) -> set[uuid.UUID]:
    """Make the group's members exactly ``wanted``; return the memberships whose group
    membership changed (their access changed)."""
    current: set[uuid.UUID] = set(
        (
            await session.execute(select(table.c.membership_id).where(table.c.group_id == group_id))
        ).scalars()
    )
    if stale := current - wanted:
        await session.execute(
            delete(table).where(table.c.group_id == group_id, table.c.membership_id.in_(stale))
        )
    for membership_id in wanted - current:
        await session.execute(
            insert(table).values(
                organization_id=organization_id, group_id=group_id, membership_id=membership_id
            )
        )
    return stale | (wanted - current)
