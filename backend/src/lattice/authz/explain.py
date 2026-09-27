"""Explanations of authorization decisions (docs/authorization-model.md §6).

Three functions, from most to least privileged output:

* ``explain(ctx, doc)`` -- the full, internal explanation: every clause of the policy
  for this member and document, including why access is *denied*. It reveals facts
  about the document (that it is restricted, account-specific, department-limited,
  granted to someone) and about the member. **Trusted callers only**: debugging,
  tests, and the two gated functions below. Never return it to an API caller
  directly.
* ``explain_own_access(ctx, doc)`` -- for a member asking "why can I see this?".
  Returns ``None`` unless they can read the document, and ``None`` for a missing
  document too, so it can never tell "exists but unreadable" from "does not exist"
  (SEC-5). Contains only the reasons that grant access.
* ``explain_for_reviewer(reviewer, subject, doc)`` -- for the future access UI
  (Phase 9): why can or can't ``subject`` read ``doc``? Returns ``None`` unless the
  reviewer can read the document themselves and both are in the same organization,
  so it is not an oracle over documents the reviewer cannot see (security-model §10:
  admin pages never show document-level facts about documents the admin cannot read).
  Whether the reviewer may review access at all (the administrator capability) is
  checked by the calling route (E7); being able to review grants no data access.

The ``readable`` field always comes from ``policy.readable`` itself, so an
explanation can never disagree with the decision it explains.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from lattice.authz.context import AuthorizationContext
from lattice.authz.policy import (
    DocumentAccess,
    GrantRef,
    account_in_scope,
    clearance_covers,
    departments_satisfied,
    matching_grants,
    readable,
)


class AccessReason(StrEnum):
    # Reasons that grant access.
    GRANT_TO_MEMBER = "grant_to_member"
    GRANT_TO_DEPARTMENT = "grant_to_department"
    GRANT_TO_GROUP = "grant_to_group"
    CLEARANCE_COVERS_SENSITIVITY = "clearance_covers_sensitivity"
    NOT_ACCOUNT_SPECIFIC = "not_account_specific"
    ACCOUNT_SCOPE_ALL = "account_scope_all"
    ACCOUNT_ASSIGNED = "account_assigned"
    NO_DEPARTMENT_RESTRICTION = "no_department_restriction"
    DEPARTMENT_MEMBER = "department_member"
    # Reasons that deny access.
    DIFFERENT_ORGANIZATION = "different_organization"
    MEMBERSHIP_INACTIVE = "membership_inactive"
    DOCUMENT_INACTIVE = "document_inactive"
    GRANTS_ONLY_WITHOUT_GRANT = "grants_only_without_grant"
    RESTRICTED_WITHOUT_GRANT = "restricted_without_grant"
    UNKNOWN_ACCESS_MODE = "unknown_access_mode"
    UNKNOWN_SENSITIVITY = "unknown_sensitivity"
    CLEARANCE_INSUFFICIENT = "clearance_insufficient"
    ACCOUNT_NOT_IN_SCOPE = "account_not_in_scope"
    DEPARTMENT_NOT_MATCHED = "department_not_matched"


GRANTING_REASONS = frozenset(
    {
        AccessReason.GRANT_TO_MEMBER,
        AccessReason.GRANT_TO_DEPARTMENT,
        AccessReason.GRANT_TO_GROUP,
        AccessReason.CLEARANCE_COVERS_SENSITIVITY,
        AccessReason.NOT_ACCOUNT_SPECIFIC,
        AccessReason.ACCOUNT_SCOPE_ALL,
        AccessReason.ACCOUNT_ASSIGNED,
        AccessReason.NO_DEPARTMENT_RESTRICTION,
        AccessReason.DEPARTMENT_MEMBER,
    }
)

_GRANT_REASON = {
    "membership": AccessReason.GRANT_TO_MEMBER,
    "department": AccessReason.GRANT_TO_DEPARTMENT,
    "group": AccessReason.GRANT_TO_GROUP,
}


@dataclass(frozen=True, slots=True)
class AccessExplanation:
    """Internal explanation. ``path`` is how access is obtained: "grant", "policy", or
    "none" when the document is not readable."""

    document_id: uuid.UUID
    readable: bool
    path: str
    reasons: tuple[AccessReason, ...]
    matched_grants: tuple[GrantRef, ...]
    policy_version: str


@dataclass(frozen=True, slots=True)
class OwnAccessExplanation:
    """What a member may be told about their own access to a document they can read."""

    reasons: tuple[AccessReason, ...]
    policy_version: str


def explain(ctx: AuthorizationContext, doc: DocumentAccess) -> AccessExplanation:
    """Full internal explanation. Trusted callers only (see module docstring)."""
    is_readable = readable(ctx, doc)

    def result(
        path: str, reasons: list[AccessReason], grants: tuple[GrantRef, ...] = ()
    ) -> AccessExplanation:
        return AccessExplanation(
            document_id=doc.id,
            readable=is_readable,
            path=path if is_readable else "none",
            reasons=tuple(reasons),
            matched_grants=grants,
            policy_version=ctx.policy_version,
        )

    # Hard boundaries first; nothing further is evaluated or revealed past them.
    if doc.organization_id != ctx.organization_id:
        return result("none", [AccessReason.DIFFERENT_ORGANIZATION])
    if ctx.membership_status != "active":
        return result("none", [AccessReason.MEMBERSHIP_INACTIVE])
    if doc.status != "active":
        return result("none", [AccessReason.DOCUMENT_INACTIVE])

    grants = matching_grants(ctx, doc)
    if grants:
        reasons = sorted({_GRANT_REASON[g.principal_type] for g in grants})
        return result("grant", reasons, grants)
    return result("policy", _policy_reasons(ctx, doc))


def _policy_reasons(ctx: AuthorizationContext, doc: DocumentAccess) -> list[AccessReason]:
    """Every policy-path clause, granted or denied (not just the first failure)."""
    if doc.access_mode == "grants_only":
        if doc.sensitivity == "restricted":
            return [AccessReason.RESTRICTED_WITHOUT_GRANT]
        return [AccessReason.GRANTS_ONLY_WITHOUT_GRANT]
    if doc.access_mode != "policy":
        return [AccessReason.UNKNOWN_ACCESS_MODE]
    if doc.sensitivity == "restricted":  # policy mode + restricted: invalid, fail closed
        return [AccessReason.RESTRICTED_WITHOUT_GRANT]
    if doc.sensitivity not in ("internal", "confidential"):
        return [AccessReason.UNKNOWN_SENSITIVITY]

    reasons = [
        AccessReason.CLEARANCE_COVERS_SENSITIVITY
        if clearance_covers(ctx.clearance, doc.sensitivity)
        else AccessReason.CLEARANCE_INSUFFICIENT
    ]
    if doc.account_id is None:
        reasons.append(AccessReason.NOT_ACCOUNT_SPECIFIC)
    elif not account_in_scope(ctx, doc.account_id):
        reasons.append(AccessReason.ACCOUNT_NOT_IN_SCOPE)
    elif ctx.account_scope == "all":
        reasons.append(AccessReason.ACCOUNT_SCOPE_ALL)
    else:
        reasons.append(AccessReason.ACCOUNT_ASSIGNED)
    if not doc.department_ids:
        reasons.append(AccessReason.NO_DEPARTMENT_RESTRICTION)
    elif departments_satisfied(ctx, doc.department_ids):
        reasons.append(AccessReason.DEPARTMENT_MEMBER)
    else:
        reasons.append(AccessReason.DEPARTMENT_NOT_MATCHED)
    return reasons


def explain_own_access(
    ctx: AuthorizationContext, doc: DocumentAccess | None
) -> OwnAccessExplanation | None:
    """Why the member can read ``doc`` -- or ``None``, identically, when the document is
    missing or unreadable."""
    if doc is None or not readable(ctx, doc):
        return None
    full = explain(ctx, doc)
    return OwnAccessExplanation(
        reasons=tuple(r for r in full.reasons if r in GRANTING_REASONS),
        policy_version=full.policy_version,
    )


def explain_for_reviewer(
    reviewer: AuthorizationContext, subject: AuthorizationContext, doc: DocumentAccess | None
) -> AccessExplanation | None:
    """Explain ``subject``'s access to ``doc`` to a reviewer who can read ``doc``
    themselves; ``None`` otherwise (missing, unreadable to the reviewer, or another
    organization's member)."""
    if doc is None or subject.organization_id != reviewer.organization_id:
        return None
    if not readable(reviewer, doc):
        return None
    return explain(subject, doc)
