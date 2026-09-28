"""AI data egress policy (SEC-10, docs/security-model.md §6).

Egress answers "may this content be sent to this model provider?" -- a data policy,
separate from authorization ("may this person read this?"). A member can be fully
authorized to read a confidential document that is never sent to an external model.

The effective level for a provider is
``min(org_settings.external_egress_level, model_providers.approved_for_level)``;
content may be sent when its classification is at or below that level. Defaults
(an organization without settings, a provider without a record) are ``internal``.
Anything unrecognised is never sent.
"""

from typing import Final, Literal

Level = Literal["internal", "confidential", "restricted"]

DEFAULT_LEVEL: Final[Level] = "internal"
_RANK: Final = {"internal": 0, "confidential": 1, "restricted": 2}


def effective_level(org_level: str, provider_approved_level: str) -> str | None:
    """The most sensitive classification that may be sent, or None if either input is
    unrecognised (then nothing is sent)."""
    if org_level not in _RANK or provider_approved_level not in _RANK:
        return None
    return min(org_level, provider_approved_level, key=_RANK.__getitem__)


def may_send(classification: str, level: str | None) -> bool:
    """May content classified ``classification`` go to a provider at ``level``?"""
    if level is None or level not in _RANK or classification not in _RANK:
        return False
    return _RANK[classification] <= _RANK[level]
