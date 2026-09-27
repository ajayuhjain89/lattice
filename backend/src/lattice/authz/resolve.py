"""Resolve an ``AuthorizationContext`` from the database (docs/authorization-model.md §7).

Called on every request, inside a transaction already bound to the session's
organization (RLS). There is no cache: a revocation, reassignment, clearance change or
group change committed before the request starts applies to it.

Fails closed -- raises ``AuthorizationContextUnavailable`` -- when the membership does
not exist in the bound organization, when the membership, its user or the organization
is not active, or when any stored attribute is outside the policy's vocabulary.
"""

import logging
import uuid
from typing import NoReturn

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz.context import AuthorizationContext
from lattice.authz.policy import POLICY_VERSION
from lattice.errors import ErrorCode, LatticeError
from lattice.log import log_event

logger = logging.getLogger(__name__)

# One statement: membership, user and organization status, and the three ID sets.
# memberships.is_admin and role_label are deliberately not read.
_CONTEXT_SQL = text(
    """
    SELECT m.id, m.organization_id, m.status, m.clearance, m.account_scope,
           m.authz_updated_at, u.status AS user_status, o.status AS organization_status,
           ARRAY(SELECT aa.account_id FROM account_assignments aa
                 WHERE aa.organization_id = m.organization_id AND aa.membership_id = m.id)
             AS assigned_account_ids,
           ARRAY(SELECT md.department_id FROM membership_departments md
                 WHERE md.organization_id = m.organization_id AND md.membership_id = m.id)
             AS department_ids,
           ARRAY(SELECT gm.group_id FROM access_group_members gm
                 WHERE gm.organization_id = m.organization_id AND gm.membership_id = m.id)
             AS group_ids
    FROM memberships m
    JOIN users u ON u.id = m.user_id
    JOIN organizations o ON o.id = m.organization_id
    WHERE m.organization_id = :organization_id AND m.id = :membership_id
    """
)


class AuthorizationContextUnavailable(LatticeError):
    """No usable authorization context. Rendered like any failed authentication."""

    def __init__(self) -> None:
        super().__init__(ErrorCode.AUTHENTICATION_REQUIRED)


def _unavailable(reason: str, membership_id: uuid.UUID) -> NoReturn:
    log_event(
        logger,
        logging.WARNING,
        "authz.context.unavailable",
        reason=reason,
        membership_id=str(membership_id),
    )
    raise AuthorizationContextUnavailable()


async def resolve_authorization_context(
    session: AsyncSession, *, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> AuthorizationContext:
    """Both IDs must come from the server-side session (``Principal``)."""
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
        _unavailable("membership_not_found", membership_id)
    if row["organization_status"] != "active" or row["user_status"] != "active":
        _unavailable("organization_or_user_inactive", membership_id)
    if row["status"] != "active":
        _unavailable("membership_inactive", membership_id)
    try:
        return AuthorizationContext(
            organization_id=row["organization_id"],
            membership_id=row["id"],
            membership_status=row["status"],
            clearance=row["clearance"],
            account_scope=row["account_scope"],
            assigned_account_ids=frozenset(row["assigned_account_ids"]),
            department_ids=frozenset(row["department_ids"]),
            group_ids=frozenset(row["group_ids"]),
            authz_updated_at=row["authz_updated_at"],
            policy_version=POLICY_VERSION,
        )
    except ValidationError:
        _unavailable("malformed_attributes", membership_id)
