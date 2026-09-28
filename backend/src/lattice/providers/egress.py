"""AI data egress policy (SEC-10, docs/security-model.md §6).

Egress answers "may this content be sent to this model provider?" -- a data policy,
separate from authorization ("may this person read this?"). A member can be fully
authorized to read a confidential document that is never sent to an external model.
Authorization always comes first: egress only ever narrows what an authorized request
may send, never widens what anyone may read.

A provider may receive content only when all of these hold (ADR-011):

1. it is **recorded** for the organization (``model_providers``) -- an unrecorded
   provider was never approved for anything;
2. its recorded **deployment** is the deployment it actually runs as (a provider cannot
   claim to be local to escape the external level);
3. its **data use** is acceptable for the organization's data: a provider that may use
   submitted content (``training_opt_out = false``, e.g. an unpaid Gemini API key,
   ADR-010) is limited to ``synthetic`` organizations;
4. the content's classification is at or below the **effective level**:

   * ``external_api`` / ``private``: ``min(org_settings.external_egress_level,
     model_providers.approved_for_level)``;
   * ``local`` (inference inside the Lattice deployment, ADR-009): the provider's own
     ``approved_for_level`` -- the organization's *external* egress setting does not
     govern a model that sends nothing out. Its approval still defaults to ``internal``.

An API key is none of these inputs: a valid credential never authorizes egress.
Defaults (an organization without settings, a provider without a record) are the most
restrictive available; anything unrecognised is never sent.

**Question sensitivity (D-13).** The question is content too. Its effective
sensitivity is the more restrictive of the organization's trusted default
(``org_settings.default_query_sensitivity``) and the caller's declaration: a
declaration, a provider choice or a credential can raise it, never lower it.
"""

from dataclasses import dataclass
from typing import Final, Literal

Level = Literal["internal", "confidential", "restricted"]
Deployment = Literal["external_api", "private", "local"]
DataOrigin = Literal["customer", "synthetic"]

DEFAULT_LEVEL: Final[Level] = "internal"
DEFAULT_DATA_ORIGIN: Final[DataOrigin] = "customer"
_ORDER: Final[tuple[Level, ...]] = ("internal", "confidential", "restricted")
_RANK: Final = {level: rank for rank, level in enumerate(_ORDER)}
_DEPLOYMENTS: Final = frozenset({"external_api", "private", "local"})


def effective_level(
    org_level: str, provider_approved_level: str, deployment: str = "external_api"
) -> str | None:
    """The most sensitive classification that may be sent, or None if any input is
    unrecognised (then nothing is sent)."""
    if (
        org_level not in _RANK
        or provider_approved_level not in _RANK
        or deployment not in _DEPLOYMENTS
    ):
        return None
    if deployment == "local":
        return provider_approved_level
    return min(org_level, provider_approved_level, key=_RANK.__getitem__)


def may_send(classification: str, level: str | None) -> bool:
    """May content classified ``classification`` go to a provider at ``level``?"""
    if level is None or level not in _RANK or classification not in _RANK:
        return False
    return _RANK[classification] <= _RANK[level]


def more_restrictive(*levels: str) -> Level:
    """The most sensitive of ``levels``; anything unrecognised counts as
    ``restricted`` (fail closed), and so does an empty list."""
    ranks = [_ORDER.index(lvl) if lvl in _ORDER else len(_ORDER) - 1 for lvl in levels]
    return _ORDER[max(ranks, default=len(_ORDER) - 1)]


def question_sensitivity(org_default: str, declared: str | None) -> Level:
    """D-13: the effective sensitivity of a question. ``declared`` (the asker's or the
    client's declaration) can only make it more restrictive than the organization's
    trusted default."""
    return more_restrictive(org_default, declared or org_default)


def data_use_acceptable(*, training_opt_out: bool, data_origin: str) -> bool:
    """May a provider with these data terms receive this organization's content? A
    provider that may use submitted content receives synthetic data only."""
    return training_opt_out or data_origin == "synthetic"


@dataclass(frozen=True, slots=True)
class OrgEgress:
    """``org_settings`` (or its defaults)."""

    external_egress_level: str = DEFAULT_LEVEL
    default_query_sensitivity: str = DEFAULT_LEVEL
    data_origin: str = DEFAULT_DATA_ORIGIN


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    """A ``model_providers`` row: what the organization approved."""

    provider: str
    model: str
    deployment: str
    approved_for_level: str
    training_opt_out: bool


@dataclass(frozen=True, slots=True)
class ProviderDecision:
    """The most sensitive classification this provider may receive for this
    organization -- or None, with a fixed ``reason`` code."""

    level: str | None
    reason: str | None = None


def provider_decision(
    org: OrgEgress,
    record: ProviderRecord | None,
    *,
    deployment: str,
    training_opt_out: bool,
) -> ProviderDecision:
    """``deployment`` and ``training_opt_out`` are what the provider implementation
    declares about itself; ``record`` is what the organization approved. Where they
    differ, the more restrictive reading wins."""
    if record is None:
        return ProviderDecision(None, "provider_unapproved")
    if record.deployment != deployment:
        return ProviderDecision(None, "provider_deployment_mismatch")
    if not data_use_acceptable(
        training_opt_out=record.training_opt_out and training_opt_out,
        data_origin=org.data_origin,
    ):
        return ProviderDecision(None, "provider_data_use")
    level = effective_level(org.external_egress_level, record.approved_for_level, deployment)
    if level is None:
        return ProviderDecision(None, "policy_unrecognised")
    return ProviderDecision(level)
