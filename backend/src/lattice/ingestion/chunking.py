"""CHUNK (docs/ingestion-design.md §3): deterministic, source-aware chunking.

A chunker is a pure function of (kind, canonical body and part spans,
``CHUNKER_VERSION``). Every chunk is an exact slice ``body[char_start:char_end]``,
never text the source did not contain.

Budget: target ~400 tokens (``TARGET_CHARS``), hard maximum ~600 (``MAX_CHARS``),
estimated at four characters per token -- no tokenizer dependency; the estimate only
sizes chunks and is stored as ``token_count`` for context budgeting.

* doc: split by section (H1/H2 part), then paragraphs packed up to the target. A chunk
  never spans two sections, so editing one section leaves every other section's chunks
  (and their IDs and embeddings) untouched.
* ticket: the header part is one chunk; every comment is its own chunk (long comments
  split by paragraph). A new comment adds a chunk and changes no other.
* chat_thread: messages packed chronologically into windows up to the target; a window
  never spans more than 48 hours. Messages are never cut unless one alone exceeds the
  maximum, so speaker and time stay with the text.
* chat_channel_day: the same, and a gap of more than two hours starts a new window.

Oversized paragraphs are split at sentence ends, then at whitespace.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from lattice.connectors.contract import DocumentKind
from lattice.ingestion.canonical import CanonicalDocument, PartSpan

CHUNKER_VERSION = "chunker-1"
TARGET_CHARS = 1600
MAX_CHARS = 2400
THREAD_WINDOW = timedelta(hours=48)
CHANNEL_GAP = timedelta(hours=2)

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkDraft:
    char_start: int
    char_end: int
    heading_path: tuple[str, ...]
    source_anchors: tuple[str, ...]
    occurred_at: datetime | None
    author_display: str | None


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _paragraphs(body: str, start: int, end: int) -> list[tuple[int, int]]:
    """Non-empty paragraph spans within [start, end)."""
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in _PARAGRAPH_BREAK.finditer(body, start, end):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, end))
    return [(a, b) for a, b in spans if body[a:b].strip()]


def _split_oversized(body: str, start: int, end: int) -> list[tuple[int, int]]:
    """Cut [start, end) into pieces of at most MAX_CHARS, at sentence ends if possible,
    else at whitespace, else hard."""
    pieces: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > MAX_CHARS:
        limit = cursor + MAX_CHARS
        cut = None
        for match in _SENTENCE_END.finditer(body, cursor, limit):
            if match.start() - cursor >= TARGET_CHARS // 2:
                cut = (match.start(), match.end())
        if cut is None:
            space = body.rfind(" ", cursor + TARGET_CHARS // 2, limit)
            cut = (space, space + 1) if space > cursor else (limit, limit)
        pieces.append((cursor, cut[0]))
        cursor = cut[1]
    pieces.append((cursor, end))
    return [(a, b) for a, b in pieces if b > a]


def _pack(body: str, units: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Greedily pack consecutive spans up to TARGET_CHARS; oversized units are split."""
    packed: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for start, end in units:
        for a, b in (
            _split_oversized(body, start, end) if end - start > MAX_CHARS else [(start, end)]
        ):
            if current is None:
                current = (a, b)
            elif b - current[0] <= TARGET_CHARS:
                current = (current[0], b)
            else:
                packed.append(current)
                current = (a, b)
    if current is not None:
        packed.append(current)
    return packed


def _speakers(parts: list[PartSpan]) -> str | None:
    names = list(dict.fromkeys(p.author for p in parts if p.author))
    return ", ".join(names) or None


def _sections(doc: CanonicalDocument) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for part in doc.parts:
        heading = (part.heading,) if part.heading else ()
        for start, end in _pack(doc.body, _paragraphs(doc.body, part.start, part.end)):
            drafts.append(
                ChunkDraft(start, end, heading, (part.anchor,), part.occurred_at, part.author)
            )
    return drafts


def _windows(doc: CanonicalDocument, kind: DocumentKind) -> list[ChunkDraft]:
    windows: list[list[PartSpan]] = []
    for part in doc.parts:
        if windows:
            window = windows[-1]
            first, last = window[0], window[-1]
            too_long = part.end - first.start > TARGET_CHARS
            if kind == "chat_thread":
                split_time = (
                    part.occurred_at is not None
                    and first.occurred_at is not None
                    and part.occurred_at - first.occurred_at > THREAD_WINDOW
                )
            else:
                split_time = (
                    part.occurred_at is not None
                    and last.occurred_at is not None
                    and part.occurred_at - last.occurred_at > CHANNEL_GAP
                )
            if not too_long and not split_time:
                window.append(part)
                continue
        windows.append([part])

    drafts: list[ChunkDraft] = []
    for window in windows:
        start, end = window[0].start, window[-1].end
        pieces = (
            [(start, end)]
            if end - start <= MAX_CHARS
            else _pack(doc.body, _paragraphs(doc.body, start, end))
        )
        for a, b in pieces:
            inside = [p for p in window if p.start < b and p.end > a]
            drafts.append(
                ChunkDraft(
                    a,
                    b,
                    (),
                    tuple(p.anchor for p in inside),
                    inside[-1].occurred_at,
                    _speakers(inside),
                )
            )
    return drafts


def chunk(doc: CanonicalDocument) -> list[ChunkDraft]:
    if doc.kind in ("chat_thread", "chat_channel_day"):
        drafts = _windows(doc, doc.kind)
    else:
        drafts = _sections(doc)
    return [_trimmed(doc.body, d) for d in drafts]


def _trimmed(body: str, draft: ChunkDraft) -> ChunkDraft:
    """Drop surrounding whitespace so a chunk's text is exactly its trimmed slice."""
    start, end = draft.char_start, draft.char_end
    while start < end and body[start].isspace():
        start += 1
    while end > start and body[end - 1].isspace():
        end -= 1
    return ChunkDraft(
        start,
        end,
        draft.heading_path,
        draft.source_anchors,
        draft.occurred_at,
        draft.author_display,
    )
