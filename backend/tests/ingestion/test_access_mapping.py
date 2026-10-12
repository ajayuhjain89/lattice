"""MAP ACCESS fails closed (docs/ingestion-design.md §6).

Pure unit tests of ``lattice.ingestion.access.map_access``: every hint that cannot be
mapped fails the record with ``access_unmappable:<what>`` -- never a dropped restriction,
never "no account", never a default of ``internal``/everyone. The comparison against the
dataset oracle over every real record is in ``test_ingested_access.py``.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from lattice.connectors.contract import AccessHints, RecordPart, SourceRecord
from lattice.ingestion.access import AccessFailure, MappedAccess, OrgDirectory, map_access
from lattice.sources.config import SourceConfig

D_CS, D_LEAD = uuid.uuid4(), uuid.uuid4()
A_ACME, A_KESTREL = uuid.uuid4(), uuid.uuid4()
G_EXEC, G_CHANNEL = uuid.uuid4(), uuid.uuid4()
DIRECTORY = OrgDirectory(
    departments={"customer-success": D_CS, "leadership": D_LEAD},
    accounts={"acme-corp": A_ACME, "kestrel-bank": A_KESTREL},
    admin_groups={"exec-staff": G_EXEC},
    source_groups={"G0PRIVATE": G_CHANNEL},
)
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _config(connector_type: str, **overrides: Any) -> SourceConfig:
    values: dict[str, Any] = {
        "key": "src",
        "connector_type": connector_type,
        "name": "Source",
        "path": ".",
        "default_sensitivity": "internal",
        "authority": "working",
    }
    values.update(overrides)
    return SourceConfig.model_validate(values)


def _record(kind: str = "doc", account_ref: str | None = None, **hints: Any) -> SourceRecord:
    return SourceRecord(
        external_id="r1",
        kind=kind,
        title="Title",
        created_at=NOW,
        updated_at=NOW,
        account_ref=account_ref,
        access=AccessHints(**hints),
        parts=(RecordPart(anchor="1", text="text"),),
    )


DOCS = _config("document_repository")
TICKETS = _config("ticketing", account_scoped=True)
SLACK = _config("chat_export", channels={"G0ESC": {"sensitivity": "confidential"}})
# The synced source's account_external_refs (P12.3): ticketing organizations and channels.
TICKET_REFS = replace(DIRECTORY, account_refs={"360001001": A_ACME})
CHANNEL_REFS = replace(DIRECTORY, account_refs={"C0ACME": A_ACME})


def _ok(result: MappedAccess | AccessFailure) -> MappedAccess:
    assert isinstance(result, MappedAccess), result
    return result


# --- fail closed --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "record", "code"),
    [
        # An unknown department restriction is never dropped (that would widen access).
        (DOCS, _record(department_keys=("customer-success", "finance")), "department"),
        (DOCS, _record(department_grant_keys=("finance",)), "department"),
        (_config("document_repository", default_departments=["ghost"]), _record(), "department"),
        # An admin group grant naming a group that does not exist.
        (DOCS, _record(group_grant_keys=("board",)), "group"),
        # A group reference the connector never declared.
        (SLACK, _record("chat_thread", "C0OTHER", group_refs=("G0UNDECLARED",)), "group"),
        # A document claiming an account that does not exist: never "no account".
        (DOCS, _record(account_ref="globex"), "account"),
        # Account-scoped ticketing: unmapped organization, missing organization, junk,
        # and a reference that differs from a mapped one only in form (exact match).
        (TICKETS, _record("ticket", "360009913"), "account"),
        (TICKETS, _record("ticket", None), "account"),
        (TICKETS, _record("ticket", "not-a-number"), "account"),
        (TICKETS, _record("ticket", "0360001001"), "account"),
        (
            _config("chat_export", account_scoped=True),
            _record("chat_thread", "C0UNMAPPED"),
            "account",
        ),
        (_config("chat_export", account_scoped=True), _record("chat_thread", "c0acme"), "account"),
    ],
)
def test_unmappable_access_fails_the_record(
    config: SourceConfig, record: SourceRecord, code: str
) -> None:
    directory = TICKET_REFS if config.connector_type == "ticketing" else CHANNEL_REFS
    result = map_access(record, config, directory)
    assert isinstance(result, AccessFailure)
    assert result.code == f"access_unmappable:{code}"


def test_an_unmapped_ticket_reports_only_the_external_reference() -> None:
    result = map_access(_record("ticket", "360009913"), TICKETS, TICKET_REFS)
    assert isinstance(result, AccessFailure)
    assert result.params == {"external_account_ref": "360009913"}


# --- mapping ------------------------------------------------------------------------------


def test_restricted_is_always_grants_only() -> None:
    access = _ok(map_access(_record(sensitivity="restricted"), DOCS, DIRECTORY))
    assert (access.sensitivity, access.access_mode) == ("restricted", "grants_only")


def test_document_hints_map_through_the_directory() -> None:
    access = _ok(
        map_access(
            _record(
                account_ref="kestrel-bank",
                sensitivity="confidential",
                department_keys=("leadership", "customer-success"),
                group_grant_keys=("exec-staff",),
                department_grant_keys=("leadership",),
            ),
            DOCS,
            DIRECTORY,
        )
    )
    assert access.sensitivity == "confidential"
    assert access.access_mode == "policy"
    assert access.account_id == A_KESTREL
    assert access.department_ids == tuple(sorted((D_CS, D_LEAD)))
    assert set(access.source_grants) == {("group", G_EXEC), ("department", D_LEAD)}


def test_source_defaults_apply_when_the_record_is_silent() -> None:
    config = _config(
        "document_repository",
        default_sensitivity="confidential",
        default_departments=["leadership"],
    )
    access = _ok(map_access(_record(), config, DIRECTORY))
    assert (access.sensitivity, access.department_ids, access.account_id) == (
        "confidential",
        (D_LEAD,),
        None,
    )


def test_tickets_map_their_organization_to_an_account() -> None:
    access = _ok(map_access(_record("ticket", "360001001"), TICKETS, TICKET_REFS))
    assert access.account_id == A_ACME


def test_a_mapping_resolves_only_for_its_own_source_kind() -> None:
    """Mappings are per source: a document naming a channel ID is not an account key, and
    a ticketing mapping does not resolve a document's key."""
    failed = map_access(_record(account_ref="360001001"), DOCS, TICKET_REFS)
    assert isinstance(failed, AccessFailure)
    assert failed.code == "access_unmappable:account"
    # A non-account-scoped chat source with no mapping: no account (the source's rules).
    plain = _ok(map_access(_record("chat_thread", "C0ACME"), SLACK, DIRECTORY))
    assert plain.account_id is None


def test_channels_take_their_mapped_account_and_configured_sensitivity() -> None:
    acme = _ok(map_access(_record("chat_thread", "C0ACME"), SLACK, CHANNEL_REFS))
    assert (acme.account_id, acme.sensitivity) == (A_ACME, "internal")
    escalations = _ok(
        map_access(
            _record("chat_thread", "G0ESC", grants_only=True, group_refs=("G0PRIVATE",)),
            SLACK,
            CHANNEL_REFS,
        )
    )
    assert escalations.sensitivity == "confidential"
    assert escalations.access_mode == "grants_only"
    assert escalations.source_grants == (("group", G_CHANNEL),)
    # A public channel with no configuration: the source default, no account.
    plain = _ok(map_access(_record("chat_channel_day", "C0GENERAL"), SLACK, CHANNEL_REFS))
    assert (plain.account_id, plain.sensitivity, plain.access_mode) == (None, "internal", "policy")


def test_the_access_hash_covers_every_access_fact_and_nothing_else() -> None:
    base = _ok(map_access(_record(account_ref="acme-corp"), DOCS, DIRECTORY))
    variants = [
        _record(account_ref="kestrel-bank"),
        _record(account_ref="acme-corp", sensitivity="confidential"),
        _record(account_ref="acme-corp", grants_only=True),
        _record(account_ref="acme-corp", department_keys=("leadership",)),
        _record(account_ref="acme-corp", group_grant_keys=("exec-staff",)),
    ]
    hashes = {base.access_hash} | {
        _ok(map_access(v, DOCS, DIRECTORY)).access_hash for v in variants
    }
    assert len(hashes) == len(variants) + 1
    # Content is not access: a different title and body give the same access hash.
    other = _record(account_ref="acme-corp").model_copy(
        update={"title": "Other", "parts": (RecordPart(anchor="1", text="other"),)}
    )
    assert _ok(map_access(other, DOCS, DIRECTORY)).access_hash == base.access_hash
