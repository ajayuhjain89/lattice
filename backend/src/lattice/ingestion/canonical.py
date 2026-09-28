"""NORMALIZE and HASH (docs/ingestion-design.md §1 steps 2 and 4): a connector's
``SourceRecord`` becomes a ``CanonicalDocument`` -- one normalized plain-text ``body``
with the exact span of every part, and a content hash.

``body`` is what citations and the inspector show; every chunk is an exact slice of
it. Rendering is per kind:

* doc, ticket: ``<heading>\\n<text>`` (the heading line only when there is one);
* chat: ``[YYYY-MM-DD HH:MM UTC] <speaker>: <text>``, so a message keeps its speaker
  and time wherever it is cut.

Parts are separated by a blank line. The content hash covers everything that is
content (title, body, part structure, authorship, source timestamps, supersession,
per-kind extras) and nothing that is access metadata: permission changes never change
it (the access hash, ``lattice.ingestion.access``, covers those).
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

from lattice.connectors.contract import DocumentKind, RecordPart, SourceRecord

CANONICAL_VERSION = "canonical-1"
MAX_BODY_CHARS = 1_000_000
MAX_TITLE_CHARS = 500

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """NFC, LF line endings, no control characters, no trailing spaces, at most one
    blank line in a row, no leading/trailing blank lines."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text.replace("\t", " "))
    text = _TRAILING_SPACE.sub("\n", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def normalize_line(text: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFC", _CONTROL.sub(" ", text))).strip()


@dataclass(frozen=True)
class PartSpan:
    anchor: str
    heading: str | None
    author: str | None
    occurred_at: datetime | None
    start: int
    end: int


@dataclass(frozen=True)
class CanonicalDocument:
    external_id: str
    kind: DocumentKind
    title: str
    display_ref: str | None
    uri: str | None
    author: str | None
    created_at: datetime | None
    updated_at: datetime
    authority: str
    supersedes_external_id: str | None
    metadata: dict[str, str | int | bool]
    body: str
    parts: tuple[PartSpan, ...]
    content_hash: bytes


class NormalizeFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.astimezone(UTC)


def _render(kind: DocumentKind, part: RecordPart) -> str:
    text = normalize_text(part.text)
    if kind in ("chat_thread", "chat_channel_day"):
        stamp = _utc(part.occurred_at)
        when = f"[{stamp:%Y-%m-%d %H:%M} UTC] " if stamp else ""
        return f"{when}{normalize_line(part.author or 'unknown')}: {text}".rstrip()
    heading = normalize_line(part.heading) if part.heading else ""
    if heading and text:
        return f"{heading}\n{text}"
    return heading or text


def canonicalize(record: SourceRecord, default_authority: str) -> CanonicalDocument:
    """Raises ``NormalizeFailure`` for records that cannot be stored."""
    title = normalize_line(record.title)
    if not title:
        raise NormalizeFailure("empty_title")
    if len(title) > MAX_TITLE_CHARS:
        raise NormalizeFailure("title_too_long")

    pieces: list[str] = []
    spans: list[PartSpan] = []
    offset = 0
    for part in record.parts:
        rendered = _render(record.kind, part)
        if not rendered:
            continue
        if pieces:
            offset += 2  # the "\n\n" separator
        spans.append(
            PartSpan(
                anchor=part.anchor,
                heading=normalize_line(part.heading) if part.heading else None,
                author=normalize_line(part.author) if part.author else None,
                occurred_at=_utc(part.occurred_at),
                start=offset,
                end=offset + len(rendered),
            )
        )
        pieces.append(rendered)
        offset += len(rendered)
    body = "\n\n".join(pieces)
    if not body:
        raise NormalizeFailure("empty_record")
    if len(body) > MAX_BODY_CHARS:
        raise NormalizeFailure("content_too_large")

    author = normalize_line(record.author) if record.author else None
    fields = {
        "version": CANONICAL_VERSION,
        "kind": record.kind,
        "title": title,
        "display_ref": record.display_ref,
        "uri": record.uri,
        "author": author,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "authority": record.authority or default_authority,
        "supersedes": record.supersedes_external_id,
        "metadata": record.extra,
        "body": body,
        "parts": [
            [s.anchor, s.heading, s.author, _iso(s.occurred_at), s.start, s.end] for s in spans
        ],
    }
    digest = hashlib.sha256(
        json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).digest()
    return CanonicalDocument(
        external_id=record.external_id,
        kind=record.kind,
        title=title,
        display_ref=record.display_ref,
        uri=record.uri,
        author=author,
        created_at=_utc(record.created_at),
        updated_at=_utc(record.updated_at) or record.updated_at,
        authority=record.authority or default_authority,
        supersedes_external_id=record.supersedes_external_id,
        metadata=dict(record.extra),
        body=body,
        parts=tuple(spans),
        content_hash=digest,
    )


def _iso(value: datetime | None) -> str | None:
    stamp = _utc(value)
    return None if stamp is None else stamp.isoformat()
