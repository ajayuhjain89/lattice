"""AI egress policy on embedding and operator-visible failure data (SEC-10, SEC-14;
docs/security-model.md §6, §10; docs/ingestion-design.md §7-8).

A recording provider captures exactly what would leave Lattice:

* Under the default policy (``internal``), the payloads are exactly the embedding
  inputs of chunks of ``internal`` documents: no confidential or restricted text, no
  canary above the level, no identifiers, no structured account names (D-10).
* An organization opt-in *and* a provider approved for ``confidential`` admits
  confidential text; ``restricted`` never; an unapproved provider caps the level at
  ``internal``; lowering the level removes the vectors it no longer allows.

Failure rows carry fixed codes, safe parameters and a keyed opaque reference -- never
free text -- and sync logs carry counts and codes only.
"""

import logging
import re

import pytest
from sqlalchemy import text

from lattice.dataset.lint import Dataset, load_dataset
from lattice.db.engine import Database
from lattice.ingestion.embedding_input import KIND_LABELS, embedding_input
from lattice.ingestion.store import opaque_ref
from lattice.providers.embedding import RecordingEmbeddingProvider

from .conftest import DATA, GOLDEN, ORGS, Org, rows, scalar, seed, sync, sync_all

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_RANK = {"internal": 0, "confidential": 1, "restricted": 2}


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATA, GOLDEN)


async def _embedded_inputs(db: Database, org: Org, levels: tuple[str, ...]) -> set[str]:
    """The embedding input of every chunk of documents at ``levels``."""
    found = await rows(
        db,
        org.id,
        "SELECT d.document_kind, d.title, c.heading_path, c.text FROM chunks c "
        "JOIN documents d ON d.id = c.document_id "
        "WHERE d.sensitivity = ANY(CAST(:levels AS text[])) AND d.status = 'active'",
        levels=list(levels),
    )
    return {embedding_input(k, t, tuple(h), body) for k, t, h, body in found}


def _canaries_above(dataset: Dataset, level: str) -> list[str]:
    out: list[str] = []
    for canary in dataset.canaries:
        world = dataset.worlds[canary.organization]
        record = world.raw.records[canary.record.split("#")[0]]
        record_level = world.level(record)
        if record_level is not None and _RANK[record_level] > _RANK[level]:
            out.append(canary.string)
    return out


async def test_default_policy_sends_only_internal_text(
    dataset: Dataset, owner_db: Database
) -> None:
    provider = RecordingEmbeddingProvider()
    orgs = [await seed(owner_db, slug) for slug in ORGS]
    for org in orgs:
        await sync_all(owner_db, org, provider)

    sent = set(provider.texts)
    expected: set[str] = set()
    for org in orgs:
        expected |= await _embedded_inputs(owner_db, org, ("internal",))
        withheld = await _embedded_inputs(owner_db, org, ("confidential", "restricted"))
        assert withheld, org.slug  # the dataset has content above the level
        assert not sent & withheld
        assert (
            await scalar(
                owner_db,
                org.id,
                "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE d.sensitivity <> 'internal' AND (c.embedding IS NOT NULL "
                "OR c.embedding_status <> 'policy_excluded')",
            )
            == 0
        )
    assert sent == expected
    assert {call.purpose for call in provider.calls} == {"document"}

    payload = "\n".join(provider.texts)
    canaries = _canaries_above(dataset, "internal")
    assert canaries
    for canary in canaries:
        assert canary not in payload
    assert not UUID.search(payload)
    # D-10: the header is the kind label, the title and the heading path -- nothing else.
    accounts = {
        name
        for world in dataset.worlds.values()
        for account in world.manifest.accounts
        for name in (account.name, *account.aliases)
    }
    labels = tuple(f"{label}: " for label in KIND_LABELS.values())
    for item in provider.texts:
        header = item.split("\n\n", 1)[0].split("\n")
        assert header[0].startswith(labels)
        assert len(header) <= 2
        title_and_path = " ".join(header)
        for name in accounts:
            # An account name may appear only if the document's own title says it.
            if name in title_and_path:
                assert name in header[0], (name, header)


async def _set_levels(db: Database, org: Org, org_level: str, provider_level: str) -> None:
    async with db.tenant_transaction(org.id) as s:
        await s.execute(
            text(
                "INSERT INTO org_settings (organization_id, external_egress_level) "
                "VALUES (:o, :l) ON CONFLICT (organization_id) "
                "DO UPDATE SET external_egress_level = EXCLUDED.external_egress_level"
            ),
            {"o": org.id, "l": org_level},
        )
        await s.execute(
            text("UPDATE model_providers SET approved_for_level = :l WHERE purpose = 'embedding'"),
            {"l": provider_level},
        )


async def test_opt_in_admits_confidential_but_never_restricted(
    dataset: Dataset, owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    confidential = await _embedded_inputs(owner_db, halden, ("confidential",))
    restricted = await _embedded_inputs(owner_db, halden, ("restricted",))
    assert confidential
    assert restricted

    # The organization opts in, but the provider is approved for internal only.
    await _set_levels(owner_db, halden, "confidential", "internal")
    capped = RecordingEmbeddingProvider()
    await sync(owner_db, halden, "drive", capped)
    assert capped.calls == []

    # Both agree: confidential chunks are embedded by the next sync; restricted never.
    await _set_levels(owner_db, halden, "confidential", "confidential")
    opted = RecordingEmbeddingProvider()
    result = await sync(owner_db, halden, "drive", opted)
    assert set(opted.texts) == confidential
    assert result.chunks_embedded == len(confidential)
    assert not set(opted.texts) & restricted
    for canary in _canaries_above(dataset, "confidential"):
        assert canary not in "\n".join(opted.texts)

    # The provider may be approved for restricted; the organization still is not.
    await _set_levels(owner_db, halden, "confidential", "restricted")
    again = RecordingEmbeddingProvider()
    await sync(owner_db, halden, "drive", again)
    assert again.calls == []

    # Lowering the level removes the vectors it no longer allows, at once.
    await _set_levels(owner_db, halden, "internal", "restricted")
    lowered = await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    assert lowered.chunks_policy_excluded == len(confidential)
    assert (
        await scalar(
            owner_db,
            halden.id,
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.sensitivity <> 'internal' AND c.embedding IS NOT NULL",
        )
        == 0
    )


async def test_a_new_provider_record_is_approved_for_internal_only(
    owner_db: Database, halden: Org
) -> None:
    await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    assert await rows(
        owner_db,
        halden.id,
        "SELECT purpose, provider, model, deployment, approved_for_level, training_opt_out "
        "FROM model_providers",
    ) == [("embedding", "fake", "fake-hashed-bow-v1", "external_api", "internal", True)]


# --- failure rows and logs ------------------------------------------------------------------


async def test_a_sensitive_filename_stays_out_of_everything_but_the_operator_columns(
    owner_db: Database, halden: Org, caplog: pytest.LogCaptureFixture
) -> None:
    """evaluation-plan §2: a failing record whose filename names restricted content."""
    await sync(owner_db, halden, "drive", None)
    path = next(halden.path("drive").rglob("project-tern-memo.md"))
    path.write_text(path.read_text().replace("updated: ", "updated: not-a-date # ", 1))
    with caplog.at_level(logging.DEBUG):
        result = await sync(owner_db, halden, "drive", None)
    assert result.failure_codes == {"invalid_record": 1}
    assert result.quarantined == 1  # restricted: never left readable on stale access
    ((external_id, location, ref, params),) = await rows(
        owner_db,
        halden.id,
        "SELECT external_id, location, opaque_ref, message_params FROM sync_record_failures",
    )
    # The raw identifiers exist for infrastructure operators only; what an admin who
    # cannot read the memo will be shown (Phase 8) is the opaque reference.
    assert (external_id, location.endswith("project-tern-memo.md")) == ("project-tern-memo", True)
    assert "tern" not in ref
    assert params == {"field": "updated"}
    logged = "\n".join(
        [r.getMessage() for r in caplog.records]
        + [str(getattr(r, "fields", "")) for r in caplog.records]
    )
    assert "tern" not in logged.lower()


FAILURE_COLUMNS = {
    "id",
    "organization_id",
    "sync_run_id",
    "external_id",
    "location",
    "opaque_ref",
    "stage",
    "error_code",
    "message_params",
    "created_at",
}


async def test_failure_rows_hold_codes_and_opaque_references_only(
    owner_db: Database, halden: Org, caplog: pytest.LogCaptureFixture
) -> None:
    path = halden.path("support") / "tickets.jsonl"
    path.write_text(path.read_text() + '{"id": "HAL-9001", "subject": "Project Tern leak"\n')
    with caplog.at_level(logging.DEBUG):
        await sync(owner_db, halden, "support", RecordingEmbeddingProvider())

    columns = await rows(
        owner_db,
        halden.id,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'sync_record_failures'",
    )
    assert {c[0] for c in columns} == FAILURE_COLUMNS  # no free-text message column
    failures = await rows(
        owner_db,
        halden.id,
        "SELECT external_id, location, opaque_ref, stage, error_code, message_params "
        "FROM sync_record_failures ORDER BY error_code",
    )
    assert [(f[3], f[4], f[5]) for f in failures] == [
        ("map_access", "access_unmappable:account", {"external_account_ref": "360009913"}),
        ("parse", "malformed_json", {}),
    ]
    key = (
        await rows(owner_db, halden.id, "SELECT opaque_ref_key FROM sources WHERE key = 'support'")
    )[0][0]
    for external_id, location, ref, *_ in failures:
        assert re.fullmatch(r"rec_[0-9a-f]{16}", ref)
        assert ref == opaque_ref(bytes(key), external_id or location)
        assert (external_id or location) not in ref
    # Keyed: the same identifier under another source's key is another reference.
    assert opaque_ref(b"k" * 32, "HAL-4951") != opaque_ref(b"j" * 32, "HAL-4951")

    messages = [r.getMessage() for r in caplog.records] + [
        str(getattr(r, "fields", "")) for r in caplog.records
    ]
    logged = "\n".join(messages)
    assert "ingestion.sync" in logged
    for forbidden in ("Project Tern", "HAL-4951", "HAL-9001", "tickets.jsonl", "Fernhill"):
        assert forbidden not in logged
