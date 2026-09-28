"""A context summary for query traces (data-model §7 ``authz_snapshot``): enough to
explain behaviour across policy versions, not an ACL dump -- counts, not identifiers."""

from typing import Any

from lattice.authz.context import AuthorizationContext


def summarize_context(ctx: AuthorizationContext) -> dict[str, Any]:
    return {
        "clearance": ctx.clearance,
        "account_scope": ctx.account_scope,
        "assigned_account_count": len(ctx.assigned_account_ids),
        "department_count": len(ctx.department_ids),
        "group_count": len(ctx.group_ids),
        "authz_updated_at": ctx.authz_updated_at.isoformat() if ctx.authz_updated_at else None,
        "policy_version": ctx.policy_version,
    }
