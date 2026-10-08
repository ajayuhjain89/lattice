"""The organization's setup record, derived from facts (phase-12-plan C12).

``derive`` is a pure function of ``SetupFacts``: every category's state and reason
comes from one rule here, never from the frontend and never from stored progress. No
percentage, no score. Each category carries a fixed ``message`` keyed by its reason,
safe facts (counts, times, provider names an administrator already sees -- never names
of hidden resources, documents, people or channels) and an action: a link to the page
that owns the item, or None when only an operator can act today.

States: ``complete``, ``required``, ``optional``, ``in_progress``, ``attention``,
``blocked``. The reasons are C12's, plus three that P12.1's data needs and C12 did not
name (documented in the plan's P12.1 notes): ``sources·required·not_synced`` (a source
exists without any run yet), ``accounts·blocked·sources_required`` and
``ai_policy·attention·provider_unavailable`` (the enabled provider cannot be used as
configured). ``sources·awaiting_import`` / ``import_rejected`` arrive with imports
(P12.6).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

State = Literal["complete", "required", "optional", "in_progress", "attention", "blocked"]
CategoryKey = Literal[
    "organization",
    "identity",
    "members",
    "provisioning",
    "ai_policy",
    "sources",
    "accounts",
    "first_answer",
]
FactValue = int | bool | str | None

CATEGORIES: Final[tuple[CategoryKey, ...]] = (
    "organization",
    "identity",
    "members",
    "provisioning",
    "ai_policy",
    "sources",
    "accounts",
    "first_answer",
)


@dataclass(frozen=True)
class Action:
    href: str
    label: str
    kind: Literal["link"] = "link"


@dataclass(frozen=True)
class Category:
    key: CategoryKey
    state: State
    reason: str
    message: str
    facts: dict[str, FactValue] = field(default_factory=dict)
    action: Action | None = None


@dataclass(frozen=True)
class AiFacts:
    """The organization's generation configuration as the invocation rule sees it."""

    configured: bool = False
    enabled: bool = False
    provider: str | None = None
    model: str | None = None
    # The enabled provider cannot be built as configured (unsupported, bad endpoint).
    unusable: bool = False
    credential_required: bool = False
    credential_available: bool = False
    # Whether the policy lets the default question sensitivity reach the provider.
    allows_default: bool = False


@dataclass(frozen=True)
class SetupFacts:
    organization_status: str = "active"
    active_administrators: int = 0
    active_members: int = 0
    connection: Literal["active", "disabled", "none"] = "none"
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    live_tokens: int = 0
    tokens_ever: int = 0
    token_used: bool = False
    provisioned_members: int = 0
    default_question_sensitivity: str = "internal"
    ai: AiFacts = field(default_factory=AiFacts)
    connected_sources: int = 0
    synced_sources: int = 0
    failed_sources: int = 0
    syncing_sources: int = 0
    account_sources: int = 0
    unmapped_references: int = 0
    answered: bool = False


MESSAGES: Final[dict[tuple[str, str], str]] = {
    ("organization", "active"): "The organization is active and has more than one administrator.",
    ("organization", "single_administrator"): (
        "One active administrator. If they leave, only your Lattice operator can appoint "
        "another; a second administrator avoids that."
    ),
    ("organization", "suspended"): (
        "The organization is suspended: sign-in, provisioning and syncs are stopped. Only "
        "your Lattice operator can reactivate it."
    ),
    ("organization", "no_administrator"): (
        "No active administrator. Only your Lattice operator can appoint one."
    ),
    ("identity", "no_connection"): (
        "No identity provider is connected, so nobody can sign in. Your Lattice operator "
        "connects it."
    ),
    ("identity", "not_verified"): (
        "The identity provider is connected but nobody has signed in through it yet. The "
        "first successful sign-in verifies it."
    ),
    ("identity", "verified"): "Sign-in through the identity provider works.",
    ("identity", "connection_disabled"): (
        "The identity provider connection is disabled: nobody can sign in through it. Your "
        "Lattice operator can re-enable it."
    ),
    ("identity", "provider_unavailable"): (
        "The last sign-in failed because the identity provider could not be reached or "
        "refused Lattice's client credentials."
    ),
    (
        "members",
        "identity_required",
    ): "Members can be added once an identity provider is connected.",
    ("members", "only_administrators"): (
        "Only administrators are members so far. People join when your identity provider "
        "provisions them (SCIM) or when your Lattice operator adds them."
    ),
    ("members", "members_active"): "Members other than administrators are active.",
    ("provisioning", "not_configured"): (
        "Automatic provisioning (SCIM) is not set up. It is optional; your Lattice "
        "operator issues the provisioning token."
    ),
    ("provisioning", "token_not_used_yet"): (
        "A provisioning token exists but the identity provider has not provisioned anyone "
        "with it yet."
    ),
    ("provisioning", "in_use"): "The identity provider provisions members.",
    ("provisioning", "no_valid_token"): (
        "Every provisioning token is revoked while provisioned members remain: changes in "
        "the identity provider no longer reach Lattice."
    ),
    ("ai_policy", "evidence_only"): (
        "No generation provider is enabled: answers show their evidence without a written answer."
    ),
    ("ai_policy", "generation_ready"): (
        "A generation provider is enabled and allowed for the level the organization's "
        "questions default to."
    ),
    ("ai_policy", "credential_failed"): (
        "The generation provider's credential is missing or cannot be used: answers fall "
        "back to evidence only."
    ),
    ("ai_policy", "policy_excludes_default"): (
        "The enabled generation provider is not allowed for the level the organization's "
        "questions default to: such questions get evidence only."
    ),
    ("ai_policy", "provider_unavailable"): (
        "The enabled generation provider cannot be used as configured: answers fall back "
        "to evidence only."
    ),
    ("sources", "no_sources"): "No source is connected yet.",
    ("sources", "not_synced"): "A source is connected but has not synced yet.",
    ("sources", "syncing"): "A sync is queued or running.",
    ("sources", "sync_failed"): "The latest sync of a source failed.",
    ("sources", "synced"): "At least one source has synced.",
    ("accounts", "not_needed"): (
        "No source maps records to accounts, so no account mapping is needed."
    ),
    ("accounts", "sources_required"): (
        "Account mappings are checked once an account-scoped source has synced."
    ),
    ("accounts", "unmapped_references"): (
        "Records reference accounts that are not mapped, and are not indexed until they are."
    ),
    ("accounts", "mapped"): "Every account reference in the latest syncs is mapped.",
    (
        "first_answer",
        "sources_required",
    ): "The first question can be asked once a source has synced.",
    ("first_answer", "no_answer_yet"): "Nobody has received an answer yet.",
    ("first_answer", "answered"): "Members have received answers.",
}


def _category(
    key: CategoryKey,
    state: State,
    reason: str,
    facts: dict[str, FactValue],
    action: Action | None = None,
) -> Category:
    return Category(key, state, reason, MESSAGES[(key, reason)], facts, action)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _organization(f: SetupFacts) -> Category:
    facts: dict[str, FactValue] = {"active_administrators": f.active_administrators}
    if f.organization_status != "active":
        return _category("organization", "blocked", "suspended", facts)
    if f.active_administrators == 0:
        return _category("organization", "blocked", "no_administrator", facts)
    if f.active_administrators == 1:
        return _category("organization", "attention", "single_administrator", facts)
    return _category("organization", "complete", "active", facts)


def _identity(f: SetupFacts) -> Category:
    facts: dict[str, FactValue] = {
        "connection": f.connection,
        "last_success_at": _iso(f.last_success_at),
        "last_failure_at": _iso(f.last_failure_at),
    }
    if f.connection == "none":
        return _category("identity", "required", "no_connection", facts)
    if f.connection == "disabled":
        return _category("identity", "attention", "connection_disabled", facts)
    if f.last_success_at is None:
        return _category("identity", "required", "not_verified", facts)
    if f.last_failure_at is not None and f.last_failure_at > f.last_success_at:
        return _category("identity", "attention", "provider_unavailable", facts)
    return _category("identity", "complete", "verified", facts)


def _members(f: SetupFacts) -> Category:
    facts: dict[str, FactValue] = {
        "active_members": f.active_members,
        "active_administrators": f.active_administrators,
    }
    if f.connection != "active":
        return _category("members", "blocked", "identity_required", facts)
    if f.active_members - f.active_administrators <= 0:
        return _category("members", "required", "only_administrators", facts)
    return _category(
        "members", "complete", "members_active", facts, Action("/access", "Review access")
    )


def _provisioning(f: SetupFacts) -> Category:
    facts: dict[str, FactValue] = {
        "live_tokens": f.live_tokens,
        "provisioned_members": f.provisioned_members,
    }
    if f.live_tokens > 0 and f.token_used:
        return _category("provisioning", "complete", "in_use", facts)
    if f.live_tokens > 0:
        return _category("provisioning", "optional", "token_not_used_yet", facts)
    if f.tokens_ever > 0 and f.provisioned_members > 0:
        return _category("provisioning", "attention", "no_valid_token", facts)
    return _category("provisioning", "optional", "not_configured", facts)


def _ai_policy(f: SetupFacts) -> Category:
    ai = f.ai
    facts: dict[str, FactValue] = {
        "provider": ai.provider if ai.enabled else None,
        "model": ai.model if ai.enabled else None,
        "default_question_sensitivity": f.default_question_sensitivity,
    }
    action = Action("/settings/providers", "View AI providers")
    if not (ai.configured and ai.enabled):
        return _category("ai_policy", "optional", "evidence_only", facts, action)
    if ai.unusable:
        return _category("ai_policy", "attention", "provider_unavailable", facts, action)
    if ai.credential_required and not ai.credential_available:
        return _category("ai_policy", "attention", "credential_failed", facts, action)
    if not ai.allows_default:
        return _category("ai_policy", "attention", "policy_excludes_default", facts, action)
    return _category("ai_policy", "complete", "generation_ready", facts, action)


def _sources(f: SetupFacts) -> Category:
    facts: dict[str, FactValue] = {
        "connected_sources": f.connected_sources,
        "synced_sources": f.synced_sources,
        "failed_sources": f.failed_sources,
        "syncing_sources": f.syncing_sources,
    }
    action = Action("/sources", "Open Sources")
    if f.connected_sources == 0:
        return _category("sources", "required", "no_sources", facts)
    if f.syncing_sources > 0:
        return _category("sources", "in_progress", "syncing", facts, action)
    if f.failed_sources > 0:
        return _category("sources", "attention", "sync_failed", facts, action)
    if f.synced_sources > 0:
        return _category("sources", "complete", "synced", facts, action)
    return _category("sources", "required", "not_synced", facts, action)


def _accounts(f: SetupFacts) -> Category:
    facts: dict[str, FactValue] = {
        "account_sources": f.account_sources,
        "unmapped_references": f.unmapped_references,
    }
    if f.account_sources == 0:
        return _category("accounts", "optional", "not_needed", facts)
    if f.unmapped_references > 0:
        return _category(
            "accounts",
            "attention",
            "unmapped_references",
            facts,
            Action("/sources", "See unmapped references"),
        )
    if f.synced_sources == 0:
        return _category("accounts", "blocked", "sources_required", facts)
    return _category("accounts", "complete", "mapped", facts)


def _first_answer(f: SetupFacts, sources: Category) -> Category:
    facts: dict[str, FactValue] = {"answered": f.answered}
    if f.answered:
        return _category("first_answer", "complete", "answered", facts)
    if sources.state != "complete":
        return _category("first_answer", "blocked", "sources_required", facts)
    return _category(
        "first_answer", "required", "no_answer_yet", facts, Action("/ask", "Ask a question")
    )


def derive(facts: SetupFacts) -> list[Category]:
    sources = _sources(facts)
    return [
        _organization(facts),
        _identity(facts),
        _members(facts),
        _provisioning(facts),
        _ai_policy(facts),
        sources,
        _accounts(facts),
        _first_answer(facts, sources),
    ]
