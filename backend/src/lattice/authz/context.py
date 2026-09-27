"""The authorization vocabulary and the per-request ``AuthorizationContext``.

An ``AuthorizationContext`` says what one member may read. It is resolved from the
database on every request (``lattice.authz.resolve``) from trusted server-side state
only: the organization and membership come from the server-side session, and every
other field comes from rows of that organization. Nothing in it is ever taken from
client input -- not the organization, role, departments, account assignments, groups
or clearance. Client filters may only *narrow* a query built on top of it.

It deliberately has no administrator or role-label field. Administrative capability
lives on the authenticated principal and grants no data access (SEC-15), so the
policy cannot consult it even by mistake.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# How sensitive a document is. Compared against clearance; ``restricted`` is never
# reachable through clearance (only through grants).
Sensitivity = Literal["internal", "confidential", "restricted"]
# How access to a document is decided: the policy rules, or explicit grants only.
AccessMode = Literal["policy", "grants_only"]
Clearance = Literal["internal", "confidential"]
AccountScope = Literal["all", "assigned"]
MembershipStatus = Literal["active", "suspended", "removed"]
PrincipalType = Literal["membership", "department", "group"]
DocumentStatus = Literal["active", "deleted"]


class AuthorizationContext(BaseModel):
    """Everything the policy knows about the reader. Immutable; validated strictly, so a
    malformed attribute cannot be constructed (the resolver then fails closed)."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    organization_id: uuid.UUID
    membership_id: uuid.UUID
    membership_status: MembershipStatus
    clearance: Clearance
    account_scope: AccountScope
    # Accounts this member is assigned to. Consulted only when account_scope="assigned".
    assigned_account_ids: frozenset[uuid.UUID]
    department_ids: frozenset[uuid.UUID]
    group_ids: frozenset[uuid.UUID]
    # When this membership's access last changed (recorded in traces, Phase 6).
    authz_updated_at: datetime
    # The policy version this context was resolved for (``lattice.authz.policy``).
    policy_version: str
