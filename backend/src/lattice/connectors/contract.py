"""The connector contract (docs/connector-design.md §2).

A connector translates one external system into typed ``SourceRecord``s. It reads
the source and interprets its format -- nothing else. It never touches the database,
computes embeddings, calls a model, builds queries or decides who may read what. It
reports what the source says about access (``AccessHints``, untrusted), and the
ingestion pipeline maps that through Lattice's own configuration, failing closed
(docs/ingestion-design.md §6).

One Protocol, a handful of frozen Pydantic models, and a registry
(``lattice.connectors.registry``). No plugin loader, no base-class hierarchy.

Every model validates its own invariants, so a connector cannot emit a record the
pipeline would have to repair: identifiers are normalized, timestamps carry a zone,
text carries no control characters or HTML.
"""

import re
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ConnectorType = Literal["document_repository", "ticketing", "chat_export"]
DocumentKind = Literal["doc", "ticket", "chat_thread", "chat_channel_day"]
Level = Literal["internal", "confidential", "restricted"]
Authority = Literal["system_of_record", "working", "conversational"]
ErrorStage = Literal["read", "parse", "validate"]

MAX_EXTERNAL_ID = 256
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_HTML_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s[^<>]*)?/?>")
_ERROR_CODE = re.compile(r"^[a-z_]+(:[a-z_]+)?$")
_PARAM_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{0,64}$")


def normalize_external_id(value: str) -> str:
    """The normal form every ``external_id`` must already be in (connector-design §4):
    trimmed, NFC, case preserved, no control characters, at most 256 characters."""
    return unicodedata.normalize("NFC", value).strip()


def _check_external_id(value: str) -> str:
    if value != normalize_external_id(value):
        raise ValueError("external_id must be trimmed and NFC-normalized")
    if not value or len(value) > MAX_EXTERNAL_ID:
        raise ValueError(f"external_id must be 1..{MAX_EXTERNAL_ID} characters")
    if _CONTROL.search(value):
        raise ValueError("external_id must not contain control characters")
    return value


def _check_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.utcoffset() is None:
        raise ValueError("timestamps must carry a time zone")
    return value


def _check_optional_text(value: str | None) -> str | None:
    return None if value is None else _check_text(value)


def _check_text(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError("text must not contain control characters")
    if _HTML_TAG.search(value):
        raise ValueError("text must be plain text or Markdown, not HTML")
    return value


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccessHints(_Frozen):
    """What the source says about access. Untrusted: ingestion validates and maps every
    value through the source's Lattice configuration and fails the record when it
    cannot (docs/connector-design.md §5)."""

    sensitivity: Level | None = None  # None -> the source's configured default
    grants_only: bool = False  # True -> only explicit grants may read
    department_keys: tuple[str, ...] | None = None  # None -> the source default
    # Groups declared by ``Connector.access_groups`` (e.g. a private channel's ID).
    group_refs: tuple[str, ...] = ()
    # Admin-defined Lattice access groups, by stable key (D-9), and departments by key.
    group_grant_keys: tuple[str, ...] = ()
    department_grant_keys: tuple[str, ...] = ()


class RecordPart(_Frozen):
    """One ordered piece of a record's content: a document section, a ticket comment,
    a chat message."""

    anchor: str = Field(min_length=1, max_length=64)  # stable position within the record
    heading: str | None = None
    author: str | None = None
    occurred_at: datetime | None = None
    text: str

    _aware = field_validator("occurred_at")(_check_aware)
    _plain = field_validator("text", "heading")(_check_optional_text)


class SourceRecord(_Frozen):
    external_id: str  # stable and unique within the source (connector-design §4)
    kind: DocumentKind
    title: str = Field(min_length=1)
    display_ref: str | None = None  # human ID, e.g. "HAL-4821"
    uri: str | None = None
    author: str | None = None
    created_at: datetime | None
    updated_at: datetime
    # The account as the source names it: an account key (document front matter), a
    # ticketing organization ID, a channel ID. Mapped by ingestion, never by a connector.
    account_ref: str | None = None
    access: AccessHints = AccessHints()
    parts: tuple[RecordPart, ...] = Field(min_length=1)
    supersedes_external_id: str | None = None  # explicit replacement, e.g. policy v2
    authority: Authority | None = None  # None -> the source default
    # Small validated extras (ticket status, channel name, the file path of a document).
    extra: dict[str, str | int | bool] = Field(default_factory=dict)

    _id = field_validator("external_id")(_check_external_id)
    _aware = field_validator("created_at", "updated_at")(_check_aware)
    _title = field_validator("title")(_check_text)

    @field_validator("supersedes_external_id")
    @classmethod
    def _supersedes(cls, value: str | None) -> str | None:
        return None if value is None else _check_external_id(value)

    @field_validator("uri")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("uri must be https")
        return value

    @model_validator(mode="after")
    def _anchors_unique(self) -> "SourceRecord":
        anchors = [p.anchor for p in self.parts]
        if len(anchors) != len(set(anchors)):
            raise ValueError("part anchors must be unique within a record")
        return self


class RecordError(_Frozen):
    """A record the connector could not turn into a ``SourceRecord``. Iteration
    continues. There is no free-text message: ``code`` is fixed and ``params`` holds
    short, safe identifiers (a field name, a line number) -- never record content."""

    external_id: str | None = None  # when known
    # Where in the export (e.g. "tickets.jsonl:12"). Operators only: it can be a filename.
    location: str | None = None
    stage: ErrorStage
    code: str
    params: dict[str, str | int] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        if not _ERROR_CODE.fullmatch(value):
            raise ValueError("error codes are lowercase identifiers")
        return value

    @field_validator("params")
    @classmethod
    def _params(cls, value: dict[str, str | int]) -> dict[str, str | int]:
        for key, item in value.items():
            if not _ERROR_CODE.fullmatch(key):
                raise ValueError("param names are lowercase identifiers")
            if isinstance(item, str) and not _PARAM_VALUE.fullmatch(item):
                raise ValueError("param values are short identifiers, never content")
        return value


class ExternalGroup(_Frozen):
    """A source-side access set whose membership governs access (a private channel)."""

    external_ref: str
    name: str
    member_emails: tuple[str, ...] = ()


class ConnectorCapabilities(_Frozen):
    snapshot: bool  # every sync sees every live record (enables the deletion sweep)
    incremental: bool  # supports checkpoints
    acl_export: bool  # reports per-record access hints and/or groups


class SourceUnavailable(Exception):
    """The source as a whole cannot be read (missing export, unreadable directory).
    The sync fails and no deletion sweep runs."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Connector(Protocol):
    @property
    def capabilities(self) -> ConnectorCapabilities: ...

    def check(self) -> None:
        """Raise ``SourceUnavailable`` if the source cannot be read right now."""

    def access_groups(self) -> list[ExternalGroup]:
        """Groups whose membership governs access. May be empty."""

    def records(self) -> Iterator[SourceRecord | RecordError]:
        """Every live record (a full snapshot), in a deterministic order. Never raises
        for a single bad record: yields ``RecordError`` and continues."""
