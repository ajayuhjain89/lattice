"""A failed re-embed never leaves a stale vector attached to new content.

The approved behaviour (docs/ingestion-design.md §4): when changed content is stored
but its embedding call fails, the new content is kept and is lexically searchable at
once, the affected chunks are ``failed`` with no vector, and the next sync embeds
exactly those chunks. The document is never rolled back because semantic indexing
failed.

The invariant proved here, for the transition

    old content + old vector -> content changes -> new embedding attempt fails

* the new canonical body and its lexical entries are stored;
* no chunk keeps a vector computed from other text: a changed passage is a new chunk
  (its ID derives from its content) and the old row goes with its vector; a passage
  whose text is unchanged but whose embedding *input* changed (a new title or heading)
  keeps its ID and has its vector cleared;
* affected chunks are ``embedding_status = 'failed'`` with ``embedding`` and
  ``embedding_model`` NULL, and the database refuses a vector on a non-``embedded``
  chunk (CHECK), so a semantic path that reads vectors cannot consume one;
* recovery: the next sync sends exactly the inputs that still need a vector, and the
  vectors stored are those of the new inputs.

The global check ``_every_vector_matches_its_own_input`` recomputes, for every embedded
chunk, the deterministic fake vector of the chunk's *current* input and compares it
with the stored one, and checks the stored ``embedding_input_hash`` -- so any vector
generated from previous text would be detected wherever it sits.
"""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from lattice.db.engine import Database
from lattice.ingestion.embedding_input import embedding_input, input_hash
from lattice.providers.embedding import FakeEmbeddingProvider, RecordingEmbeddingProvider

from .conftest import Org, changed_tables, row_versions, rows, sync
from .helpers import document, edit_front_matter

DOC = "refund-policy-v2"
OLD_SENTENCE = "An Enterprise customer may"
NEW_SENTENCE = "An Enterprise customer on an annual plan may"
NEW_WORD = "zephyrine"  # appears nowhere in the dataset
FAKE = FakeEmbeddingProvider()


async def _chunks(db: Database, org: Org, document_id: object) -> list[dict[str, Any]]:
    found = await rows(
        db,
        org.id,
        "SELECT c.id, c.text, c.embedding_status, c.embedding::text, c.embedding_model, "
        "c.embedding_input_hash, c.heading_path, d.title, d.document_kind, c.xmin::text "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE c.document_id = :d ORDER BY c.ordinal",
        d=document_id,
    )
    names = [
        *("id", "text", "status", "vector", "model"),
        *("input_hash", "heading_path", "title", "kind", "xmin"),
    ]
    return [dict(zip(names, r, strict=True)) for r in found]


def _payload(chunk: dict[str, Any]) -> str:
    return embedding_input(
        chunk["kind"], chunk["title"], tuple(chunk["heading_path"]), chunk["text"]
    )


def _parse(vector: str) -> list[float]:
    return [float(v) for v in vector.strip("[]").split(",")]


async def _every_vector_matches_its_own_input(db: Database, org: Org) -> int:
    """Every stored vector is the fake vector of its chunk's current input, and the
    stored input hash is that input's. Returns the number of vectors checked."""
    found = await rows(
        db,
        org.id,
        "SELECT c.text, c.heading_path, d.title, d.document_kind, c.embedding::text, "
        "c.embedding_input_hash, c.embedding_status FROM chunks c "
        "JOIN documents d ON d.id = c.document_id",
    )
    checked = 0
    for body, heading, title, kind, vector, stored_hash, status in found:
        payload = embedding_input(kind, title, tuple(heading), body)
        assert bytes(stored_hash) == input_hash(payload)
        assert (vector is not None) == (status == "embedded")
        if vector is not None:
            expected = FAKE.vector(payload)
            assert _parse(vector) == pytest.approx(expected, abs=1e-6)
            checked += 1
    return checked


def _edit_section(body: str) -> str:
    assert body.count(OLD_SENTENCE) == 1
    return body.replace(OLD_SENTENCE, f"{NEW_SENTENCE} ({NEW_WORD})")


async def test_a_failed_reembed_leaves_no_stale_vector_and_the_next_sync_recovers(
    owner_db: Database, halden: Org
) -> None:
    # Old content + old vectors.
    await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    old_doc = await document(owner_db, halden, DOC)
    old = await _chunks(owner_db, halden, old_doc["id"])
    assert all(c["status"] == "embedded" for c in old)
    (edited_old,) = [c for c in old if OLD_SENTENCE in c["text"]]
    untouched = [c for c in old if c is not edited_old]
    assert await _every_vector_matches_its_own_input(owner_db, halden) > 0

    # Content changes; the embedding attempt fails.
    edit_front_matter(halden, DOC, body=_edit_section)
    failing = RecordingEmbeddingProvider(fail=True)
    result = await sync(owner_db, halden, "drive", failing)
    assert (result.updated, result.status) == (1, "partial")
    assert result.failure_codes == {"embedding_failed:provider_error": 1}

    # The new canonical content is stored (no rollback) ...
    new_doc = await document(owner_db, halden, DOC)
    assert new_doc["version"] == old_doc["version"] + 1
    assert new_doc["content_hash"] != old_doc["content_hash"]
    assert NEW_WORD in new_doc["body"]
    # ... and so is its lexical representation.
    lexical = await rows(
        owner_db,
        halden.id,
        "SELECT c.id FROM chunks c WHERE c.document_id = :d "
        "AND c.tsv @@ plainto_tsquery('english', :w)",
        d=new_doc["id"],
        w=NEW_WORD,
    )
    assert len(lexical) == 1

    new = await _chunks(owner_db, halden, new_doc["id"])
    (edited_new,) = [c for c in new if NEW_WORD in c["text"]]
    # The changed passage is a new chunk: the old row and its vector are gone.
    assert edited_new["id"] != edited_old["id"]
    assert edited_old["id"] not in {c["id"] for c in new}
    assert (
        await rows(owner_db, halden.id, "SELECT 1 FROM chunks WHERE id = :i", i=edited_old["id"])
        == []
    )
    # It carries the failed state, no vector, no model, and the hash of its new input.
    assert (edited_new["status"], edited_new["vector"], edited_new["model"]) == (
        "failed",
        None,
        None,
    )
    assert bytes(edited_new["input_hash"]) == input_hash(_payload(edited_new))
    # The provider was only ever asked for the new input (never sent old text again).
    assert set(failing.texts) == {_payload(edited_new)}
    # Unchanged passages keep their IDs and their (still correct) vectors.
    kept = {c["id"]: c for c in new if c["id"] != edited_new["id"]}
    assert set(kept) == {c["id"] for c in untouched}
    for c in untouched:
        assert (kept[c["id"]]["status"], kept[c["id"]]["vector"]) == ("embedded", c["vector"])
    await _every_vector_matches_its_own_input(owner_db, halden)

    # A semantic read of vectors cannot see the chunk; the database refuses one on it.
    assert (
        await rows(
            owner_db,
            halden.id,
            "SELECT 1 FROM chunks WHERE id = :i AND embedding IS NOT NULL",
            i=edited_new["id"],
        )
        == []
    )
    with pytest.raises(IntegrityError, match="ck_chunks_embedding_matches_status"):
        async with owner_db.tenant_transaction(halden.id) as s:
            await s.execute(
                text(
                    "UPDATE chunks SET embedding = CAST(:v AS vector), embedding_model = 'x' "
                    "WHERE id = :i"
                ),
                {"v": edited_old["vector"], "i": edited_new["id"]},
            )

    # Recovery: the next sync embeds exactly what still needs a vector.
    before = await row_versions(owner_db, halden.id)
    recovering = RecordingEmbeddingProvider()
    recovered = await sync(owner_db, halden, "drive", recovering)
    assert (recovered.status, recovered.unchanged, recovered.updated) == ("succeeded", 48, 0)
    assert recovered.chunks_embedded == 1
    assert recovering.texts == [_payload(edited_new)]
    assert changed_tables(before, await row_versions(owner_db, halden.id)) == {
        "sources",
        "chunks",
    }
    after = await _chunks(owner_db, halden, new_doc["id"])
    (fixed,) = [c for c in after if c["id"] == edited_new["id"]]
    assert (fixed["status"], fixed["model"]) == ("embedded", FAKE.model)
    assert _parse(fixed["vector"]) == pytest.approx(FAKE.vector(_payload(edited_new)), abs=1e-6)
    assert _parse(fixed["vector"]) != pytest.approx(_parse(edited_old["vector"]), abs=1e-6)
    # Only that chunk was written.
    versions = {c["id"]: c["xmin"] for c in new}
    assert {c["id"] for c in after if c["xmin"] != versions[c["id"]]} == {edited_new["id"]}
    await _every_vector_matches_its_own_input(owner_db, halden)


async def test_same_text_under_a_new_title_does_not_keep_its_old_vector(
    owner_db: Database, halden: Org
) -> None:
    """A passage whose text is unchanged keeps its chunk ID, but its embedding input
    (title + heading path + text) changed with the title: its old vector must go."""
    await sync(owner_db, halden, "drive", RecordingEmbeddingProvider())
    old_doc = await document(owner_db, halden, DOC)
    old = await _chunks(owner_db, halden, old_doc["id"])

    edit_front_matter(halden, DOC, title="Enterprise refund policy (v2, revised)")
    failing = RecordingEmbeddingProvider(fail=True)
    result = await sync(owner_db, halden, "drive", failing)
    assert (result.updated, result.status) == (1, "partial")

    new = await _chunks(owner_db, halden, old_doc["id"])
    assert [c["id"] for c in new] == [c["id"] for c in old]  # same passages, same IDs
    for before, after in zip(old, new, strict=True):
        assert (after["status"], after["vector"], after["model"]) == ("failed", None, None)
        assert after["input_hash"] != before["input_hash"]
        assert bytes(after["input_hash"]) == input_hash(_payload(after))
    assert set(failing.texts) == {_payload(c) for c in new}
    assert all(
        t.startswith("Document: Enterprise refund policy (v2, revised)") for t in failing.texts
    )

    recovering = RecordingEmbeddingProvider()
    recovered = await sync(owner_db, halden, "drive", recovering)
    assert recovered.status == "succeeded"
    assert sorted(recovering.texts) == sorted(_payload(c) for c in new)
    assert all(c["status"] == "embedded" for c in await _chunks(owner_db, halden, old_doc["id"]))
    assert await _every_vector_matches_its_own_input(owner_db, halden) > 0
