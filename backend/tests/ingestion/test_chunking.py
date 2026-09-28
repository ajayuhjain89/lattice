"""Normalization and chunking (docs/ingestion-design.md §2-3).

* Golden outputs: the canonical body and chunk boundaries of representative records of
  every kind are pinned in ``golden/chunks.json``. A chunker or normalizer change that
  moves them fails here and must bump ``CHUNKER_VERSION`` / ``CANONICAL_VERSION`` (and
  be re-chunked with ``lattice reindex --rechunk``). Regenerate deliberately with
  ``LATTICE_UPDATE_GOLDEN=1``.
* Invariants over every record of both organizations: each chunk is an exact, trimmed
  slice of the body within the size limit; no non-whitespace text is lost; document
  chunks never span sections; chat chunks keep speaker and time.
* Locality: editing one section, or adding a ticket comment, changes no other chunk.
* The embedding input carries title and heading path only -- never account names,
  identifiers or access metadata (D-10).
"""

import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lattice.connectors.contract import RecordPart, SourceRecord
from lattice.connectors.registry import build_connector
from lattice.ingestion.canonical import CANONICAL_VERSION, canonicalize, normalize_text
from lattice.ingestion.chunking import CHUNKER_VERSION, MAX_CHARS, TARGET_CHARS, chunk
from lattice.ingestion.embedding_input import EMBEDDING_INPUT_VERSION, embedding_input
from lattice.sources.config import load_sources_file

from .conftest import DATA, ORGS

GOLDEN_FILE = Path(__file__).parent / "golden" / "chunks.json"
GOLDEN_RECORDS = {
    "halden/drive": ["refund-policy-v2", "acme-account-brief"],
    "halden/support": ["HAL-4821"],
    "halden/slack": ["C0INCIDENT1:1784621160.000100", "C0ENGINEER1:day:2026-08-18"],
    "veridian/drive": [],  # filled with the first record, whatever it is called
}


def _records(slug: str, key: str) -> dict[str, SourceRecord]:
    config = load_sources_file(DATA / slug / "sources.yaml").get(key)
    assert config is not None
    connector = build_connector(config.connector_type, DATA / slug / config.path)
    return {r.external_id: r for r in connector.records() if isinstance(r, SourceRecord)}


def _all_records() -> list[tuple[str, SourceRecord]]:
    out: list[tuple[str, SourceRecord]] = []
    for slug in ORGS:
        for source in load_sources_file(DATA / slug / "sources.yaml").sources:
            for record in _records(slug, source.key).values():
                out.append((source.authority, record))
    return out


def _describe(record: SourceRecord) -> dict[str, object]:
    doc = canonicalize(record, "working")
    return {
        "body_sha256": hashlib.sha256(doc.body.encode()).hexdigest(),
        "content_hash": doc.content_hash.hex(),
        "chunks": [
            {
                "span": [c.char_start, c.char_end],
                "heading_path": list(c.heading_path),
                "anchors": list(c.source_anchors),
                "occurred_at": c.occurred_at.isoformat() if c.occurred_at else None,
                "author": c.author_display,
                "starts_with": doc.body[c.char_start : c.char_start + 40],
            }
            for c in chunk(doc)
        ],
    }


def _golden_snapshot() -> dict[str, object]:
    records: dict[str, object] = {}
    for where, ids in GOLDEN_RECORDS.items():
        slug, key = where.split("/")
        found = _records(slug, key)
        for external_id in ids or sorted(found)[:1]:
            records[f"{where}/{external_id}"] = _describe(found[external_id])
    return {
        "versions": [CANONICAL_VERSION, CHUNKER_VERSION, EMBEDDING_INPUT_VERSION],
        "records": records,
    }


def test_chunker_golden_outputs() -> None:
    current = _golden_snapshot()
    if os.environ.get("LATTICE_UPDATE_GOLDEN"):
        GOLDEN_FILE.parent.mkdir(exist_ok=True)
        GOLDEN_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    golden = json.loads(GOLDEN_FILE.read_text())
    assert current["versions"] == golden["versions"], (
        "a version changed: regenerate the golden file deliberately (LATTICE_UPDATE_GOLDEN=1)"
    )
    assert current == golden, (
        "chunk boundaries or canonical text changed without a version bump: bump "
        "CHUNKER_VERSION / CANONICAL_VERSION, then regenerate with LATTICE_UPDATE_GOLDEN=1"
    )
    kinds = {k.split("/")[1] for k in golden["records"]}
    assert kinds == {"drive", "support", "slack"}


# --- invariants over every record -------------------------------------------------------

ALL = _all_records()


@pytest.mark.parametrize(("authority", "record"), ALL, ids=[r.external_id for _, r in ALL])
def test_every_chunk_is_an_exact_slice_and_nothing_is_lost(
    authority: str, record: SourceRecord
) -> None:
    doc = canonicalize(record, authority)
    chunks = chunk(doc)
    assert chunks
    assert chunk(canonicalize(record, authority)) == chunks  # deterministic
    covered = [False] * len(doc.body)
    previous_end = 0
    for c in chunks:
        text = doc.body[c.char_start : c.char_end]
        assert text
        assert text == text.strip()
        assert len(text) <= MAX_CHARS
        assert c.char_start >= previous_end, "chunks are ordered and never overlap"
        previous_end = c.char_end
        for i in range(c.char_start, c.char_end):
            covered[i] = True
        if doc.kind == "doc" or doc.kind == "ticket":
            spans = [p for p in doc.parts if p.start <= c.char_start and c.char_end <= p.end]
            assert len(spans) == 1, "a document or ticket chunk never spans two parts"
            assert c.source_anchors == (spans[0].anchor,)
        else:
            # Every message keeps its speaker and time inside the chunk.
            assert re.match(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\] ", text)
            assert c.author_display
    lost = "".join(ch for ch, ok in zip(doc.body, covered, strict=True) if not ok)
    assert lost.strip() == ""


# --- locality -----------------------------------------------------------------------------


def _texts(record: SourceRecord) -> list[str]:
    doc = canonicalize(record, "working")
    return [doc.body[c.char_start : c.char_end] for c in chunk(doc)]


def test_editing_one_section_changes_only_that_sections_chunks() -> None:
    record = _records("halden", "drive")["refund-policy-v2"]
    before = _texts(record)
    parts = list(record.parts)
    parts[1] = parts[1].model_copy(update={"text": parts[1].text + "\n\nOne more sentence."})
    after = _texts(record.model_copy(update={"parts": tuple(parts)}))
    assert len(set(before) - set(after)) == 1
    assert len(set(after) - set(before)) == 1


def test_a_new_ticket_comment_adds_one_chunk_and_changes_no_other() -> None:
    record = _records("halden", "support")["HAL-4821"]
    before = _texts(record)
    comment = RecordPart(
        anchor="c99",
        heading="Comment 99 · Priya Natarajan (agent)",
        author="Priya Natarajan",
        occurred_at=datetime(2026, 9, 27, tzinfo=UTC),
        text="Following up: the fix is deployed.",
    )
    after = _texts(record.model_copy(update={"parts": (*record.parts, comment)}))
    assert after[: len(before)] == before
    assert len(after) == len(before) + 1


def _chat(kind: str, gaps_minutes: list[int], size: int = 60) -> SourceRecord:
    start = datetime(2026, 9, 1, 9, tzinfo=UTC)
    parts = []
    at = start
    for i, gap in enumerate([0, *gaps_minutes]):
        at += timedelta(minutes=gap)
        parts.append(RecordPart(anchor=str(i), author=f"P{i % 2}", occurred_at=at, text="x" * size))
    return SourceRecord(
        external_id="C1:t",
        kind=kind,
        title="#c",
        created_at=start,
        updated_at=at,
        parts=tuple(parts),
    )


def test_a_thread_window_never_spans_more_than_48_hours() -> None:
    record = _chat("chat_thread", [60, 47 * 60, 120])  # 0h, 1h, 48h, 50h
    doc = canonicalize(record, "conversational")
    assert [c.source_anchors for c in chunk(doc)] == [("0", "1", "2"), ("3",)]


def test_a_channel_day_splits_on_gaps_longer_than_two_hours() -> None:
    record = _chat("chat_channel_day", [30, 121, 30])
    doc = canonicalize(record, "conversational")
    assert [c.source_anchors for c in chunk(doc)] == [("0", "1"), ("2", "3")]


def test_messages_are_packed_up_to_the_target() -> None:
    record = _chat("chat_channel_day", [1] * 40, size=200)
    doc = canonicalize(record, "conversational")
    chunks = chunk(doc)
    assert len(chunks) > 1
    for c in chunks:
        assert c.char_end - c.char_start <= TARGET_CHARS


def test_an_oversized_paragraph_is_split_at_sentence_ends() -> None:
    sentence = "The migration moves invoices to the new ledger in nightly batches. "
    record = SourceRecord(
        external_id="big",
        kind="doc",
        title="Big",
        created_at=None,
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
        parts=(RecordPart(anchor="1", heading="Plan", text=sentence * 80),),
    )
    doc = canonicalize(record, "working")
    texts = [doc.body[c.char_start : c.char_end] for c in chunk(doc)]
    assert len(texts) > 1
    assert all(len(t) <= MAX_CHARS for t in texts)
    assert all(t.endswith(".") for t in texts)


# --- normalization and hashing ------------------------------------------------------------


def test_normalization() -> None:
    assert normalize_text("a\r\nb\rc\t d  \n\n\n\ne\x00") == "a\nb\nc  d\n\ne"
    assert normalize_text("Café") == "Café"


def test_the_content_hash_ignores_access_and_tracks_content() -> None:
    record = _records("halden", "drive")["refund-policy-v2"]
    base = canonicalize(record, "working").content_hash
    restricted = record.access.model_copy(update={"sensitivity": "restricted", "grants_only": True})
    access_only = record.model_copy(update={"access": restricted, "account_ref": "kestrel-bank"})
    assert canonicalize(access_only, "working").content_hash == base
    edited = record.model_copy(update={"title": record.title + " (rev)"})
    assert canonicalize(edited, "working").content_hash != base
    later = record.model_copy(update={"updated_at": record.updated_at + timedelta(seconds=1)})
    assert canonicalize(later, "working").content_hash != base


def test_the_embedding_input_is_title_and_heading_path_only() -> None:
    """D-10: no account names, identifiers or access metadata in what is embedded."""
    record = _records("halden", "support")["HAL-4821"]
    doc = canonicalize(record, "working")
    for c in chunk(doc):
        text = doc.body[c.char_start : c.char_end]
        payload = embedding_input(doc.kind, doc.title, c.heading_path, text)
        header = payload[: -len(text)].rstrip("\n").split("\n")
        assert header[0] == f"Support ticket: {doc.title}"
        assert header[1:] == ([" / ".join(c.heading_path)] if c.heading_path else [])
        assert "HAL-4821" not in "\n".join(header)
        assert "Acme" not in "\n".join(header) or "Acme" in doc.title


def test_chunk_dataclasses_are_frozen() -> None:
    doc = canonicalize(_records("halden", "drive")["refund-policy-v2"], "working")
    first = chunk(doc)[0]
    with pytest.raises(AttributeError):
        first.char_start = 0  # type: ignore[misc]
    assert replace(first) == first
