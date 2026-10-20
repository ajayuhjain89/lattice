"""AI data egress policy (SEC-10, docs/security-model.md §6).

Egress answers "may this content be sent to this model provider?" -- a data policy,
separate from authorization ("may this person read this?"). A member can be fully
authorized to read a confidential document that is never sent to an external model.
Authorization always comes first: egress only ever narrows what an authorized request
may send, never widens what anyone may read.

**Deployment trust classes (D-15).** Where inference runs, as recorded for the
organization -- never inferred from a provider's name:

* ``local`` -- inside the Lattice deployment boundary; no inference content leaves it;
* ``organization_controlled`` -- outside the Lattice deployment, on infrastructure or an
  endpoint the organization controls or approved (its own model server, a private
  cloud endpoint). It may involve network transmission and is not trusted by default;
* ``external_api`` -- a third-party provider (Gemini, OpenAI, Anthropic, Voyage);
* ``unknown`` -- anything else, including a missing value or the pre-D-15 label
  ``private``: **fail closed**, nothing is sent.

A provider may receive content only when all of these hold (ADR-011):

1. it is **recorded** for the organization (``model_providers``) -- an unrecorded
   provider, or another organization's record, was never approved for anything;
2. its recorded **trust class** is known and is the class it actually runs as (a
   provider cannot claim ``local`` or ``organization_controlled`` to escape a stricter
   rule);
3. the organization **allows the class**, with a ceiling (``class_ceiling``):
   ``external_api`` up to ``org_settings.external_egress_level`` (NULL since P12.5: the
   organization does not allow external APIs at all);
   ``organization_controlled`` only up to ``org_settings.organization_controlled_level``,
   which is unset (class not allowed) until an administrator sets it; ``local`` is not
   capped by an organization setting -- nothing leaves the deployment -- only by the
   provider's own approval;
4. the provider is **approved**: ``model_providers.approved_for_level`` (default
   ``internal``). An ``organization_controlled`` record has no default approval: it
   needs an explicit administrator approval (``approved_at``) before anything is sent.
   Neither has a generation record (P12.5, ``answering.policy``): every class needs its
   explicit approval for generation; embedding records keep the default;
5. its **data use** is acceptable for the organization's data: a provider that may use
   submitted content (``training_opt_out = false``, e.g. an unpaid Gemini API key,
   ADR-010) is limited to ``synthetic`` organizations;
6. the content's classification is at or below the **effective level**
   ``min(class ceiling, approved_for_level)``.

An API key is none of these inputs: a valid credential never authorizes egress, raises
a ceiling or changes a trust class. Defaults (an organization without settings, a
provider without a record) are the most restrictive available; anything unrecognised
is never sent.

**Question sensitivity (D-13).** The question is content too. Its effective
sensitivity is the more restrictive of the organization's trusted default
(``org_settings.default_query_sensitivity``) and the caller's declaration: a
declaration, a provider choice or a credential can raise it, never lower it.
"""

from dataclasses import dataclass
from typing import Final, Literal

Level = Literal["internal", "confidential", "restricted"]
# D-15 trust classes that may be recorded; anything else is ``unknown`` (fail closed).
Deployment = Literal["local", "organization_controlled", "external_api"]
TrustClass = Literal["local", "organization_controlled", "external_api", "unknown"]
DataOrigin = Literal["customer", "synthetic"]

DEFAULT_LEVEL: Final[Level] = "internal"
DEFAULT_DATA_ORIGIN: Final[DataOrigin] = "customer"
_ORDER: Final[tuple[Level, ...]] = ("internal", "confidential", "restricted")
_RANK: Final = {level: rank for rank, level in enumerate(_ORDER)}
LEVELS_ORDER: Final = _ORDER  # least to most sensitive
DEPLOYMENTS: Final[tuple[Deployment, ...]] = ("local", "organization_controlled", "external_api")


def trust_class(value: object) -> TrustClass:
    """The D-15 trust class of a recorded or declared deployment; ``unknown`` for
    anything unrecognised (a missing value, a typo, the pre-D-15 label ``private``)."""
    for known in DEPLOYMENTS:
        if value == known:
            return known
    return "unknown"


def effective_level(ceiling: str | None, provider_approved_level: str) -> str | None:
    """The most sensitive classification that may be sent, or None if either input is
    missing or unrecognised (then nothing is sent)."""
    if ceiling not in _RANK or provider_approved_level not in _RANK:
        return None
    return min(ceiling, provider_approved_level, key=_RANK.__getitem__)


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

    # None (P12.5): the organization does not allow external APIs.
    external_egress_level: str | None = DEFAULT_LEVEL
    default_query_sensitivity: str = DEFAULT_LEVEL
    data_origin: str = DEFAULT_DATA_ORIGIN
    # D-15: None = the organization has not allowed organization_controlled inference.
    organization_controlled_level: str | None = None


def class_ceiling(org: OrgEgress, deployment: TrustClass) -> str | None:
    """The organization's ceiling for a trust class, or None if the class is not
    allowed (``organization_controlled`` until configured; ``unknown`` always)."""
    if deployment == "external_api":
        return org.external_egress_level
    if deployment == "organization_controlled":
        return org.organization_controlled_level
    if deployment == "local":
        # Nothing leaves the deployment: only the provider's own approval caps it.
        return _ORDER[-1]
    return None


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    """A ``model_providers`` row: what the organization approved."""

    provider: str
    model: str
    deployment: str
    approved_for_level: str
    training_opt_out: bool
    # An administrator approved it explicitly (``approved_at`` is set), rather than it
    # carrying the default approval of its first use.
    explicitly_approved: bool = False


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
    declared = trust_class(deployment)
    if declared == "unknown":
        return ProviderDecision(None, "deployment_unknown")
    if record is None:
        return ProviderDecision(None, "provider_unapproved")
    recorded = trust_class(record.deployment)
    if recorded == "unknown":
        return ProviderDecision(None, "deployment_unknown")
    if recorded != declared:
        return ProviderDecision(None, "provider_deployment_mismatch")
    if recorded == "organization_controlled" and not record.explicitly_approved:
        return ProviderDecision(None, "provider_unapproved")
    if not data_use_acceptable(
        training_opt_out=record.training_opt_out and training_opt_out,
        data_origin=org.data_origin,
    ):
        return ProviderDecision(None, "provider_data_use")
    ceiling = class_ceiling(org, recorded)
    if ceiling is None:
        return ProviderDecision(None, "deployment_not_allowed")
    level = effective_level(ceiling, record.approved_for_level)
    if level is None:
        return ProviderDecision(None, "policy_unrecognised")
    return ProviderDecision(level)
