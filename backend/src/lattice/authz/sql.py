"""The SQL form of the authorization policy (enforcement point E3, ADR-003).

``readable_documents_predicate(ctx)`` returns a SQLAlchemy boolean expression over the
``documents`` table (or an alias of it) that is true exactly when
``lattice.authz.policy.readable`` would return True for that row. Retrieval compiles
it into the same statement as its lexical or semantic condition, so unauthorized rows
never leave PostgreSQL::

    SELECT ... FROM chunks JOIN documents d ON ...
    WHERE <readable_documents_predicate(ctx, d)> AND <retrieval condition>

There is no retrieve-then-filter path (SEC-3). Client filters are additional AND
conditions; they can narrow the predicate but never replace or widen it.

This module is intentionally independent of ``lattice.authz.policy``: it does not call
the reference functions or share its tables, so the property-based equivalence test
compares two implementations rather than one implementation with itself.

Every context value is sent as a bound parameter (arrays for ID sets), so the SQL
text depends only on the policy, never on who is asking.
"""

import uuid
from collections.abc import Collection

from sqlalchemy import (
    ColumnElement,
    FromClause,
    Table,
    and_,
    any_,
    exists,
    false,
    func,
    literal,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from lattice.authz.context import AuthorizationContext
from lattice.authz.models import Document, DocumentGrant
from lattice.orgs.models import Account

# Sensitivities readable on the policy path at each clearance. "restricted" appears
# nowhere: it is reachable only through a grant. An unknown clearance maps to nothing.
_SENSITIVITIES_AT_CLEARANCE: dict[str, tuple[str, ...]] = {
    "internal": ("internal",),
    "confidential": ("internal", "confidential"),
}

_UUID_ARRAY = ARRAY(UUID(as_uuid=True))


def _table(model: type[Document] | type[DocumentGrant] | type[Account]) -> Table:
    table = model.__table__
    if not isinstance(table, Table):  # pragma: no cover - declarative models map to Tables
        raise TypeError(model)
    return table


DOCUMENTS = _table(Document)
_GRANTS = _table(DocumentGrant)
ACCOUNTS = _table(Account)


def _ids(values: Collection[uuid.UUID]) -> ColumnElement[list[uuid.UUID]]:
    # Sorted, so the same context always produces the same parameters.
    return literal(sorted(values), _UUID_ARRAY)


def readable_documents_predicate(
    ctx: AuthorizationContext, documents: FromClause = DOCUMENTS
) -> ColumnElement[bool]:
    """Rows of ``documents`` the member may read. ``documents`` may be an alias."""
    if ctx.membership_status != "active":
        return false()
    d = documents.c
    grant = _GRANTS.alias("authz_grant")
    granted = exists(
        select(1).where(
            grant.c.document_id == d.id,
            grant.c.organization_id == ctx.organization_id,
            or_(
                grant.c.membership_id == ctx.membership_id,
                grant.c.department_id == any_(_ids(ctx.department_ids)),
                grant.c.group_id == any_(_ids(ctx.group_ids)),
            ),
        )
    )

    if ctx.account_scope == "all":
        account_ok: ColumnElement[bool] = true()
    elif ctx.account_scope == "assigned":
        account_ok = or_(
            d.account_id.is_(None), d.account_id == any_(_ids(ctx.assigned_account_ids))
        )
    else:  # pragma: no cover - AuthorizationContext cannot hold another value
        account_ok = d.account_id.is_(None)

    policy_path = and_(
        d.access_mode == "policy",
        d.sensitivity.in_(_SENSITIVITIES_AT_CLEARANCE.get(ctx.clearance, ())),
        account_ok,
        or_(
            func.cardinality(d.department_ids) == 0,
            d.department_ids.overlap(_ids(ctx.department_ids)),
        ),
    )
    return and_(
        d.organization_id == ctx.organization_id,
        d.status == "active",
        or_(granted, policy_path),
    )


def visible_accounts_predicate(
    ctx: AuthorizationContext, accounts: FromClause = ACCOUNTS
) -> ColumnElement[bool]:
    """Accounts whose existence this member may learn: lists, filters, scope pickers,
    account-mention detection and any future autocomplete (security-model §10)."""
    if ctx.membership_status != "active":
        return false()
    a = accounts.c
    if ctx.account_scope == "all":
        in_scope: ColumnElement[bool] = true()
    elif ctx.account_scope == "assigned":
        in_scope = a.id == any_(_ids(ctx.assigned_account_ids))
    else:  # pragma: no cover - AuthorizationContext cannot hold another value
        in_scope = false()
    return and_(a.organization_id == ctx.organization_id, in_scope)
