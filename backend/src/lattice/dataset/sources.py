"""Read the raw source fixtures of one synthetic organization into ``Record``s.

Record keys follow ``docs/evaluation-plan.md`` §3 and the external IDs of
``docs/connector-design.md``:

* ``doc:<front-matter id>`` -- parts ``#1..#n``: the text before the first H1/H2 heading
  (if any) is part 1, then one part per H1/H2 section.
* ``ticket:<ticket id>`` -- parts ``#c1..#cn``: one per comment, in order.
* ``chat:<channel id>:<thread ts>`` (a thread: root plus replies) and
  ``chat:<channel id>:day:<YYYY-MM-DD>`` (a channel's non-threaded messages of one UTC
  day) -- parts ``#<message ts>``.

Parsing problems are collected, not raised, so the lint can report every problem at
once.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

RecordKind = Literal["doc", "ticket", "chat_thread", "chat_channel_day"]
Authority = Literal["system_of_record", "working", "conversational"]

_HEADING = re.compile(r"^#{1,2} (?P<title>.+)$")
_KEYISH = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_TICKET_ID = re.compile(r"^[A-Z]+-\d+$")
_SLACK_TS = re.compile(r"^\d{10}\.\d{6}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must carry a time zone")
    return value.astimezone(UTC)


class FrontMatter(_Strict):
    """Front matter of a document (``docs/connector-design.md`` §3.1)."""

    id: str
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)
    created: datetime
    updated: datetime
    account: str | None = None
    departments: list[str] = Field(default_factory=list)
    sensitivity: Literal["internal", "confidential", "restricted"] | None = None
    grants_only: bool = False
    authority: Authority | None = None
    uri: str | None = None
    supersedes: str | None = None
    # "group:<key>" (an admin-defined access group) or "department:<key>".
    grants: list[str] = Field(default_factory=list)

    _created = field_validator("created", "updated")(_aware)

    @field_validator("id", "supersedes")
    @classmethod
    def _key(cls, value: str | None) -> str | None:
        if value is not None and not _KEYISH.fullmatch(value):
            raise ValueError("must be lowercase letters, digits and hyphens")
        return value

    @field_validator("grants")
    @classmethod
    def _grants(cls, value: list[str]) -> list[str]:
        for grant in value:
            kind, _, key = grant.partition(":")
            if kind not in ("group", "department") or not _KEYISH.fullmatch(key):
                raise ValueError(f"grant {grant!r} must be group:<key> or department:<key>")
        return value

    @field_validator("uri")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("uri must be https")
        return value


class Person(_Strict):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)


class TicketComment(_Strict):
    id: int
    author: Person
    author_role: Literal["end-user", "agent"]
    public: bool
    created_at: datetime
    body: str = Field(min_length=1)

    _created = field_validator("created_at")(_aware)


class Ticket(_Strict):
    """One line of the Zendesk-style export (``docs/connector-design.md`` §3.2)."""

    id: str
    subject: str = Field(min_length=1)
    organization_id: int
    status: Literal["new", "open", "pending", "hold", "solved", "closed"]
    priority: Literal["low", "normal", "high", "urgent"]
    type: Literal["question", "problem", "incident", "task", "feature"]
    via: Literal["email", "web", "api", "chat"]
    group: str
    created_at: datetime
    updated_at: datetime
    tags: list[str]
    requester: Person
    assignee: Person | None
    comments: list[TicketComment] = Field(min_length=1)

    _created = field_validator("created_at", "updated_at")(_aware)

    @field_validator("id")
    @classmethod
    def _ticket_id(cls, value: str) -> str:
        if not _TICKET_ID.fullmatch(value):
            raise ValueError("ticket id must look like HAL-1234")
        return value

    @property
    def number(self) -> int:
        return int(self.id.split("-")[1])


class SlackProfile(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    real_name: str
    email: str = ""


class SlackUser(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    id: str
    team_id: str
    real_name: str
    is_bot: bool
    profile: SlackProfile


class SlackChannel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    id: str
    name: str
    created: int
    creator: str
    members: list[str]


class SlackMessage(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    type: Literal["message"]
    ts: str
    text: str
    user: str | None = None
    subtype: str | None = None
    username: str | None = None
    thread_ts: str | None = None
    reply_count: int | None = None
    replies: list[dict[str, Any]] | None = None

    @field_validator("ts", "thread_ts")
    @classmethod
    def _ts(cls, value: str | None) -> str | None:
        if value is not None and not _SLACK_TS.fullmatch(value):
            raise ValueError("Slack ts must look like 1723380000.000100")
        return value

    @property
    def at(self) -> datetime:
        return datetime.fromtimestamp(float(self.ts), UTC)


@dataclass(frozen=True)
class Part:
    anchor: str
    heading: str | None
    author: str | None
    occurred_at: datetime
    text: str


@dataclass(frozen=True)
class ChannelInfo:
    channel: SlackChannel
    private: bool


@dataclass(frozen=True)
class Record:
    organization: str
    key: str
    kind: RecordKind
    source: str
    path: Path
    title: str
    author: str | None
    created_at: datetime
    updated_at: datetime
    parts: tuple[Part, ...]
    supersedes: str | None
    authority: Authority | None
    # The raw, access-bearing metadata the future connector reports; interpreted only
    # by ``lattice.dataset.access``.
    origin: FrontMatter | Ticket | ChannelInfo

    @property
    def text(self) -> str:
        return "\n".join(part.text for part in self.parts)

    def part(self, anchor: str) -> Part | None:
        return next((p for p in self.parts if p.anchor == anchor), None)


@dataclass
class SourceSet:
    """Everything read from one organization's raw fixtures."""

    organization: str
    records: dict[str, Record] = field(default_factory=dict)
    tickets: list[tuple[Path, Ticket]] = field(default_factory=list)
    slack_users: dict[str, SlackUser] = field(default_factory=dict)
    slack_channels: dict[str, ChannelInfo] = field(default_factory=dict)
    problems: list[tuple[str, str]] = field(default_factory=list)  # (where, message)

    def problem(self, where: Path | str, message: str) -> None:
        self.problems.append((str(where), message))

    def add(self, record: Record) -> None:
        if record.key in self.records:
            self.problem(record.path, f"duplicate record key {record.key}")
            return
        self.records[record.key] = record


def _errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
        for e in exc.errors(include_input=False, include_url=False)
    )


# --- Documents ---------------------------------------------------------------------------


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("missing front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated front matter")
    raw: Any = yaml.safe_load(text[4:end])
    if not isinstance(raw, dict):
        raise ValueError("front matter must be a mapping")
    return raw, text[end + 5 :]


def document_parts(body: str, fm: FrontMatter) -> tuple[Part, ...]:
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
    parts: list[Part] = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if heading is None and not text:
            continue  # no preamble
        parts.append(
            Part(
                anchor=str(len(parts) + 1),
                heading=heading,
                author=fm.author,
                occurred_at=fm.updated,
                text=f"{heading}\n{text}".strip() if heading else text,
            )
        )
    return tuple(parts)


def load_documents(directory: Path, out: SourceSet, source: str) -> None:
    for path in sorted(directory.rglob("*.md")):
        try:
            raw, body = split_front_matter(path.read_text(encoding="utf-8"))
            fm = FrontMatter.model_validate(raw)
        except ValidationError as exc:  # before ValueError, which it subclasses
            out.problem(path, f"front matter: {_errors(exc)}")
            continue
        except (ValueError, yaml.YAMLError) as exc:
            out.problem(path, f"front matter: {exc}")
            continue
        parts = document_parts(body, fm)
        if not parts:
            out.problem(path, "document has no content")
            continue
        out.add(
            Record(
                organization=out.organization,
                key=f"doc:{fm.id}",
                kind="doc",
                source=source,
                path=path,
                title=fm.title,
                author=fm.author,
                created_at=fm.created,
                updated_at=fm.updated,
                parts=parts,
                supersedes=f"doc:{fm.supersedes}" if fm.supersedes else None,
                authority=fm.authority,
                origin=fm,
            )
        )


# --- Tickets -----------------------------------------------------------------------------


def load_tickets(directory: Path, out: SourceSet, source: str) -> None:
    for path in sorted(directory.glob("*.jsonl")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            where = f"{path}:{lineno}"
            if not line.strip():
                out.problem(where, "blank line in JSON Lines export")
                continue
            try:
                ticket = Ticket.model_validate(json.loads(line))
            except json.JSONDecodeError as exc:
                out.problem(where, f"malformed JSON: {exc.msg}")
                continue
            except ValidationError as exc:
                out.problem(where, f"ticket: {_errors(exc)}")
                continue
            out.tickets.append((path, ticket))
            parts = tuple(
                Part(
                    anchor=f"c{i}",
                    heading=f"Comment {i}",
                    author=c.author.name,
                    occurred_at=c.created_at,
                    text=c.body,
                )
                for i, c in enumerate(ticket.comments, start=1)
            )
            out.add(
                Record(
                    organization=out.organization,
                    key=f"ticket:{ticket.id}",
                    kind="ticket",
                    source=source,
                    path=path,
                    title=ticket.subject,
                    author=ticket.requester.name,
                    created_at=ticket.created_at,
                    updated_at=ticket.updated_at,
                    parts=parts,
                    supersedes=None,
                    authority=None,
                    origin=ticket,
                )
            )


# --- Slack -------------------------------------------------------------------------------


def _load_json_list(path: Path, out: SourceSet) -> list[Any]:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        out.problem(path, "missing")
        return []
    except json.JSONDecodeError as exc:
        out.problem(path, f"malformed JSON: {exc.msg}")
        return []
    if not isinstance(value, list):
        out.problem(path, "expected a JSON list")
        return []
    return value


def load_slack(directory: Path, out: SourceSet, source: str) -> None:
    slack = directory
    for raw in _load_json_list(slack / "users.json", out):
        try:
            user = SlackUser.model_validate(raw)
        except ValidationError as exc:
            out.problem(slack / "users.json", _errors(exc))
            continue
        out.slack_users[user.id] = user
    for name, private in (("channels.json", False), ("groups.json", True)):
        for raw in _load_json_list(slack / name, out):
            try:
                channel = SlackChannel.model_validate(raw)
            except ValidationError as exc:
                out.problem(slack / name, _errors(exc))
                continue
            if channel.id in out.slack_channels:
                out.problem(slack / name, f"duplicate channel id {channel.id}")
            out.slack_channels[channel.id] = ChannelInfo(channel, private)

    by_name = {c.channel.name: c for c in out.slack_channels.values()}
    for folder in sorted(p for p in slack.iterdir() if p.is_dir()):
        info = by_name.get(folder.name)
        if info is None:
            out.problem(folder, "message folder for a channel not in channels.json/groups.json")
            continue
        _load_channel(folder, info, out, source)


def _display(out: SourceSet, message: SlackMessage) -> str:
    if message.user and message.user in out.slack_users:
        return out.slack_users[message.user].real_name
    return message.username or message.user or "unknown"


def _load_channel(folder: Path, info: ChannelInfo, out: SourceSet, source: str) -> None:
    cid = info.channel.id
    messages: dict[str, SlackMessage] = {}
    for path in sorted(folder.glob("*.json")):
        for raw in _load_json_list(path, out):
            try:
                message = SlackMessage.model_validate(raw)
            except ValidationError as exc:
                out.problem(path, _errors(exc))
                continue
            if message.at.date().isoformat() != path.stem:
                out.problem(path, f"message {message.ts} is not from {path.stem} (UTC)")
            if message.ts in messages:
                out.problem(path, f"duplicate ts {message.ts} in #{info.channel.name}")
            if message.subtype != "bot_message" and message.user not in out.slack_users:
                out.problem(path, f"message {message.ts}: unknown user {message.user}")
            messages[message.ts] = message

    threads: dict[str, list[SlackMessage]] = defaultdict(list)
    days: dict[str, list[SlackMessage]] = defaultdict(list)
    for message in sorted(messages.values(), key=lambda m: float(m.ts)):
        if message.thread_ts and message.thread_ts != message.ts:
            if message.thread_ts not in messages:
                out.problem(folder, f"reply {message.ts} to missing thread {message.thread_ts}")
                continue
            threads[message.thread_ts].append(message)
        elif message.reply_count:
            threads.setdefault(message.ts, [])
        else:
            days[message.at.date().isoformat()].append(message)

    for root_ts, replies in threads.items():
        root = messages[root_ts]
        if root.reply_count != len(replies) or [r["ts"] for r in root.replies or []] != [
            r.ts for r in replies
        ]:
            out.problem(folder, f"thread {root_ts}: reply_count/replies do not match the replies")
        chain = [root, *replies]
        out.add(
            _chat_record(out, info, folder, f"chat:{cid}:{root_ts}", "chat_thread", chain, source)
        )
    for day, chain in days.items():
        out.add(
            _chat_record(
                out, info, folder, f"chat:{cid}:day:{day}", "chat_channel_day", chain, source
            )
        )


def _chat_record(
    out: SourceSet,
    info: ChannelInfo,
    folder: Path,
    key: str,
    kind: RecordKind,
    chain: list[SlackMessage],
    source: str,
) -> Record:
    parts = tuple(
        Part(anchor=m.ts, heading=None, author=_display(out, m), occurred_at=m.at, text=m.text)
        for m in chain
    )
    name = info.channel.name
    title = (
        f"#{name} · thread by {_display(out, chain[0])}"
        if kind == "chat_thread"
        else f"#{name} · {chain[0].at.date().isoformat()}"
    )
    return Record(
        organization=out.organization,
        key=key,
        kind=kind,
        source=source,
        path=folder,
        title=title,
        author=_display(out, chain[0]),
        created_at=chain[0].at,
        updated_at=chain[-1].at,
        parts=parts,
        supersedes=None,
        authority=None,
        origin=info,
    )


SOURCE_LOADERS = {
    "document_repository": load_documents,
    "ticketing": load_tickets,
    "chat_export": load_slack,
}
