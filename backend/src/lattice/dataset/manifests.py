"""Models for the dataset's Lattice-side configuration and ground truth.

Per organization (``data/synthetic/<org>/``):

* ``org.yaml`` -- the seed manifest (``lattice.seed.Manifest``).
* ``sources.yaml`` -- how Phase 4 will configure the three sources: defaults, ticketing
  organization -> account mapping, per-channel account and sensitivity settings.
* ``admin-grants.yaml`` -- grants made by administrators inside Lattice (not by a
  source), applied after ingestion.
* ``ground-truth.yaml`` -- what the data is designed to contain: storylines, potential
  conflicts, explicit supersession, stale records, source authority, prompt-injection
  fixtures, deliberately unmapped records, quiet periods.

Shared (``data/synthetic/``): ``canaries.yaml``. Golden questions:
``evals/golden/*.yaml`` (``docs/evaluation-plan.md`` §3).
"""

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lattice.admin_grants import AdminGrant, AdminGrantsFile
from lattice.sources.config import ChannelConfig, SourceConfig, SourcesFile

__all__ = [
    "AdminGrant",
    "AdminGrantsFile",
    "ChannelConfig",
    "SourceConfig",
    "SourcesFile",
]

AS_OF = datetime.fromisoformat("2026-09-27T23:59:59+00:00")
"""The dataset's "now". No record may be dated after it."""

KEY_REF = re.compile(
    r"^(?:(?P<org>[a-z0-9-]+)/)?(?P<ref>(?:doc|ticket|chat):[^#\s]+)(?:#(?P<part>[^\s]+))?$"
)
CANARY = re.compile(r"LATTICE-CANARY-[A-Z0-9]+-[0-9a-f]{6}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --- sources.yaml and admin-grants.yaml ----------------------------------------------
# Lattice-side configuration shared with ingestion (Phase 4): the models live with their
# consumers and are re-exported here for the lint.

# --- ground-truth.yaml -------------------------------------------------------------------


class StorylineEvent(_Strict):
    date: date
    record: str
    what: str


class Storyline(_Strict):
    id: str
    title: str
    exercises: list[str] = Field(min_length=1)
    current_state: str
    events: list[StorylineEvent] = Field(min_length=2)


class ConflictCase(_Strict):
    id: str
    kind: Literal["potential_conflict", "superseded"]
    topic: str
    records: list[str] = Field(min_length=2)
    notes: str


class StaleCase(_Strict):
    id: str
    record: str
    newer: list[str] = Field(min_length=1)
    reason: str


class AuthorityCase(_Strict):
    id: str
    newer_lower_authority: str
    older_higher_authority: str
    notes: str


class InjectionFixture(_Strict):
    id: str
    record: str
    marker: str = Field(min_length=8)
    style: Literal["obvious", "subtle"]
    goal: str


class UnmappedRecord(_Strict):
    record: str
    expected_failure: Literal["access_unmappable:account"]


class QuietPeriod(_Strict):
    account: str
    last_evidence_on: date
    notes: str


class Collision(_Strict):
    record: str
    notes: str


class GroundTruth(_Strict):
    organization: str
    storylines: list[Storyline] = Field(default_factory=list)
    conflicts: list[ConflictCase] = Field(default_factory=list)
    stale: list[StaleCase] = Field(default_factory=list)
    authority: list[AuthorityCase] = Field(default_factory=list)
    injections: list[InjectionFixture] = Field(default_factory=list)
    unmapped: list[UnmappedRecord] = Field(default_factory=list)
    quiet_periods: list[QuietPeriod] = Field(default_factory=list)
    # Record keys that deliberately exist in another organization as well.
    cross_org_collisions: list[Collision] = Field(default_factory=list)
    # Words bulk distractor records must never contain (they would change what the
    # storylines' questions retrieve).
    distractor_forbidden_terms: list[str] = Field(default_factory=list)


# --- canaries.yaml -----------------------------------------------------------------------


class Canary(_Strict):
    id: str
    string: str
    organization: str
    record: str  # key, with the part: doc:x#3, ticket:HAL-1#c4, chat:C:ts#ts
    # access: some persona of the canary's own organization cannot read the record.
    # isolation: readable inside its organization; guards against cross-tenant leaks.
    guards: Literal["access", "isolation"] = "access"
    protects: str

    @field_validator("string")
    @classmethod
    def _format(cls, value: str) -> str:
        if not CANARY.fullmatch(value):
            raise ValueError("canary must look like LATTICE-CANARY-<ID>-<6 hex>")
        return value

    @model_validator(mode="after")
    def _id_in_string(self) -> Self:
        if not self.string.startswith(f"LATTICE-CANARY-{self.id}-"):
            raise ValueError(f"canary {self.id}: string must start with LATTICE-CANARY-{self.id}-")
        return self


class CanariesFile(_Strict):
    canaries: list[Canary] = Field(min_length=1)


# --- evals/golden/*.yaml -----------------------------------------------------------------

Category = Literal[
    "lookup",
    "cross_document",
    "cross_source",
    "potential_conflicts",
    "supersession",
    "ai_egress",
    "stale",
    "ambiguous",
    "inaccessible",
    "insufficient_evidence",
    "refusal",
    "prompt_injection",
    "cross_org",
    "account_visibility",
]

# docs/evaluation-plan.md §4 minimums. Potential conflicts and supersession share one row
# of the plan (6 together); each must still be present.
CATEGORY_MINIMUMS: dict[str, int] = {
    "lookup": 8,
    "cross_document": 6,
    "cross_source": 8,
    "ai_egress": 4,
    "stale": 5,
    "ambiguous": 4,
    "inaccessible": 6,
    "insufficient_evidence": 4,
    "refusal": 3,
    "prompt_injection": 4,
    "cross_org": 4,
    "account_visibility": 3,
    "potential_conflicts": 2,
    "supersession": 2,
}
CONFLICT_AND_SUPERSESSION_MINIMUM = 6

Status = Literal[
    "answered",
    "partial",
    "insufficient_evidence",
    "evidence_only",
    "ambiguous",
    "unsupported_request",
]
EgressConfig = Literal["default", "opt_in_confidential"]
SourceType = Literal["doc", "ticket", "chat"]


class Scope(_Strict):
    account: str | None = None
    source_types: list[SourceType] = Field(default_factory=list)
    since: date | None = None
    until: date | None = None


class ConflictExpectation(_Strict):
    kind: Literal["none", "potential_conflict", "superseded"] = "none"
    records: list[str] = Field(default_factory=list)


class FreshnessExpectation(_Strict):
    stale: bool
    newest_evidence_on: date | None = None


class Clarification(_Strict):
    choices: list[str] = Field(min_length=2)


# Label provenance (phase-13-plan EC-8): who authored an item's labels, and whether the
# product owner has reviewed them. Engineering-authored labels are the maintainers'
# expectations, not independently verified ground truth.
LabelSource = Literal["authored_phase3", "authored_phase13", "authored_f2", "owner_reviewed"]
# EC-7: values are chosen on dev only; held_out is reported and never tuned against.
# sealed (phase-13-followups §7): kept out of every default run and evaluated only at a
# checkpoint's final decision (``--sealed --reason``, logged).
Split = Literal["dev", "held_out", "sealed"]
# The follow-up authoring round an item came from; None for Phase 3 and Phase 13 items.
# Unlike label_source it survives owner review, so held-out results can be reported for
# the items seen during Phase 13 and the fresh ones separately.
Cohort = Literal["f2"]
# The evaluation classes of phase-13-plan §10 an item exercises (an item may exercise
# several); the lint requires a minimum number of items per class.
EvalClass = Literal[
    "paraphrase",
    "keyword_vs_natural",
    "identifier_variant",
    "ambiguous_account",
    "hidden_account_collision",
    "multi_document",
    "multi_account_comparison",
    "contradictory_evidence",
    "temporal_supersession",
    "insufficient_evidence",
    "egress_withheld",
    "inaccessible_strong_match",
    "scoped_retrieval",
    "injection_misleading",
    "veridian_cross_tenant",
]
CLASS_MINIMUM = 4


class OwnerReview(_Strict):
    """D-41: the product owner reviews held-out items in batches. ``approved`` exists only
    once the owner has actually approved the item's labels."""

    batch: str = Field(pattern=r"^[HS][0-9]+$")  # H: held-out, S: sealed
    status: Literal["pending", "approved", "corrections_requested"] = "pending"
    reviewed_on: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _dated(self) -> Self:
        if (self.status == "pending") != (self.reviewed_on is None):
            raise ValueError("reviewed_on is set exactly when the review is not pending")
        return self


Candidate = Literal["relevant", "acceptable", "unrelated"]


class ExhaustiveReview(_Strict):
    """The documented review that makes an item's labels exhaustive (EC-3): every part the
    persona can read inside the item's scope that contains any of ``terms`` (or, with
    ``scope_all``, every such part at all) is a candidate and is classified; ``extra``
    lists candidates the reviewer found without a term. The lint recomputes the
    candidates from the corpus, so a corpus change makes a stale review fail."""

    reviewed_on: date
    reviewer: Literal["engineering", "product_owner"]
    terms: list[str] = Field(default_factory=list)
    scope_all: bool = False
    candidates: dict[str, Candidate] = Field(min_length=1)
    extra: list[str] = Field(default_factory=list)
    notes: str = Field(min_length=10)

    @model_validator(mode="after")
    def _how(self) -> Self:
        if bool(self.terms) == self.scope_all:
            raise ValueError("an exhaustive review uses terms or scope_all (exactly one)")
        return self


class Expected(_Strict):
    status: Status
    status_reason: (
        Literal["no_relevant_evidence", "claims_unverified", "egress_policy", "action_request"]
        | None
    ) = None
    # Evidence that should be retrieved and used (recall is measured over it).
    relevant: list[str] = Field(default_factory=list)
    # Readable evidence that may appear without penalty.
    acceptable: list[str] = Field(default_factory=list)
    # Must never appear in candidates, context, payload, response, trace or logs.
    must_not_retrieve: list[str] = Field(default_factory=list)
    # Readable and relevant, but kept out of the provider payload by the AI egress
    # policy: returned as "Not used to draft this answer".
    not_used_for_answer: list[str] = Field(default_factory=list)
    required_facts: list[str] = Field(default_factory=list)
    # Claims the answer must not make (judge-checked).
    forbidden_claims: list[str] = Field(default_factory=list)
    # Literal strings that must appear nowhere in the output.
    must_not_contain: list[str] = Field(default_factory=list)
    conflict: ConflictExpectation = Field(default_factory=ConflictExpectation)
    presented_as_resolved: Literal[False] = False
    freshness: FreshnessExpectation | None = None
    clarification: Clarification | None = None
    # Accounts that must not be revealed as structured metadata: no interpretation,
    # boost, ambiguity choice, filter value or evidence-card account field.
    hidden_accounts: list[str] = Field(default_factory=list)
    # Readable evidence whose account field must be omitted (D-8).
    account_metadata_hidden: list[str] = Field(default_factory=list)


class GoldenItem(_Strict):
    id: str
    category: Category
    persona: str
    organization: str
    question: str = Field(min_length=3)
    scope: Scope = Field(default_factory=Scope)
    egress_config: EgressConfig | None = None
    expected: Expected | None = None
    expected_by_egress: dict[EgressConfig, Expected] | None = None
    tags: list[str] = Field(default_factory=list)
    same_question_group: str | None = None
    # Phrases from the question that appear verbatim in unauthorized evidence (lint
    # checks both), so the easiest match is something the persona may not read.
    trap_phrases: list[str] = Field(default_factory=list)
    notes: str = Field(min_length=10)
    # Provenance and splits (P13.2; phase-13-plan EC-7, EC-8, D-41). ``split`` may be
    # missing while an item is being authored (``lattice dataset splits --propose``); the
    # lint requires it.
    label_source: LabelSource
    split: Split | None = None
    # Items asking for the same information need (the same decisive answer facts),
    # whatever the wording or persona. With ``same_question_group`` it decides the split
    # unit: a group never straddles dev and held_out.
    paraphrase_group: str | None = None
    classes: list[EvalClass] = Field(default_factory=list)
    # Precision@k is computed only for exhaustive items (EC-3), which need a review.
    exhaustive: bool = False
    exhaustive_review: ExhaustiveReview | None = None
    owner_review: OwnerReview | None = None
    cohort: Cohort | None = None

    @model_validator(mode="after")
    def _provenance(self) -> Self:
        if self.exhaustive != (self.exhaustive_review is not None):
            raise ValueError("exhaustive items (and only they) carry an exhaustive_review")
        approved = self.owner_review is not None and self.owner_review.status == "approved"
        if (self.label_source == "owner_reviewed") != approved:
            raise ValueError("label_source owner_reviewed needs an approved owner_review")
        if self.label_source == "authored_f2" and self.cohort != "f2":
            raise ValueError("label_source authored_f2 needs cohort f2")
        if self.split == "sealed" and self.cohort is None:
            raise ValueError("only follow-up items (cohort f2 onwards) are sealed")
        if self.owner_review is not None and self.split in ("held_out", "sealed"):
            prefix = "S" if self.split == "sealed" else "H"
            if not self.owner_review.batch.startswith(prefix):
                raise ValueError(f"{self.split} review batches are named {prefix}<n>")
        return self

    @model_validator(mode="after")
    def _one_expectation(self) -> Self:
        if (self.expected is None) == (self.expected_by_egress is None):
            raise ValueError("exactly one of expected / expected_by_egress is required")
        if self.expected_by_egress is not None:
            if self.egress_config is not None:
                raise ValueError("egress_config is implied by expected_by_egress")
            if set(self.expected_by_egress) != {"default", "opt_in_confidential"}:
                raise ValueError("expected_by_egress needs default and opt_in_confidential")
        return self

    def expectations(self) -> list[tuple[EgressConfig, Expected]]:
        if self.expected_by_egress is not None:
            return sorted(self.expected_by_egress.items())
        assert self.expected is not None  # noqa: S101 - enforced by the validator
        return [(self.egress_config or "default", self.expected)]


# Fields that describe an item's place in the evaluation, not its labels: changing them
# never changes what the item expects (label digests exclude them).
METADATA_FIELDS = frozenset(
    {
        "label_source",
        "split",
        "paraphrase_group",
        "classes",
        "exhaustive",
        "exhaustive_review",
        "owner_review",
        "cohort",
    }
)
