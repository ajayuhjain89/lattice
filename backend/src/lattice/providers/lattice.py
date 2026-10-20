"""The egress lattice: which data level may reach which recorded provider, and why
(phase-12-plan C6.1, C6.2; D-15; P12.5).

One cell per (provider record, level). A cell is **permitted** when the recorded
configuration would let content of that level reach that provider: the invocation rule
(``providers.egress.provider_decision``) over the record as recorded, plus the generation
rules (enabled, explicitly approved). The credential is not an input: a key, a passing
test or a declared paid tier never makes a cell permitted (C6.1) -- they decide only
whether a permitted call can be made.

The database computes the same permitted cells (``lattice_egress_cells``, migration 0021)
and decides from them whether a change is an **escalation** -- a cell newly permitted, or
the default question sensitivity lowered (C6.2) -- which only a fresh step-up may commit
(D-34). This module adds the reason for every cell, for the page; the two are proven to
agree (``tests/providers/test_egress_lattice.py``). The page never computes a cell.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from lattice.providers.egress import (
    LEVELS_ORDER,
    OrgEgress,
    ProviderRecord,
    class_ceiling,
    provider_decision,
    trust_class,
)

# Why a cell is (not) permitted. ``permitted`` is the only positive one.
CELL_REASONS: Final = (
    "permitted",
    "provider_disabled",
    "provider_unapproved",
    "deployment_unknown",
    "provider_data_use",
    "deployment_not_allowed",
    "policy_unrecognised",
    "above_approval",
    "above_ceiling",
)
_RANK: Final[dict[str, int]] = {level: rank for rank, level in enumerate(LEVELS_ORDER)}


@dataclass(frozen=True, slots=True)
class PolicyRecord:
    """A ``model_providers`` row as the lattice reads it."""

    id: uuid.UUID
    purpose: str
    provider: str
    model: str
    deployment: str
    endpoint: str | None
    approved_for_level: str
    explicitly_approved: bool
    enabled: bool
    training_opt_out: bool

    def as_provider_record(self) -> ProviderRecord:
        return ProviderRecord(
            self.provider,
            self.model,
            self.deployment,
            self.approved_for_level,
            self.training_opt_out,
            self.explicitly_approved,
        )


@dataclass(frozen=True, slots=True)
class Cell:
    provider_id: uuid.UUID
    endpoint: str
    level: str
    reason: str

    @property
    def permitted(self) -> bool:
        return self.reason == "permitted"

    @property
    def key(self) -> tuple[str, str, str]:
        """The identity the database compares cells by: record, destination, level."""
        return (str(self.provider_id), self.endpoint, self.level)


def allowed_level(org: OrgEgress, record: PolicyRecord) -> tuple[str | None, str | None]:
    """The most sensitive level ``record`` may receive, or None with the reason."""
    if record.purpose == "generation" and not record.enabled:
        return None, "provider_disabled"
    if record.purpose == "generation" and not record.explicitly_approved:
        return None, "provider_unapproved"
    decision = provider_decision(
        org,
        record.as_provider_record(),
        deployment=record.deployment,
        training_opt_out=record.training_opt_out,
    )
    return decision.level, decision.reason


def cells_for(org: OrgEgress, record: PolicyRecord) -> list[Cell]:
    level, reason = allowed_level(org, record)
    endpoint = record.endpoint or ""
    found: list[Cell] = []
    for candidate in LEVELS_ORDER:
        if level is not None and _RANK[candidate] <= _RANK[level]:
            found.append(Cell(record.id, endpoint, candidate, "permitted"))
            continue
        if level is None:
            found.append(Cell(record.id, endpoint, candidate, reason or "policy_unrecognised"))
            continue
        # Permitted up to ``level`` only: which of the two limits stops this level.
        ceiling = class_ceiling(org, trust_class(record.deployment)) or level
        approval = _RANK[record.approved_for_level]
        limit = (
            "above_approval"
            if approval < _RANK[candidate] and approval <= _RANK[ceiling]
            else "above_ceiling"
        )
        found.append(Cell(record.id, endpoint, candidate, limit))
    return found


def lattice(org: OrgEgress, records: Iterable[PolicyRecord]) -> list[Cell]:
    return [cell for record in records for cell in cells_for(org, record)]


def permitted_keys(cells: Sequence[Cell]) -> set[tuple[str, str, str]]:
    return {c.key for c in cells if c.permitted}
