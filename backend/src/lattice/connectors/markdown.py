"""``document_repository``: Markdown files with YAML front matter, mirroring a
Drive/Notion export plus its sharing manifest (docs/connector-design.md §3.1).

* ``external_id`` = the front-matter ``id`` (not the path, so a rename is not a
  delete + create). Two files with the same ``id`` are both rejected.
* Parts: the text before the first H1/H2 heading (if any), then one part per H1/H2
  section (headings inside fenced code blocks do not count). Anchors ``1..n``.
* Access hints from front matter: ``sensitivity``, ``grants_only``, ``departments``
  (department keys), ``grants`` (``group:<key>`` / ``department:<key>``), ``account``
  (an account key). All untrusted; mapped by ingestion.
* Capabilities: snapshot, ACL export. Not incremental (a local export is read whole).
"""

import re
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lattice.connectors.contract import (
    AccessHints,
    ConnectorCapabilities,
    RecordError,
    RecordPart,
    SourceRecord,
    SourceUnavailable,
    normalize_external_id,
)

_HEADING = re.compile(r"^#{1,2} (?P<title>.+)$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class MarkdownConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path


class _FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)
    created: datetime
    updated: datetime
    account: str | None = None
    departments: list[str] = Field(default_factory=list)
    sensitivity: Literal["internal", "confidential", "restricted"] | None = None
    grants_only: bool = False
    authority: Literal["system_of_record", "working", "conversational"] | None = None
    uri: str | None = None
    supersedes: str | None = None
    grants: list[str] = Field(default_factory=list)

    @field_validator("grants")
    @classmethod
    def _grants(cls, value: list[str]) -> list[str]:
        for grant in value:
            kind, _, key = grant.partition(":")
            if kind not in ("group", "department") or not _KEY.fullmatch(key):
                raise ValueError("grants must be group:<key> or department:<key>")
        return value


class _Unreadable(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise _Unreadable("missing_front_matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise _Unreadable("unterminated_front_matter")
    try:
        raw: Any = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        raise _Unreadable("malformed_front_matter") from None
    if not isinstance(raw, dict):
        raise _Unreadable("malformed_front_matter")
    return raw, text[end + 5 :]


def _sections(body: str) -> list[tuple[str | None, str]]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    fence = False
    for line in body.splitlines():
        if line.startswith("```"):
            fence = not fence
        match = None if fence else _HEADING.match(line)
        if match:
            sections.append((match["title"].strip(), []))
        else:
            sections[-1][1].append(line)
    return [
        (heading, "\n".join(lines).strip())
        for heading, lines in sections
        if heading is not None or "\n".join(lines).strip()
    ]


def _field(exc: ValidationError) -> str:
    loc = exc.errors(include_input=False, include_url=False)[0].get("loc") or ("record",)
    return str(loc[0])


class MarkdownConnector:
    connector_type: ClassVar[Literal["document_repository"]] = "document_repository"
    capabilities: ClassVar[ConnectorCapabilities] = ConnectorCapabilities(
        snapshot=True, incremental=False, acl_export=True
    )

    def __init__(self, config: MarkdownConfig) -> None:
        self.root = config.path

    def check(self) -> None:
        if not self.root.is_dir():
            raise SourceUnavailable("source_unavailable:missing_export")

    def access_groups(self) -> list[Any]:
        return []  # front matter grants name Lattice groups by key; nothing to sync

    def _files(self) -> list[Path]:
        return sorted(self.root.rglob("*.md"))

    def records(self) -> Iterator[SourceRecord | RecordError]:
        self.check()
        parsed: list[tuple[Path, _FrontMatter, str] | RecordError] = []
        for path in self._files():
            location = path.relative_to(self.root).as_posix()
            try:
                raw, body = _split_front_matter(path.read_text(encoding="utf-8"))
                fm = _FrontMatter.model_validate(raw)
            except UnicodeDecodeError:
                parsed.append(RecordError(location=location, stage="read", code="unreadable_file"))
            except _Unreadable as exc:
                parsed.append(RecordError(location=location, stage="parse", code=exc.code))
            except ValidationError as exc:
                ident = raw.get("id") if isinstance(raw.get("id"), str) else None
                parsed.append(
                    RecordError(
                        external_id=normalize_external_id(ident) if ident else None,
                        location=location,
                        stage="validate",
                        code="invalid_record",
                        params={"field": _field(exc)},
                    )
                )
            else:
                parsed.append((path, fm, body))

        counts = Counter(normalize_external_id(p[1].id) for p in parsed if isinstance(p, tuple))
        for item in parsed:
            if isinstance(item, RecordError):
                yield item
                continue
            path, fm, body = item
            location = path.relative_to(self.root).as_posix()
            external_id = normalize_external_id(fm.id)
            if counts[external_id] > 1:
                yield RecordError(
                    external_id=external_id,
                    location=location,
                    stage="validate",
                    code="duplicate_external_id",
                )
                continue
            yield self._record(fm, body, location)

    def _record(self, fm: _FrontMatter, body: str, location: str) -> SourceRecord | RecordError:
        sections = _sections(body)
        if not sections:
            return RecordError(
                external_id=normalize_external_id(fm.id),
                location=location,
                stage="validate",
                code="empty_record",
            )
        group_keys = tuple(g.partition(":")[2] for g in fm.grants if g.startswith("group:"))
        department_keys = tuple(
            g.partition(":")[2] for g in fm.grants if g.startswith("department:")
        )
        try:
            return SourceRecord(
                external_id=normalize_external_id(fm.id),
                kind="doc",
                title=fm.title,
                uri=fm.uri,
                author=fm.author,
                created_at=fm.created,
                updated_at=fm.updated,
                account_ref=fm.account,
                access=AccessHints(
                    sensitivity=fm.sensitivity,
                    grants_only=fm.grants_only,
                    department_keys=tuple(fm.departments) or None,
                    group_grant_keys=group_keys,
                    department_grant_keys=department_keys,
                ),
                parts=tuple(
                    RecordPart(
                        anchor=str(i),
                        heading=heading,
                        author=fm.author,
                        occurred_at=fm.updated,
                        text=text,
                    )
                    for i, (heading, text) in enumerate(sections, start=1)
                ),
                supersedes_external_id=fm.supersedes,
                authority=fm.authority,
                extra={"path": location},
            )
        except ValidationError as exc:
            return RecordError(
                external_id=normalize_external_id(fm.id),
                location=location,
                stage="validate",
                code="invalid_record",
                params={"field": _field(exc)},
            )
