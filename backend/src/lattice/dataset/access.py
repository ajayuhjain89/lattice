"""The expected-access oracle: who may read each synthetic record, decided by the real
reference policy (``lattice.authz.readable``) -- never by a re-implementation.

Raw fixtures only carry what a connector would report (front matter, a ticket's
organization, a channel's privacy and membership). This module maps those hints the way
``docs/connector-design.md`` and ``docs/ingestion-design.md`` §6 specify for Phase 4:

* documents: front-matter sensitivity (else the source default), departments,
  ``grants_only`` (implied by ``restricted``), grants to admin groups and departments by
  key, the account by key; plus admin grants made in Lattice (``admin-grants.yaml``);
* tickets: source defaults (``internal``, no department restriction); the account from
  the ticketing organization ID; an unmapped organization on an account-scoped source
  means the record is **not indexed** (fail closed);
* Slack: public channels use the source default (or the channel's configured
  sensitivity) and the channel's configured account; private channels are
  ``grants_only`` with a grant to the channel's group, whose members are the channel
  members that map to memberships by email.

It then builds each persona's ``AuthorizationContext`` from the organization manifest
(and the channel groups), and asks ``readable`` / ``account_visible`` /
``visible_document_account``. Identifiers are deterministic UUIDs derived from keys, so
the oracle is reproducible and needs no database. Phase 4 ingestion must reach the same
decisions; a contract test will compare them.

This is the dataset package's only module that reads access vocabulary (an explicit
allowance in ``tests/test_authz_boundary.py``).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from lattice.authz import (
    POLICY_VERSION,
    AccountRef,
    AuthorizationContext,
    DocumentAccess,
    GrantRef,
    account_visible,
    readable,
    visible_document_account,
)
from lattice.dataset.manifests import AdminGrantsFile, SourceConfig
from lattice.dataset.sources import ChannelInfo, FrontMatter, Record, SourceSet, Ticket
from lattice.seed import Manifest

_NAMESPACE = uuid.UUID("5f1c7a52-2b0e-4d4b-9a57-6c1f0d6e8a21")
_EPOCH = datetime.fromisoformat("2026-09-27T00:00:00+00:00")
_RANK = {"internal": 0, "confidential": 1, "restricted": 2}
EGRESS_LEVEL = {"default": "internal", "opt_in_confidential": "confidential"}


def stable_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, "/".join(parts))


@dataclass(frozen=True)
class Unmapped:
    """A record ingestion must refuse (``access_unmappable:<what>``)."""

    reason: str


@dataclass(frozen=True)
class ExpectedAccess:
    access: DocumentAccess
    account: str | None  # account key
    level: str  # sensitivity, for the AI egress policy


@dataclass
class OrgWorld:
    """One organization's identities and access state, as the oracle sees them."""

    slug: str
    manifest: Manifest
    sources: dict[str, SourceConfig]
    raw: SourceSet
    admin_grants: AdminGrantsFile
    org_id: uuid.UUID = field(init=False)
    personas: dict[str, str] = field(init=False)  # persona key -> email
    problems: list[str] = field(default_factory=list)
    _access: dict[str, ExpectedAccess | Unmapped] = field(default_factory=dict)
    _contexts: dict[str, AuthorizationContext] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.org_id = stable_id("org", self.slug)
        self.personas = {m.email.split(".")[0]: m.email for m in self.manifest.members}
        if len(self.personas) != len(self.manifest.members):
            self.problems.append("persona keys (email first names) are not unique")

    # identifiers -------------------------------------------------------------------------

    def department_id(self, key: str) -> uuid.UUID:
        return stable_id(self.slug, "department", key)

    def account_id(self, key: str) -> uuid.UUID:
        return stable_id(self.slug, "account", key)

    def membership_id(self, email: str) -> uuid.UUID:
        return stable_id(self.slug, "membership", email)

    def admin_group_id(self, key: str) -> uuid.UUID:
        return stable_id(self.slug, "group", key)

    def channel_group_id(self, channel_id: str) -> uuid.UUID:
        return stable_id(self.slug, "channel-group", channel_id)

    @property
    def department_keys(self) -> set[str]:
        return {d.key for d in self.manifest.departments}

    @property
    def account_keys(self) -> set[str]:
        return {a.key for a in self.manifest.accounts}

    @property
    def admin_group_keys(self) -> set[str]:
        return {g.key for g in self.manifest.groups}

    def channel_members(self, info: ChannelInfo) -> set[str]:
        """Emails of the channel's members that are memberships of this organization."""
        emails = {m.email for m in self.manifest.members}
        users = self.raw.slack_users
        return {
            users[u].profile.email
            for u in info.channel.members
            if u in users and users[u].profile.email in emails
        }

    # records -----------------------------------------------------------------------------

    def expected(self, record: Record) -> ExpectedAccess | Unmapped:
        if record.key not in self._access:
            self._access[record.key] = self._derive(record)
        return self._access[record.key]

    def _derive(self, record: Record) -> ExpectedAccess | Unmapped:
        source = self.sources[record.source]
        origin = record.origin
        account: str | None = None
        departments = list(source.default_departments)
        grants: list[GrantRef] = []
        grants_only = False
        level: str = source.default_sensitivity

        if isinstance(origin, FrontMatter):
            level = origin.sensitivity or level
            departments = origin.departments or departments
            grants_only = origin.grants_only or level == "restricted"
            account = origin.account
            for grant in origin.grants:
                kind, _, key = grant.partition(":")
                principal = self.admin_group_id(key) if kind == "group" else self.department_id(key)
                grants.append(GrantRef(self.org_id, kind, principal))
        elif isinstance(origin, Ticket):
            account = source.account_refs.get(origin.organization_id)
            if account is None and source.account_scoped:
                return Unmapped("access_unmappable:account")
        elif isinstance(origin, ChannelInfo):
            config = source.channels.get(origin.channel.id)
            if config is not None:
                account = config.account
                level = config.sensitivity or level
            if origin.private:
                grants_only = True
                grants.append(
                    GrantRef(self.org_id, "group", self.channel_group_id(origin.channel.id))
                )

        for admin in self.admin_grants.grants:
            if admin.document == record.key:
                grants.append(GrantRef(self.org_id, "membership", self.membership_id(admin.member)))
        doc = DocumentAccess(
            id=stable_id(self.slug, "record", record.key),
            organization_id=self.org_id,
            status="active",
            sensitivity=level,
            access_mode="grants_only" if grants_only else "policy",
            account_id=self.account_id(account) if account else None,
            department_ids=frozenset(self.department_id(k) for k in departments),
            grants=tuple(grants),
        )
        return ExpectedAccess(doc, account, level)

    # personas ----------------------------------------------------------------------------

    def context(self, persona: str) -> AuthorizationContext:
        if persona not in self._contexts:
            email = self.personas[persona]
            member = next(m for m in self.manifest.members if m.email == email)
            groups = {
                self.admin_group_id(g.key) for g in self.manifest.groups if email in g.members
            }
            groups |= {
                self.channel_group_id(cid)
                for cid, info in self.raw.slack_channels.items()
                if info.private and email in self.channel_members(info)
            }
            self._contexts[persona] = AuthorizationContext(
                organization_id=self.org_id,
                membership_id=self.membership_id(email),
                membership_status=member.status,
                clearance=member.clearance,
                account_scope=member.account_scope,
                assigned_account_ids=frozenset(self.account_id(k) for k in member.accounts),
                department_ids=frozenset(self.department_id(k) for k in member.departments),
                group_ids=frozenset(groups),
                authz_updated_at=_EPOCH,
                policy_version=POLICY_VERSION,
            )
        return self._contexts[persona]

    def can_read(self, persona: str, record: Record) -> bool:
        if record.organization != self.slug:
            return False  # another tenant's record: never in this persona's world
        expected = self.expected(record)
        return isinstance(expected, ExpectedAccess) and readable(
            self.context(persona), expected.access
        )

    def readers(self, record: Record) -> set[str]:
        return {p for p in self.personas if self.can_read(p, record)}

    def sees_account(self, persona: str, account: str) -> bool:
        return account in self.account_keys and account_visible(
            self.context(persona), AccountRef(self.account_id(account), self.org_id)
        )

    def shown_account(self, persona: str, record: Record) -> str | None:
        """The account key an evidence card may show for ``record`` (D-8)."""
        expected = self.expected(record)
        if not isinstance(expected, ExpectedAccess):
            return None
        shown = visible_document_account(self.context(persona), expected.access)
        return expected.account if shown is not None else None

    def level(self, record: Record) -> str | None:
        expected = self.expected(record)
        return expected.level if isinstance(expected, ExpectedAccess) else None

    def above_egress(self, record: Record, config: str) -> bool:
        level = self.level(record)
        return level is not None and _RANK[level] > _RANK[EGRESS_LEVEL[config]]

    def account_of(self, record: Record) -> str | None:
        expected = self.expected(record)
        return expected.account if isinstance(expected, ExpectedAccess) else None

    def is_grants_only_readable_by_grant_only(self, persona: str, record: Record) -> bool:
        """True when ``persona`` reads ``record`` only because of a grant."""
        expected = self.expected(record)
        if not isinstance(expected, ExpectedAccess) or not self.can_read(persona, record):
            return False
        without = DocumentAccess(
            id=expected.access.id,
            organization_id=expected.access.organization_id,
            status=expected.access.status,
            sensitivity=expected.access.sensitivity,
            access_mode=expected.access.access_mode,
            account_id=expected.access.account_id,
            department_ids=expected.access.department_ids,
        )
        return not readable(self.context(persona), without)
