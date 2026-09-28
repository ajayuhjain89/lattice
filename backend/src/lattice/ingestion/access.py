"""MAP ACCESS (docs/ingestion-design.md §1 step 3, §6): a connector's untrusted access
hints become a document's access record, through the source's Lattice configuration,
failing closed.

This module decides *what the stored access metadata is*; it never decides who may
read a document -- that is ``lattice.authz`` over the stored record. The rules mirror
the dataset's expected-access oracle (``lattice.dataset.access``); a contract test
compares the two record by record, persona by persona.

Rules:

* sensitivity: the record's own (document front matter), else the channel's configured
  sensitivity (chat), else the source default. ``restricted`` always means
  ``grants_only``.
* access mode: ``grants_only`` if the source says so (a private channel, a front-matter
  flag) or the content is restricted; otherwise ``policy``.
* departments: the record's department keys, else the source's default departments.
  An unknown key fails the record (``access_unmappable:department``) -- never dropped,
  because dropping a restriction widens access.
* account: a document names an account key, which must exist; a ticket names the
  ticketing organization, mapped by ``account_refs``; a chat record names its channel,
  mapped by ``channels``. On an account-scoped source an unmapped reference fails the
  record (``access_unmappable:account``); a reference that claims an account that does
  not exist always fails it. Never "no account", which would widen access.
* source grants: department and admin-group grants by key (unknown ->
  ``access_unmappable:department`` / ``:group``); groups the connector declared
  (private channels) by external reference (undeclared -> ``access_unmappable:group``).

Nothing here defaults to ``internal``/everyone on missing or unmappable data.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field

from lattice.connectors.contract import SourceRecord
from lattice.sources.config import SourceConfig

LEVELS = ("internal", "confidential", "restricted")


@dataclass(frozen=True)
class OrgDirectory:
    """The organization's identifiers, by key, as ingestion needs them."""

    departments: dict[str, uuid.UUID]
    accounts: dict[str, uuid.UUID]
    admin_groups: dict[str, uuid.UUID]
    # External reference (e.g. a private channel ID) -> the source-synced group.
    source_groups: dict[str, uuid.UUID] = field(default_factory=dict)


@dataclass(frozen=True)
class MappedAccess:
    sensitivity: str
    access_mode: str
    account_id: uuid.UUID | None
    department_ids: tuple[uuid.UUID, ...]  # sorted
    source_grants: tuple[tuple[str, uuid.UUID], ...]  # (principal_type, id), sorted

    @property
    def access_hash(self) -> bytes:
        """SHA-256 of the normalized access metadata (data-model §4). Admin grants are
        not included: they are Lattice configuration, applied separately (D-12)."""
        canonical = {
            "sensitivity": self.sensitivity,
            "access_mode": self.access_mode,
            "account_id": str(self.account_id) if self.account_id else None,
            "department_ids": [str(d) for d in self.department_ids],
            "source_grants": [[t, str(p)] for t, p in self.source_grants],
        }
        return hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).digest()


@dataclass(frozen=True)
class AccessFailure:
    code: str  # access_unmappable:<what>
    params: dict[str, str | int] = field(default_factory=dict)


def map_access(
    record: SourceRecord, config: SourceConfig, directory: OrgDirectory
) -> MappedAccess | AccessFailure:
    hints = record.access
    channel = (
        config.channels.get(record.account_ref)
        if config.connector_type == "chat_export" and record.account_ref
        else None
    )

    sensitivity = hints.sensitivity or (channel.sensitivity if channel else None)
    sensitivity = sensitivity or config.default_sensitivity
    if sensitivity not in LEVELS:
        return AccessFailure("access_unmappable:sensitivity")
    grants_only = hints.grants_only or sensitivity == "restricted"

    department_keys = hints.department_keys or tuple(config.default_departments)
    unknown = [k for k in department_keys if k not in directory.departments]
    if unknown:
        return AccessFailure("access_unmappable:department")
    department_ids = tuple(sorted({directory.departments[k] for k in department_keys}))

    account = _account(
        record, config, directory, channel_account=channel.account if channel else None
    )
    if isinstance(account, AccessFailure):
        return account

    grants: set[tuple[str, uuid.UUID]] = set()
    for key in hints.department_grant_keys:
        if key not in directory.departments:
            return AccessFailure("access_unmappable:department")
        grants.add(("department", directory.departments[key]))
    for key in hints.group_grant_keys:
        if key not in directory.admin_groups:
            return AccessFailure("access_unmappable:group")
        grants.add(("group", directory.admin_groups[key]))
    for ref in hints.group_refs:
        if ref not in directory.source_groups:
            return AccessFailure("access_unmappable:group")
        grants.add(("group", directory.source_groups[ref]))

    return MappedAccess(
        sensitivity=sensitivity,
        access_mode="grants_only" if grants_only else "policy",
        account_id=account,
        department_ids=department_ids,
        source_grants=tuple(sorted(grants, key=lambda g: (g[0], str(g[1])))),
    )


def _account(
    record: SourceRecord,
    config: SourceConfig,
    directory: OrgDirectory,
    *,
    channel_account: str | None,
) -> uuid.UUID | AccessFailure | None:
    ref = record.account_ref
    if config.connector_type == "document_repository":
        key = ref
    elif config.connector_type == "ticketing":
        key = config.account_refs.get(int(ref)) if ref and ref.isdigit() else None
    else:
        key = channel_account
    if key is None:
        if config.account_scoped:
            params: dict[str, str | int] = {}
            if ref and config.connector_type != "document_repository":
                # The unmapped *external* reference: mapping it is the admin's task
                # (security-model §10). Never the record's title or content.
                params["external_account_ref"] = ref
            return AccessFailure("access_unmappable:account", params)
        return None
    if key not in directory.accounts:
        return AccessFailure("access_unmappable:account")
    return directory.accounts[key]
