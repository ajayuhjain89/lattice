"""The reference authorization policy (docs/authorization-model.md §4).

This module is the semantic source of truth for "may this member read this?". It is
plain Python with no I/O, written to be read top to bottom by a reviewer. The SQL
predicate in ``lattice.authz.sql`` is a second implementation of the same rules, and
property-based tests prove the two agree on every generated case.

A member may read a document if and only if:

    the document is in the member's organization
    AND the member's membership is active
    AND the document is active
    AND (
          an explicit grant names the member, one of their departments or one of their
          groups                                                        -- grant path
       OR (   the document's access mode is "policy"                    -- policy path
          AND the document is not restricted
          AND the member's clearance covers the document's sensitivity
          AND (the document is not account-specific
               OR the member's account scope is "all"
               OR the member is assigned to the document's account)
          AND (the document has no department restriction
               OR the member belongs to one of its departments) )
    )

Everything unknown fails closed: an unrecognised sensitivity, access mode, clearance,
account scope, grant principal type or document status never satisfies a clause.
Administrative capability and role labels are not inputs: they are not in the
context at all.

Changing the behaviour of this module, ``lattice.authz.sql`` or
``lattice.authz.resolve`` requires bumping ``POLICY_VERSION``
(enforced by ``tests/test_authz_boundary.py``).
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

from lattice.authz.context import AuthorizationContext

POLICY_VERSION: Final = "authz-2"

# Clearance levels and the sensitivities they cover. "restricted" is deliberately
# absent: no clearance ever reaches restricted content.
_CLEARANCE_RANK: Final = {"internal": 0, "confidential": 1}
_SENSITIVITY_RANK: Final = {"internal": 0, "confidential": 1}


@dataclass(frozen=True, slots=True)
class GrantRef:
    """One explicit grant on a document, as the policy sees it."""

    organization_id: uuid.UUID
    principal_type: str
    principal_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class DocumentAccess:
    """The access-relevant facts of one document. Values are kept as stored (plain
    strings), so that a value the policy does not recognise can be represented -- and
    denied -- rather than rejected before the policy sees it."""

    id: uuid.UUID
    organization_id: uuid.UUID
    status: str
    sensitivity: str
    access_mode: str
    account_id: uuid.UUID | None
    department_ids: frozenset[uuid.UUID]
    grants: tuple[GrantRef, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountRef:
    id: uuid.UUID
    organization_id: uuid.UUID


def readable(ctx: AuthorizationContext, doc: DocumentAccess) -> bool:
    """May the member described by ``ctx`` read ``doc``?"""
    if doc.organization_id != ctx.organization_id:
        return False
    if ctx.membership_status != "active":
        return False
    if doc.status != "active":
        return False
    if matching_grants(ctx, doc):
        return True
    return policy_allows(ctx, doc)


def matching_grants(ctx: AuthorizationContext, doc: DocumentAccess) -> tuple[GrantRef, ...]:
    """The document's grants that apply to this member (the grant path)."""
    return tuple(grant for grant in doc.grants if grant_applies(ctx, doc, grant))


def grant_applies(ctx: AuthorizationContext, doc: DocumentAccess, grant: GrantRef) -> bool:
    if grant.organization_id != doc.organization_id:
        return False
    if grant.principal_type == "membership":
        return grant.principal_id == ctx.membership_id
    if grant.principal_type == "department":
        return grant.principal_id in ctx.department_ids
    if grant.principal_type == "group":
        return grant.principal_id in ctx.group_ids
    return False


def policy_allows(ctx: AuthorizationContext, doc: DocumentAccess) -> bool:
    """The policy path: every clause must hold. Grants are not consulted here."""
    return (
        doc.access_mode == "policy"
        and doc.sensitivity != "restricted"
        and clearance_covers(ctx.clearance, doc.sensitivity)
        and account_in_scope(ctx, doc.account_id)
        and departments_satisfied(ctx, doc.department_ids)
    )


def clearance_covers(clearance: str, sensitivity: str) -> bool:
    if clearance not in _CLEARANCE_RANK or sensitivity not in _SENSITIVITY_RANK:
        return False
    return _SENSITIVITY_RANK[sensitivity] <= _CLEARANCE_RANK[clearance]


def account_in_scope(ctx: AuthorizationContext, account_id: uuid.UUID | None) -> bool:
    if account_id is None:
        return True
    if ctx.account_scope == "all":
        return True
    if ctx.account_scope == "assigned":
        return account_id in ctx.assigned_account_ids
    return False


def departments_satisfied(ctx: AuthorizationContext, department_ids: Collection[uuid.UUID]) -> bool:
    if not department_ids:
        return True
    return not ctx.department_ids.isdisjoint(department_ids)


def account_visible(ctx: AuthorizationContext, account: AccountRef) -> bool:
    """May this member know that ``account`` exists (scope pickers, filters, account
    mention detection, autocomplete)? Only accounts inside their account scope
    (docs/security-model.md §10). This is deliberately independent of ``readable``:
    an explicit grant can make one document of an out-of-scope account readable
    without putting that account into the member's lists or filters. The admin account
    directory is a separate, capability-gated view (Phase 9)."""
    if account.organization_id != ctx.organization_id:
        return False
    if ctx.membership_status != "active":
        return False
    if ctx.account_scope == "all":
        return True
    if ctx.account_scope == "assigned":
        return account.id in ctx.assigned_account_ids
    return False


def visible_document_account(ctx: AuthorizationContext, doc: DocumentAccess) -> uuid.UUID | None:
    """The account to show as *structured metadata* of ``doc`` (an evidence card's
    account field, the account attribute of prompt evidence, a facet value) -- or
    ``None`` when it must be omitted (decision D-8).

    Document readability and account visibility are separate decisions. A member may
    read a document through an explicit grant while its account is outside their
    account scope; the document and its content stay readable, but the grant does not
    disclose the structured account record: it is omitted here, never replaced by a
    substitute name, and the account stays out of the member's lists, filters,
    mention detection and autocomplete (``account_visible``). Text inside the
    document that names the customer is content, and is governed by ``readable``.

    Unreadable documents disclose nothing, so they yield ``None`` as well.
    """
    if doc.account_id is None or not readable(ctx, doc):
        return None
    if account_visible(ctx, AccountRef(doc.account_id, doc.organization_id)):
        return doc.account_id
    return None
