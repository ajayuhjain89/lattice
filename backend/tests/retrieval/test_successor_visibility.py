"""D-14: supersession is never an existence oracle (retrieval-design §10).

A reader who can read document A but not its newer, superseding document B gets no
signal about B at all: no successor ID, no count, no title, no timestamp, no notice,
no change in ranking -- the result is exactly what it would be if B did not exist.
Between readable documents, explicit supersession metadata is exposed. Losing access
to the successor removes the signal on the very next request.
"""

from datetime import timedelta

import pytest

from lattice.db.engine import Database
from lattice.retrieval import RetrievalResult

from .world import NOW, Org, new_org, search

SUCCESSOR_TITLE = "SLA 2026 renegotiated terms"
SUCCESSOR_TIME = NOW - timedelta(days=3, hours=5, minutes=17)


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.department("leadership")
    await org.member("maya")  # internal clearance, no leadership department
    await org.member("grace", clearance="confidential", departments=["leadership"])
    await org.document(
        "sla-2025", "SLA 2025", ["P1 response within 1 hour."], updated=NOW - timedelta(days=400)
    )
    return org


async def _hidden_successor(org: Org) -> None:
    await org.document(
        "sla-2026",
        SUCCESSOR_TITLE,
        ["P1 response within 30 minutes."],
        sensitivity="confidential",
        departments=["leadership"],
        supersedes="sla-2025",
        updated=SUCCESSOR_TIME,
    )


def _observable(result: RetrievalResult) -> object:
    """Everything a reader receives, minus per-request noise (IDs, timings)."""
    data = result.model_dump(mode="json")
    data["trace"].pop("request_id")
    data["trace"].pop("timings_ms")
    return data


async def test_an_unreadable_successor_changes_nothing_the_reader_receives(
    app_db: Database, org: Org
) -> None:
    before = await search(app_db, org, "maya", "P1 response", provider=None)
    await _hidden_successor(org)
    after = await search(app_db, org, "maya", "P1 response", provider=None)
    assert _observable(after) == _observable(before)  # no notice, count or ranking change
    (evidence,) = after.evidence
    assert evidence.superseded_by_document_ids == ()
    dump = after.model_dump_json()
    assert str(org.documents["sla-2026"]) not in dump  # no successor ID
    assert SUCCESSOR_TITLE not in dump  # no title
    assert SUCCESSOR_TIME.isoformat() not in dump  # no timestamp
    assert SUCCESSOR_TIME.date().isoformat() not in dump
    assert "supersed" not in str(evidence.features)  # no ranking signal from it


async def test_an_unreadable_predecessor_is_not_named_either(app_db: Database, org: Org) -> None:
    await org.document(
        "sla-2024-internal",
        "SLA 2024 leadership notes",
        ["P1 response within 2 hours."],
        sensitivity="confidential",
        departments=["leadership"],
    )
    await org.document(
        "sla-2026-public",
        "SLA 2026",
        ["P1 response within 45 minutes."],
        supersedes="sla-2024-internal",
    )
    result = await search(app_db, org, "maya", "P1 response", provider=None)
    newer = next(e for e in result.evidence if org.document_of(e.chunk_id) == "sla-2026-public")
    assert newer.supersedes_document_id is None
    assert str(org.documents["sla-2024-internal"]) not in result.model_dump_json()


async def test_a_readable_successor_is_exposed(app_db: Database, org: Org) -> None:
    await _hidden_successor(org)
    grace = await search(app_db, org, "grace", "P1 response", provider=None)
    by_doc = {org.document_of(e.chunk_id): e for e in grace.evidence}
    assert by_doc["sla-2025"].superseded_by_document_ids == (org.documents["sla-2026"],)
    assert by_doc["sla-2026"].supersedes_document_id == org.documents["sla-2025"]


async def test_losing_access_removes_the_signal_on_the_next_request(
    app_db: Database, org: Org
) -> None:
    await _hidden_successor(org)
    first = await search(app_db, org, "grace", "P1 response", provider=None)
    old = next(e for e in first.evidence if org.document_of(e.chunk_id) == "sla-2025")
    assert old.superseded_by_document_ids == (org.documents["sla-2026"],)
    await org.execute(
        "DELETE FROM membership_departments WHERE membership_id = :m", m=org.members["grace"]
    )
    await org.execute(
        "UPDATE memberships SET authz_updated_at = now() WHERE id = :m", m=org.members["grace"]
    )
    second = await search(app_db, org, "grace", "P1 response", provider=None)
    (evidence,) = second.evidence
    assert org.document_of(evidence.chunk_id) == "sla-2025"
    assert evidence.superseded_by_document_ids == ()
    assert str(org.documents["sla-2026"]) not in second.model_dump_json()
