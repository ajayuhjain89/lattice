"""The account-administration contract (Phase 12, P12.3; phase-12-plan C10): what an
administrator is shown and may send about accounts and their external references.

Accounts are the administrator's directory (operational metadata, already visible to
administrators in Access); their names and aliases never become search text,
embeddings or suggestions (D-10). A reference is the exact value a source reports -- a
ticketing organization ID or a chat channel ID -- never a channel *name*, a title or
anything from a record's content (C9, D-18). Counts of records waiting are counts only.
"""

import unicodedata
import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lattice.access.models import MAX_REASON

BusinessStatus = Literal["active", "churned", "prospect"]
Lifecycle = Literal["active", "archived"]
RefKind = Literal["ticketing_organization", "chat_channel"]
MappingOperation = Literal["map", "remap", "unmap"]
MappingEffect = Literal["indexed", "scoped", "moved", "quarantined", "unscoped"]
MAX_ALIASES = 20
MAX_ALIAS = 100
MAX_NAME = 200
_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
_CONTROL = frozenset(chr(c) for c in (*range(32), 127))


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class _In(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _text(value: str, field: str) -> str:
    """NFC, trimmed, inner whitespace collapsed; no control characters."""
    cleaned = " ".join(unicodedata.normalize("NFC", value).split())
    if any(c in _CONTROL for c in value.replace("\t", " ").replace("\n", " ")):
        raise ValueError(f"{field} must not contain control characters")
    return cleaned


def normalise_name(value: str) -> str:
    name = _text(value, "name")
    if not 1 <= len(name) <= MAX_NAME:
        raise ValueError(f"name must be 1-{MAX_NAME} characters")
    return name


def normalise_aliases(values: list[str]) -> list[str]:
    """Each alias normalised like a name; blanks dropped; duplicates (ignoring case)
    dropped, first spelling kept."""
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        alias = _text(value, "aliases")
        if not alias:
            continue
        if len(alias) > MAX_ALIAS:
            raise ValueError(f"each alias must be at most {MAX_ALIAS} characters")
        if alias.casefold() in seen:
            continue
        seen.add(alias.casefold())
        aliases.append(alias)
    if len(aliases) > MAX_ALIASES:
        raise ValueError(f"at most {MAX_ALIASES} aliases")
    return aliases


def normalise_external_ref(value: str) -> str:
    """As connectors report references: NFC, trimmed, case preserved -- then matched
    exactly. Never lower-cased or otherwise made 'similar'."""
    ref = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(ref) <= 256 or any(c in _CONTROL for c in ref):
        raise ValueError("external_ref must be 1-256 characters without control characters")
    return ref


def _reason(value: str) -> None:
    if not value.strip():
        raise ValueError("reason must not be blank")


# --- read models --------------------------------------------------------------------------


class SourceRef(_Out):
    id: uuid.UUID
    name: str
    connector_type: str


class AccountRef(_Out):
    id: uuid.UUID
    key: str
    name: str
    lifecycle: Lifecycle


class AccountSummary(_Out):
    id: uuid.UUID
    key: str = Field(description="Immutable: what document front matter and an Ask scope name.")
    name: str
    aliases: list[str]
    status: BusinessStatus = Field(description="A business label with no effect on access.")
    lifecycle: Lifecycle = Field(
        description="archived: kept with its history and mappings; refuses new mappings."
    )
    archived_at: datetime | None
    references: int = Field(description="External references mapped to this account.")
    assigned_members: int = Field(description="Members assigned this account (directory).")


class AccountsPage(_Out):
    items: list[AccountSummary]
    next_after: uuid.UUID | None = Field(
        description="Pass as `after` for the next page; null on the last page."
    )


class AccountMapping(_Out):
    source: SourceRef
    ref_kind: RefKind
    external_ref: str
    created_at: datetime
    updated_at: datetime


class AccountDetail(AccountSummary):
    version: datetime = Field(description="Send back with a change (409 when it moved on).")
    created_at: datetime
    mappings: list[AccountMapping]


class UnmappedReference(_Out):
    source: SourceRef
    ref_kind: RefKind
    external_ref: str = Field(
        description="The exact reference the source reported: a ticketing organization ID "
        "or a channel ID -- never a channel name or anything from a record."
    )
    records_waiting: int = Field(
        description="Records of the source's latest run not indexed for want of a mapping."
    )
    seen_at: datetime = Field(description="When that run started.")
    state: Literal["unmapped", "mapped_pending_sync"]
    account: AccountRef | None = Field(
        description="The account it is now mapped to, if mapped since that run."
    )


class UnmappedReferences(_Out):
    items: list[UnmappedReference]


class MappingPreview(_Out):
    """Now → After for one reference. Nothing indexed changes until the source syncs."""

    operation: MappingOperation
    source: SourceRef
    ref_kind: RefKind
    external_ref: str
    account_scoped: bool = Field(
        description="Records of an account-scoped source need a mapping to be indexed."
    )
    now: AccountRef | None
    after: AccountRef | None
    effect: MappingEffect = Field(
        description="At the next sync: indexed (waiting records are indexed under the "
        "account), scoped (records become scoped to the account), moved (records move to "
        "the new account), quarantined (records become unreadable until mapped again), "
        "unscoped (records lose their account: readable under the source's other rules)."
    )
    records_waiting: int = Field(
        description="Records of the latest run waiting for this reference (0 if none)."
    )
    takes_effect: Literal["next_sync"] = "next_sync"


class MappingResult(_Out):
    operation: MappingOperation
    source: SourceRef
    ref_kind: RefKind
    external_ref: str
    account: AccountRef | None
    takes_effect: Literal["next_sync"] = "next_sync"


# --- requests ------------------------------------------------------------------------------


class NewAccountRequest(_In):
    key: str = Field(
        min_length=1,
        max_length=63,
        description="Lowercase letters, digits and hyphens; immutable once created.",
    )
    name: str = Field(min_length=1, max_length=MAX_NAME)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    status: BusinessStatus = "active"
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _normalise(self) -> Self:
        key = self.key.strip()
        if not key or key[0] == "-" or any(c not in _KEY_CHARS for c in key):
            raise ValueError("key must be lowercase letters, digits and hyphens (max 63)")
        _reason(self.reason)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "name", normalise_name(self.name))
        object.__setattr__(self, "aliases", normalise_aliases(self.aliases))
        return self


class AccountChangeRequest(_In):
    """Only what is sent changes. The key, lifecycle and mappings have operations of
    their own."""

    version: datetime
    name: str | None = Field(default=None, max_length=MAX_NAME)
    aliases: list[str] | None = Field(default=None, max_length=50)
    status: BusinessStatus | None = None
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _normalise(self) -> Self:
        _reason(self.reason)
        if self.name is not None:
            object.__setattr__(self, "name", normalise_name(self.name))
        if self.aliases is not None:
            object.__setattr__(self, "aliases", normalise_aliases(self.aliases))
        return self


class AccountLifecycleRequest(_In):
    version: datetime
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _reason_has_text(self) -> Self:
        _reason(self.reason)
        return self


class ReferenceKey(_In):
    source_id: uuid.UUID
    ref_kind: RefKind
    external_ref: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _normalise_ref(self) -> Self:
        object.__setattr__(self, "external_ref", normalise_external_ref(self.external_ref))
        return self


class MappingPreviewRequest(ReferenceKey):
    account_id: uuid.UUID | None = Field(description="The account after; null to unmap.")


class MapRequest(ReferenceKey):
    account_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _reason_has_text(self) -> Self:
        _reason(self.reason)
        return self


class RemapRequest(MapRequest):
    current_account_id: uuid.UUID = Field(description="The account it maps to now (409 if not).")


class UnmapRequest(ReferenceKey):
    current_account_id: uuid.UUID = Field(description="The account it maps to now (409 if not).")
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _reason_has_text(self) -> Self:
        _reason(self.reason)
        return self
