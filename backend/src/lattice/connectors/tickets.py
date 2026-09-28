"""``ticketing``: a Zendesk-style JSON Lines export (docs/connector-design.md §3.2).

* Every ``*.jsonl`` file of the directory is one page of the export, read in name order.
* ``external_id`` and ``display_ref`` = the ticket ID (``HAL-4821``). A ticket ID that
  appears twice is rejected in every occurrence.
* Parts: a header (subject, status, priority, type, requester, assignee, tags; anchor
  ``header``) and one part per comment (anchors ``c1..cn``). Internal (non-public)
  comments are included and marked: they are internal-level content.
* ``account_ref`` = the ticketing tool's organization ID, mapped to an account by
  ingestion; the source is account-scoped, so unmapped tickets are not indexed.
* Emails are never copied into content: people appear by name only.
* Capabilities: snapshot. No per-record ACLs (source defaults apply).
"""

import json
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lattice.connectors.contract import (
    ConnectorCapabilities,
    ExternalGroup,
    RecordError,
    RecordPart,
    SourceRecord,
    SourceUnavailable,
    normalize_external_id,
)


class TicketsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path


class _Person(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    name: str = Field(min_length=1)


class _Comment(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: int
    author: _Person
    author_role: Literal["end-user", "agent"]
    public: bool
    created_at: datetime
    body: str = Field(min_length=1)


class _Ticket(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    organization_id: int | None
    status: str
    priority: str
    type: str | None = None
    created_at: datetime
    updated_at: datetime
    tags: list[str] = Field(default_factory=list)
    requester: _Person
    assignee: _Person | None = None
    comments: list[_Comment] = Field(min_length=1)


def _header(ticket: _Ticket) -> str:
    lines = [
        f"Subject: {ticket.subject}",
        f"Status: {ticket.status} · Priority: {ticket.priority}"
        + (f" · Type: {ticket.type}" if ticket.type else ""),
        f"Requester: {ticket.requester.name}",
    ]
    if ticket.assignee:
        lines.append(f"Assignee: {ticket.assignee.name}")
    if ticket.tags:
        lines.append(f"Tags: {', '.join(ticket.tags)}")
    return "\n".join(lines)


def _comment_heading(index: int, comment: _Comment) -> str:
    role = "customer" if comment.author_role == "end-user" else "agent"
    visibility = "" if comment.public else ", internal note"
    return f"Comment {index} · {comment.author.name} ({role}{visibility})"


def _field(exc: ValidationError) -> str:
    loc = exc.errors(include_input=False, include_url=False)[0].get("loc") or ("record",)
    return str(loc[0])


class TicketsConnector:
    connector_type: ClassVar[Literal["ticketing"]] = "ticketing"
    capabilities: ClassVar[ConnectorCapabilities] = ConnectorCapabilities(
        snapshot=True, incremental=False, acl_export=False
    )

    def __init__(self, config: TicketsConfig) -> None:
        self.root = config.path

    def check(self) -> None:
        if not self.root.is_dir():
            raise SourceUnavailable("source_unavailable:missing_export")

    def access_groups(self) -> list[ExternalGroup]:
        return []

    def records(self) -> Iterator[SourceRecord | RecordError]:
        self.check()
        parsed: list[tuple[str, _Ticket] | RecordError] = []
        for path in sorted(self.root.glob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                parsed.append(RecordError(location=path.name, stage="read", code="unreadable_file"))
                continue
            for lineno, line in enumerate(lines, start=1):
                parsed.append(self._parse(f"{path.name}:{lineno}", line))

        counts = Counter(
            normalize_external_id(item[1].id) for item in parsed if isinstance(item, tuple)
        )
        for item in parsed:
            if isinstance(item, RecordError):
                yield item
                continue
            location, ticket = item
            external_id = normalize_external_id(ticket.id)
            if counts[external_id] > 1:
                yield RecordError(
                    external_id=external_id,
                    location=location,
                    stage="validate",
                    code="duplicate_external_id",
                )
                continue
            yield self._record(location, ticket)

    def _parse(self, location: str, line: str) -> tuple[str, _Ticket] | RecordError:
        if not line.strip():
            return RecordError(location=location, stage="parse", code="blank_line")
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError:
            return RecordError(location=location, stage="parse", code="malformed_json")
        if not isinstance(raw, dict):
            return RecordError(location=location, stage="parse", code="malformed_json")
        try:
            return location, _Ticket.model_validate(raw)
        except ValidationError as exc:
            ident = raw.get("id")
            return RecordError(
                external_id=normalize_external_id(ident) if isinstance(ident, str) else None,
                location=location,
                stage="validate",
                code="invalid_record",
                params={"field": _field(exc)},
            )

    def _record(self, location: str, ticket: _Ticket) -> SourceRecord | RecordError:
        external_id = normalize_external_id(ticket.id)
        parts = [RecordPart(anchor="header", text=_header(ticket), occurred_at=ticket.created_at)]
        for i, comment in enumerate(ticket.comments, start=1):
            parts.append(
                RecordPart(
                    anchor=f"c{i}",
                    heading=_comment_heading(i, comment),
                    author=comment.author.name,
                    occurred_at=comment.created_at,
                    text=comment.body.strip(),
                )
            )
        extra: dict[str, str | int | bool] = {
            "status": ticket.status,
            "priority": ticket.priority,
        }
        if ticket.type:
            extra["type"] = ticket.type
        try:
            return SourceRecord(
                external_id=external_id,
                kind="ticket",
                title=ticket.subject,
                display_ref=external_id,
                author=ticket.requester.name,
                created_at=ticket.created_at,
                updated_at=ticket.updated_at,
                account_ref=None if ticket.organization_id is None else str(ticket.organization_id),
                parts=tuple(parts),
                extra=extra,
            )
        except ValidationError as exc:
            return RecordError(
                external_id=external_id,
                location=location,
                stage="validate",
                code="invalid_record",
                params={"field": _field(exc)},
            )
