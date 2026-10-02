"""Contexts for access review (Phase 9): another member's access, as it is or as a
proposed change would make it.

An administrator reviewing or changing a member's access needs that member's
``AuthorizationContext`` -- including when the member is suspended (to preview a
reactivation) -- and the context a change *would* produce, to compare what each can
read (counts only over documents the administrator can read; security-model §10).

* ``resolve_member_context`` reads the same facts as ``resolve_authorization_context``
  (the same statement) but does not refuse an inactive member: the context carries the
  stored membership status, and the policy denies every read for anything but
  ``active``. A disabled user or a suspended organization is represented as a
  suspended membership (they can read nothing either). ``None`` when the membership is
  not in the bound organization, or an attribute is outside the vocabulary.
* ``proposed_context`` is a validated copy with some attributes replaced.

These contexts describe *someone else*. They are never the request's own context: a
request reads with ``resolve_authorization_context`` of its own session only.
"""

import uuid
from collections.abc import Collection

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz.context import AuthorizationContext
from lattice.authz.policy import POLICY_VERSION
from lattice.authz.resolve import _CONTEXT_SQL


async def resolve_member_context(
    session: AsyncSession, *, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> AuthorizationContext | None:
    row = (
        (
            await session.execute(
                _CONTEXT_SQL,
                {"organization_id": organization_id, "membership_id": membership_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    status = row["status"]
    if status == "active" and (
        row["organization_status"] != "active" or row["user_status"] != "active"
    ):
        status = "suspended"
    try:
        return AuthorizationContext(
            organization_id=row["organization_id"],
            membership_id=row["id"],
            membership_status=status,
            clearance=row["clearance"],
            account_scope=row["account_scope"],
            assigned_account_ids=frozenset(row["assigned_account_ids"]),
            department_ids=frozenset(row["department_ids"]),
            group_ids=frozenset(row["group_ids"]),
            authz_updated_at=row["authz_updated_at"],
            policy_version=POLICY_VERSION,
        )
    except ValidationError:
        return None


def proposed_context(
    ctx: AuthorizationContext,
    *,
    membership_status: str | None = None,
    clearance: str | None = None,
    account_scope: str | None = None,
    accounts: Collection[uuid.UUID] | None = None,
    departments: Collection[uuid.UUID] | None = None,
    groups: Collection[uuid.UUID] | None = None,
) -> AuthorizationContext:
    """``ctx`` with the given attributes replaced (``accounts``, ``departments`` and
    ``groups`` are the assigned account, department and group ID sets), validated like
    any context (an out-of-vocabulary value raises ``pydantic.ValidationError``)."""
    values = ctx.model_dump()
    replacements = {
        "membership_status": membership_status,
        "clearance": clearance,
        "account_scope": account_scope,
    }
    values.update({k: v for k, v in replacements.items() if v is not None})
    for name, ids in (
        ("assigned_account_ids", accounts),
        ("department_ids", departments),
        ("group_ids", groups),
    ):
        values[name] = frozenset(values[name] if ids is None else ids)
    return AuthorizationContext(**values)
