"""Typed contracts of answering (retrieval-design §8-11, data-model §9).

Two families:

* the **draft** the model returns (``AnswerDraft``) -- untrusted input. The top level is
  parsed strictly; each claim, citation and conflict is then validated one by one, so a
  malformed claim invalidates that claim only (claim-level fail-closed, SEC-13);
* the **answer** Lattice returns (``Answer``) -- only validated claims, citations bound
  to server-side chunk IDs, evidence the asker may read, deterministic freshness,
  supersession and potential-conflict assembly, notices, and a status computed by code.

``ANSWER_SCHEMA`` is the JSON schema sent to providers. It is written by hand in the
subset of JSON Schema that every supported structured-output mode accepts (type,
properties, required, enum, items, additionalProperties); length limits are enforced by
validation, not by the schema.
"""

import uuid
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from lattice.retrieval import RetrievalScope

DraftStatus = Literal[
    "answered", "partial", "insufficient_evidence", "ambiguous", "unsupported_request"
]
ClaimRole = Literal["direct_answer", "supporting"]
AnswerStatus = Literal[
    "answered",
    "partial",
    "insufficient_evidence",
    "evidence_only",
    "ambiguous",
    "unsupported_request",
    "failed",
]
Level = Literal["internal", "confidential", "restricted"]

_CITATION: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"handle": {"type": "string"}, "quote": {"type": "string"}},
    "required": ["handle", "quote"],
}
_CLAIM: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
        "role": {"type": "string", "enum": ["direct_answer", "supporting"]},
        "citations": {"type": "array", "items": _CITATION},
    },
    "required": ["text", "role", "citations"],
}
ANSWER_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {"type": "array", "items": _CLAIM},
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "topic": {"type": "string"},
                    "positions": {"type": "array", "items": _CLAIM},
                },
                "required": ["topic", "positions"],
            },
        },
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "clarifying_question": {"type": "string"},
        "status": {
            "type": "string",
            "enum": [
                "answered",
                "partial",
                "insufficient_evidence",
                "ambiguous",
                "unsupported_request",
            ],
        },
    },
    "required": ["claims", "conflicts", "unknowns", "clarifying_question", "status"],
}


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- the model's draft (untrusted) ------------------------------------------------------


class AnswerDraft(_Strict):
    """Top level only. Claims and conflicts stay raw here and are validated one by one."""

    claims: list[Any] = Field(max_length=40)
    conflicts: list[Any] = Field(default_factory=list, max_length=10)
    unknowns: list[Any] = Field(default_factory=list, max_length=20)
    clarifying_question: str | None = None
    status: DraftStatus


class DraftCitation(_Strict):
    handle: str = Field(max_length=16)
    quote: str = Field(max_length=2000)


class DraftClaim(_Strict):
    text: str = Field(max_length=2000)
    role: ClaimRole
    citations: list[DraftCitation] = Field(max_length=20)


class DraftConflict(_Strict):
    topic: str = Field(max_length=500)
    positions: list[Any] = Field(max_length=10)


# --- the request ------------------------------------------------------------------------


class AskRequest(_Strict):
    """What a client may send. Nothing here is authority: the organization and member come
    from the session, every access fact from the database; ``classification`` can only
    raise the question's sensitivity (D-13)."""

    question: str = Field(min_length=1, max_length=2000)
    scope: RetrievalScope = Field(default_factory=RetrievalScope)
    classification: Level | None = None


# --- the answer (validated) --------------------------------------------------------------


class Citation(_Strict):
    evidence_id: uuid.UUID = Field(description="The chunk ID of an item in `evidence`.")
    document_id: uuid.UUID
    quote: str = Field(description="Verbatim from the evidence text.")
    quote_start: int = Field(description="Offset of the quote in the evidence text.")
    quote_end: int


class Claim(_Strict):
    text: str
    role: ClaimRole
    citations: list[Citation]


class ConflictPosition(_Strict):
    claim: Claim
    document_id: uuid.UUID
    title: str
    source_type: str
    authority: str
    occurred_at: datetime | None
    version: int


class Supersession(_Strict):
    superseded_document_id: uuid.UUID
    superseding_document_id: uuid.UUID
    since: datetime | None


class Conflict(_Strict):
    kind: Literal["superseded", "potential_conflict"]
    topic: str | None
    summary: str = Field(description="Deterministic; never declares a winner.")
    positions: list[ConflictPosition]
    supersession: Supersession | None = None


class EvidenceAccount(_Strict):
    key: str
    name: str


class EvidenceItem(_Strict):
    """A permitted chunk the asker may read (D-8: the account only when visible)."""

    evidence_id: uuid.UUID
    document_id: uuid.UUID
    rank: int
    source_type: str
    document_kind: str
    source_name: str
    title: str
    display_ref: str | None
    uri: str | None
    heading_path: list[str]
    author: str | None
    occurred_at: datetime | None
    document_updated_at: datetime
    version: int
    authority: str
    classification: Level
    account: EvidenceAccount | None
    text: str
    supersedes_document_id: uuid.UUID | None
    superseded_by_document_ids: list[uuid.UUID]
    used_for_answer: bool
    # Why an item was not sent to the model: ``egress_policy`` (above the level the
    # organization allows for this provider), ``relevance_floor`` (matched only as a
    # semantic neighbour) or ``prompt_budget``. None when used, or when no model was
    # called for another reason (the status says why).
    not_used_reason: Literal["egress_policy", "relevance_floor", "prompt_budget"] | None = None


class Freshness(_Strict):
    newest_cited_at: datetime | None
    newest_evidence_at: datetime | None
    is_stale: bool
    threshold_days: int | None
    note: str | None


class Notice(_Strict):
    code: Literal[
        "egress_excluded",
        "claims_removed",
        "stale_evidence",
        "cites_superseded",
        "semantic_degraded",
    ]
    message: str
    count: int | None = None


class ClarificationChoice(_Strict):
    key: str
    name: str


class Clarification(_Strict):
    question: str
    choices: list[ClarificationChoice]


class Answer(_Strict):
    trace_id: uuid.UUID
    status: AnswerStatus
    status_reason: str | None
    message: str | None = Field(description="A fixed sentence for the status, or null.")
    claims: list[Claim]
    conflicts: list[Conflict]
    unknowns: list[str]
    clarification: Clarification | None
    evidence: list[EvidenceItem]
    freshness: Freshness
    notices: list[Notice]
    question_sensitivity: Level


class EvidenceDetail(_Strict):
    """``GET /v1/evidence/{id}``: one readable chunk, for the inspector."""

    evidence_id: uuid.UUID
    document_id: uuid.UUID
    source_type: str
    document_kind: str
    source_name: str
    title: str
    display_ref: str | None
    uri: str | None
    heading_path: list[str]
    author: str | None
    occurred_at: datetime | None
    document_updated_at: datetime
    version: int
    authority: str
    classification: Level
    account: EvidenceAccount | None
    text: str
    char_start: int = Field(description="Offset of this chunk in its document body.")
    char_end: int
    supersedes_document_id: uuid.UUID | None
    superseded_by_document_ids: list[uuid.UUID]
