"""``chat_export``: the Slack export format (docs/connector-design.md §3.3).

Layout: ``users.json``, ``channels.json`` (public), ``groups.json`` (private, with
``members``) and ``<channel name>/<YYYY-MM-DD>.json`` message files.

* A thread (a root with replies) is one ``chat_thread`` record,
  ``external_id = <channel id>:<thread ts>``; non-threaded messages form one
  ``chat_channel_day`` record per channel per UTC day,
  ``external_id = <channel id>:day:<YYYY-MM-DD>``. Parts are messages (anchor = ts).
* Private channels are reported as ``ExternalGroup``s whose members are the channel's
  users' emails; their records carry ``grants_only`` and a group reference, so only
  channel members can read them -- Slack's own semantics.
* ``account_ref`` = the channel ID. Whether a channel belongs to an account, and a
  channel's sensitivity, are Lattice configuration applied by ingestion.
* Slack markup is made readable: ``<@U123>`` becomes ``@Real Name``; ``&amp;``,
  ``&lt;`` and ``&gt;`` are unescaped.
* Capabilities: snapshot, ACL export. Not incremental (exports are full snapshots).
"""

import json
import re
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lattice.connectors.contract import (
    AccessHints,
    ConnectorCapabilities,
    DocumentKind,
    ExternalGroup,
    RecordError,
    RecordPart,
    SourceRecord,
    SourceUnavailable,
    normalize_external_id,
)

_TS = re.compile(r"^\d{10}\.\d{6}$")
_MENTION = re.compile(r"<@([A-Z0-9]+)>")


class SlackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path


class _Profile(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    email: str = ""


class _User(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str
    real_name: str
    profile: _Profile = _Profile()


class _Channel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    members: list[str] = Field(default_factory=list)


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    type: Literal["message"]
    ts: str
    text: str
    user: str | None = None
    subtype: str | None = None
    username: str | None = None
    thread_ts: str | None = None
    reply_count: int | None = None

    @field_validator("ts", "thread_ts")
    @classmethod
    def _ts(cls, value: str | None) -> str | None:
        if value is not None and not _TS.fullmatch(value):
            raise ValueError("invalid ts")
        return value

    @property
    def at(self) -> datetime:
        return datetime.fromtimestamp(float(self.ts), UTC)


class _Unavailable(Exception):
    pass


class SlackConnector:
    connector_type: ClassVar[Literal["chat_export"]] = "chat_export"
    capabilities: ClassVar[ConnectorCapabilities] = ConnectorCapabilities(
        snapshot=True, incremental=False, acl_export=True
    )

    def __init__(self, config: SlackConfig) -> None:
        self.root = config.path
        self._loaded: tuple[dict[str, _User], dict[str, tuple[_Channel, bool]]] | None = None

    # --- export metadata -----------------------------------------------------------------

    def check(self) -> None:
        if not self.root.is_dir():
            raise SourceUnavailable("source_unavailable:missing_export")
        for name in ("users.json", "channels.json"):
            if not (self.root / name).is_file():
                raise SourceUnavailable("source_unavailable:incomplete_export")

    def _json_list(self, name: str, *, required: bool) -> list[Any]:
        path = self.root / name
        if not path.is_file():
            if required:
                raise SourceUnavailable("source_unavailable:incomplete_export")
            return []
        try:
            value: Any = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise SourceUnavailable("source_unavailable:malformed_export") from None
        if not isinstance(value, list):
            raise SourceUnavailable("source_unavailable:malformed_export")
        return value

    def _metadata(self) -> tuple[dict[str, _User], dict[str, tuple[_Channel, bool]]]:
        """Users and channels. Without them no message can be attributed or no private
        channel's membership known, so a problem here makes the source unavailable."""
        if self._loaded is None:
            self.check()
            try:
                users = {
                    u.id: u
                    for u in (
                        _User.model_validate(raw)
                        for raw in self._json_list("users.json", required=True)
                    )
                }
                channels: dict[str, tuple[_Channel, bool]] = {}
                for name, private in (("channels.json", False), ("groups.json", True)):
                    for raw in self._json_list(name, required=not private):
                        channel = _Channel.model_validate(raw)
                        if channel.id in channels:
                            raise SourceUnavailable("source_unavailable:malformed_export")
                        channels[channel.id] = (channel, private)
            except ValidationError:
                raise SourceUnavailable("source_unavailable:malformed_export") from None
            self._loaded = (users, channels)
        return self._loaded

    def access_groups(self) -> list[ExternalGroup]:
        users, channels = self._metadata()
        return [
            ExternalGroup(
                external_ref=channel.id,
                name=f"#{channel.name}",
                member_emails=tuple(
                    sorted(
                        users[u].profile.email.lower()
                        for u in channel.members
                        if u in users and users[u].profile.email
                    )
                ),
            )
            for channel, private in sorted(channels.values(), key=lambda c: c[0].id)
            if private
        ]

    # --- records -----------------------------------------------------------------------

    def records(self) -> Iterator[SourceRecord | RecordError]:
        users, channels = self._metadata()
        by_name = {channel.name: (channel, private) for channel, private in channels.values()}
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            found = by_name.get(folder.name)
            if found is None:
                yield RecordError(location=folder.name, stage="validate", code="unknown_channel")
                continue
            yield from self._channel(folder, *found, users)

    def _channel(
        self, folder: Path, channel: _Channel, private: bool, users: dict[str, _User]
    ) -> Iterator[SourceRecord | RecordError]:
        messages: dict[str, _Message] = {}
        for path in sorted(folder.glob("*.json")):
            location = f"{folder.name}/{path.name}"
            try:
                raw: Any = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                yield RecordError(location=location, stage="parse", code="malformed_json")
                continue
            if not isinstance(raw, list):
                yield RecordError(location=location, stage="parse", code="malformed_json")
                continue
            for item in raw:
                try:
                    message = _Message.model_validate(item)
                except ValidationError:
                    yield RecordError(location=location, stage="validate", code="invalid_message")
                    continue
                messages[message.ts] = message

        # A root is any message a reply points to, even without ``reply_count``, so a
        # root never lands in both its thread and its day record.
        roots = {m.thread_ts for m in messages.values() if m.thread_ts and m.thread_ts != m.ts} | {
            m.ts for m in messages.values() if m.reply_count
        }
        threads: dict[str, list[_Message]] = defaultdict(list)
        days: dict[str, list[_Message]] = defaultdict(list)
        for message in sorted(messages.values(), key=lambda m: float(m.ts)):
            if message.thread_ts and message.thread_ts != message.ts:
                if message.thread_ts not in messages:
                    yield RecordError(
                        external_id=f"{channel.id}:{message.thread_ts}",
                        location=folder.name,
                        stage="validate",
                        code="orphan_reply",
                    )
                    continue
                threads[message.thread_ts].append(message)
            elif message.ts in roots:
                threads.setdefault(message.ts, [])
            else:
                days[message.at.date().isoformat()].append(message)

        for root_ts in sorted(threads, key=float):
            chain = [messages[root_ts], *threads[root_ts]]
            yield self._record(
                f"{channel.id}:{root_ts}", "chat_thread", channel, private, chain, users
            )
        for day in sorted(days):
            yield self._record(
                f"{channel.id}:day:{day}", "chat_channel_day", channel, private, days[day], users
            )

    def _record(
        self,
        external_id: str,
        kind: DocumentKind,
        channel: _Channel,
        private: bool,
        chain: list[_Message],
        users: dict[str, _User],
    ) -> SourceRecord | RecordError:
        def speaker(message: _Message) -> str:
            if message.user and message.user in users:
                return users[message.user].real_name
            return message.username or "unknown"

        def readable(text: str) -> str:
            text = _MENTION.sub(
                lambda m: f"@{users[m[1]].real_name}" if m[1] in users else "@unknown", text
            )
            return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

        title = (
            f"#{channel.name} · thread by {speaker(chain[0])}"
            if kind == "chat_thread"
            else f"#{channel.name} · {chain[0].at.date().isoformat()}"
        )
        try:
            return SourceRecord(
                external_id=normalize_external_id(external_id),
                kind=kind,
                title=title,
                author=speaker(chain[0]),
                created_at=chain[0].at,
                updated_at=chain[-1].at,
                account_ref=channel.id,
                access=AccessHints(
                    grants_only=private, group_refs=(channel.id,) if private else ()
                ),
                parts=tuple(
                    RecordPart(
                        anchor=m.ts, author=speaker(m), occurred_at=m.at, text=readable(m.text)
                    )
                    for m in chain
                ),
                extra={"channel": f"#{channel.name}", "private_channel": private},
            )
        except ValidationError:
            return RecordError(
                external_id=external_id,
                location=channel.name,
                stage="validate",
                code="invalid_record",
            )
