"""Step-up grants for egress escalation (D-34, phase-12-plan C6.4; migration 0021).

The OIDC callback (``lattice.identity.oidc``) verifies a fresh identity-provider
re-authentication of the administrator in the session that started it; this module is
its database half. ``lattice_create_step_up_grant`` re-checks freshness (``auth_time``
not before the attempt began, at most five minutes old), the session (live, the one the
attempt was bound to) and the administrator, then creates a grant that copies the
attempt's exact change, reason and policy version and expires five minutes after the
authentication at most. The ``egress.step_up_completed`` event is written in the same
transaction (the deferred coupling trigger refuses a grant without it).

A grant authorizes one thing: ``lattice_apply_egress_change`` with exactly that change, on
exactly that policy version, by that administrator in that session, once. It is never an
elevated session: it is not a cookie, not a token the browser holds, and nothing else
reads it as permission.
"""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import text

from lattice.access.audit import record_event
from lattice.db.engine import Database
from lattice.providers.fences import Refused, call

STEP_UP_COMPLETED: Final = "egress.step_up_completed"
# The database's refusals, as the step-up's recorded outcome.
_OUTCOMES: Final = {
    "step_up_not_fresh": "not_fresh",
    "session_ended": "session_mismatch",
}


async def create_grant(
    db: Database,
    organization_id: uuid.UUID,
    *,
    membership_id: uuid.UUID,
    session_id: uuid.UUID,
    attempt_id: uuid.UUID,
    auth_time: datetime,
    acr: str | None,
) -> str:
    """``granted``, or the outcome the database refused it with."""
    try:
        async with db.tenant_transaction(organization_id) as session:
            grant_id: uuid.UUID = await call(
                session,
                "SELECT lattice_create_step_up_grant(:a, :s, :t, :auth, :acr)",
                {
                    "a": membership_id,
                    "s": session_id,
                    "t": attempt_id,
                    "auth": auth_time,
                    "acr": acr,
                },
            )
            grant = (
                await session.execute(
                    text(
                        "SELECT change, encode(change_digest, 'hex') AS digest, reason, "
                        "policy_version, auth_time, expires_at FROM step_up_grants "
                        "WHERE organization_id = :o AND id = :g"
                    ),
                    {"o": organization_id, "g": grant_id},
                )
            ).one()
            await record_event(
                session,
                organization_id,
                actor=f"member:{membership_id}",
                action=STEP_UP_COMPLETED,
                target={"grant_id": str(grant_id), "step_up_id": str(attempt_id)},
                before=None,
                after={
                    "change": grant.change,
                    "change_digest": grant.digest,
                    "policy_version": grant.policy_version,
                    "auth_time": grant.auth_time.isoformat(),
                    "expires_at": grant.expires_at.isoformat(),
                    # What the provider asserted, only when the operator required it.
                    "assurance": acr,
                },
                reason=grant.reason,
            )
    except Refused as refused:
        return _OUTCOMES.get(refused.reason, "step_up_invalid")
    return "granted"
