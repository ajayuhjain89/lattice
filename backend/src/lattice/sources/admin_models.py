"""Request and response models of source administration (P12.6; phase-12-plan C7-C9).

Every response is operational metadata: settings an administrator chose, counts, fixed
codes, sizes, a digest, times and IDs. Never a document, ticket or message body, a file
name from an import, a private channel's name, or a path.
"""

import re
import unicodedata
import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lattice.sources.config import _KEY

ConnectorType = Literal["document_repository", "ticketing", "chat_export"]
DefaultLevel = Literal["internal", "confidential"]
Authority = Literal["system_of_record", "working", "conversational"]
ManagedBy = Literal["operator", "organization"]
ImportStatus = Literal["receiving", "validating", "ready", "rejected", "superseded", "deleted"]
# What the administrator can usefully do next, decided by the server (never inferred).
NextAction = Literal[
    "upload_import",  # an organization-managed source with no usable import
    "replace_import",  # the current import cannot be synced (its check failed as a whole)
    "wait",  # a check or sync is queued or running
    "map_references",  # account references the latest check or sync could not map
    "start_sync",  # an import is ready and has never been synced
    "retry_sync",  # the latest sync failed
    "resume",  # paused
    "none",
]
Effect = Literal["narrows", "widens", "neutral"]

MAX_REASON = 500
_CHANNEL = re.compile(r"^[A-Z0-9]{2,32}$")


def _text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Reasoned(_Request):
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _reason_has_text(self) -> Self:
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


def _departments(value: list[str]) -> list[str]:
    if len(value) > 50:
        raise ValueError("at most 50 departments")
    if len(set(value)) != len(value):
        raise ValueError("departments must be distinct")
    for key in value:
        if not _KEY.fullmatch(key):
            raise ValueError("departments are named by key")
    return sorted(value)


class NewSourceRequest(_Reasoned):
    connector_type: ConnectorType
    key: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=200)
    default_sensitivity: DefaultLevel
    default_departments: list[str] = Field(default_factory=list)
    authority: Authority
    account_scoped: bool

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not _KEY.fullmatch(value):
            raise ValueError(
                "lowercase letters, digits and hyphens, starting with a letter or digit"
            )
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = _text(value)
        if not value:
            raise ValueError("name must not be blank")
        return value

    _depts = field_validator("default_departments")(_departments)


class SourceChangeRequest(_Reasoned):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    default_sensitivity: DefaultLevel | None = None
    default_departments: list[str] | None = None
    authority: Authority | None = None
    account_scoped: bool | None = None
    # Chat sources: channel ID -> sensitivity, the whole map (replaces the current one).
    channel_sensitivities: dict[str, DefaultLevel] | None = None
    # A change that makes records readable to more people must say so (preview first).
    acknowledge_widening: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = _text(value)
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("default_departments")
    @classmethod
    def _depts(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _departments(value)

    @field_validator("channel_sensitivities")
    @classmethod
    def _channels(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if len(value) > 500:
            raise ValueError("at most 500 channels")
        for channel in value:
            if not _CHANNEL.fullmatch(channel):
                raise ValueError("channels are named by their Slack channel ID")
        return dict(sorted(value.items()))

    def change(self) -> dict[str, object]:
        fields = (
            "name",
            "default_sensitivity",
            "default_departments",
            "authority",
            "account_scoped",
            "channel_sensitivities",
        )
        return {f: getattr(self, f) for f in fields if getattr(self, f) is not None}


class VersionedRequest(_Reasoned):
    version: int = Field(ge=1)


class DisconnectRequest(VersionedRequest):
    # The source's key, typed by the administrator (the dialog shows it).
    confirm_key: str = Field(min_length=1, max_length=63)


# --- responses ---------------------------------------------------------------------------


class DepartmentRef(BaseModel):
    key: str
    name: str | None


class SourceSettings(BaseModel):
    name: str
    default_sensitivity: str
    default_departments: list[DepartmentRef]
    authority: str
    account_scoped: bool
    channel_sensitivities: dict[str, str] = Field(
        description="Chat sources: each configured channel's level, by channel ID (IDs only, "
        "never names)."
    )


class FailureCodeCount(BaseModel):
    code: str
    count: int


class UnresolvedReference(BaseModel):
    external_ref: str = Field(
        description="The exact reference (a ticketing organization ID or a channel ID)."
    )
    records: int
    state: Literal["unmapped", "mapped"]


class ImportCheck(BaseModel):
    """The deterministic check of an import: the sync pipeline as a dry run (no writes,
    no provider call). Counts and codes only."""

    checked_at: datetime
    error_code: str | None = Field(
        description="The import cannot be synced as a whole (e.g. a Slack export without "
        "users.json); null when the check read it."
    )
    records_seen: int
    would_create: int
    would_update: int
    would_update_access: int
    unchanged: int
    invalid_records: int
    would_quarantine: int
    would_delete: int
    deletion_guard: str | None
    failure_codes: list[FailureCodeCount]
    unresolved_references: list[UnresolvedReference]
    unresolved_total: int


class ImportOut(BaseModel):
    id: uuid.UUID
    status: ImportStatus
    format: Literal["zip", "jsonl"]
    current: bool = Field(description="The import the next sync reads.")
    size_bytes: int | None
    sha256: str | None
    entries: int | None = Field(description="Files kept (the ones this source type reads).")
    ignored_entries: int | None
    expanded_bytes: int | None
    rejected_code: str | None
    created_at: datetime
    received_at: datetime | None
    ready_at: datetime | None
    superseded_at: datetime | None
    check: ImportCheck | None


class UploadLimits(BaseModel):
    available: bool = Field(description="False: this deployment has no import area.")
    formats: list[Literal["zip", "jsonl"]]
    max_upload_bytes: int
    max_expanded_bytes: int
    max_file_bytes: int
    max_entries: int


class SourceJob(BaseModel):
    id: uuid.UUID
    kind: Literal["source_sync", "import_check"]
    mode: str
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    sync_run_id: uuid.UUID | None
    import_id: uuid.UUID | None


class SourceDetail(BaseModel):
    id: uuid.UUID
    key: str
    connector_type: ConnectorType
    managed_by: ManagedBy
    status: Literal["active", "paused"]
    version: int
    settings: SourceSettings
    documents: int = Field(description="Active documents in the source (aggregate).")
    created_at: datetime
    last_synced_at: datetime | None
    last_succeeded_at: datetime | None
    current_import: ImportOut | None
    latest_upload: ImportOut | None = Field(
        description="The most recent upload when it is not the current import (e.g. one "
        "being received, or rejected)."
    )
    job: SourceJob | None
    next_action: NextAction
    can_sync: bool
    sync_blocked_by: str | None
    upload: UploadLimits


class SourceCreated(BaseModel):
    id: uuid.UUID
    version: int


class SettingChange(BaseModel):
    field: str
    before: str | None
    after: str | None
    effect: Effect


class SourceChangePreview(BaseModel):
    changes: list[SettingChange]
    widens: bool
    takes_effect: Literal["next_sync"] = "next_sync"


class SourceVersion(BaseModel):
    version: int


class DisconnectPreview(BaseModel):
    documents: int
    chunks: int
    account_mappings: int
    imports: int
    queued_jobs: int
    sync_running: bool
    operator_managed: bool = Field(
        description="An operator's sources file configures it: re-running the file "
        "connects it again as a new source."
    )


class DisconnectResult(BaseModel):
    documents: int
    chunks: int
    account_mappings: int
    imports: int
    cancelled_jobs: int
    purged: bool


class UploadResult(BaseModel):
    import_: ImportOut = Field(alias="import")
    check_queued: bool

    model_config = ConfigDict(populate_by_name=True)
