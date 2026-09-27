"""Server-side sessions (ADR-005, docs/authorization-model.md §2).

A request is authenticated only if all of these hold, checked on every request:

* the token's hash matches a session row (found via the SECURITY DEFINER lookup,
  because the organization is not yet known);
* the session is not revoked, not past its absolute expiry, and not idle for longer
  than the idle timeout;
* in that session's organization (now bound for RLS): the organization, the user and
  the membership are all ``active``.

Every failure produces the same ``authentication_required`` error, so a client cannot
tell which check failed. The organization of the request always comes from the
session row, never from the client.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.db.engine import Database, bind_organization
from lattice.errors import ErrorCode, LatticeError
from lattice.identity.models import Session, User
from lattice.identity.tokens import hash_session_token, is_well_formed, new_session_token
from lattice.log import log_event
from lattice.orgs.models import Membership, Organization

logger = logging.getLogger(__name__)

_RESOLVE_SESSION_SQL = text("SELECT * FROM lattice_resolve_session(:token_hash)")


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, derived entirely from server-side state."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID
    email: str
    display_name: str
    organization_slug: str
    organization_name: str
    role_label: str | None
    # Administrative capability only; confers no content access (SEC-15).
    is_admin: bool


@dataclass(frozen=True)
class SessionPolicy:
    idle_timeout: timedelta
    absolute_timeout: timedelta
    # Avoid a write per request: refresh last_seen_at at most this often.
    touch_interval: timedelta = timedelta(seconds=60)


class SessionService:
    def __init__(
        self, db: Database, policy: SessionPolicy, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._db = db
        self._policy = policy
        self._clock = clock

    async def create(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> tuple[str, Principal]:
        """Create a new session. Returns the raw token (for the cookie only)."""
        token = new_session_token()
        now = self._clock()
        async with self._db.tenant_transaction(organization_id) as db_session:
            row = Session(
                token_hash=hash_session_token(token),
                user_id=user_id,
                organization_id=organization_id,
                created_at=now,
                last_seen_at=now,
                expires_at=now + self._policy.absolute_timeout,
            )
            db_session.add(row)
            await db_session.flush()
            principal = await _load_principal(db_session, row.id, user_id, organization_id)
            if principal is None:
                # The membership became inactive after identity resolution. Raising
                # inside the block rolls back the session insert.
                raise LatticeError(ErrorCode.AUTHENTICATION_FAILED)
        log_event(
            logger,
            logging.INFO,
            "auth.session.created",
            session_id=str(principal.session_id),
            user_id=str(user_id),
            organization_id=str(organization_id),
        )
        return token, principal

    async def authenticate(self, token: str) -> Principal:
        if not is_well_formed(token):
            self._reject("malformed_token")
        now = self._clock()
        async with self._db.unscoped_transaction() as db_session:
            found = (
                (
                    await db_session.execute(
                        _RESOLVE_SESSION_SQL, {"token_hash": hash_session_token(token)}
                    )
                )
                .mappings()
                .one_or_none()
            )
            if found is None:
                self._reject("unknown_session")
            if found["revoked_at"] is not None:
                self._reject("revoked", found["session_id"])
            if now >= found["expires_at"]:
                self._reject("expired", found["session_id"])
            if now >= found["last_seen_at"] + self._policy.idle_timeout:
                self._reject("idle_timeout", found["session_id"])

            # From here on, the session's organization is bound: RLS confines every
            # query in this transaction to that tenant.
            await bind_organization(db_session, found["organization_id"])
            principal = await _load_principal(
                db_session, found["session_id"], found["user_id"], found["organization_id"]
            )
            if principal is None:
                self._reject("inactive_membership_user_or_org", found["session_id"])
            if now - found["last_seen_at"] >= self._policy.touch_interval:
                await db_session.execute(
                    update(Session)
                    .where(Session.id == principal.session_id)
                    .values(last_seen_at=now)
                )
        return principal

    async def revoke(self, principal: Principal) -> None:
        async with self._db.tenant_transaction(principal.organization_id) as db_session:
            await db_session.execute(
                update(Session)
                .where(Session.id == principal.session_id, Session.revoked_at.is_(None))
                .values(revoked_at=self._clock())
            )
        log_event(
            logger, logging.INFO, "auth.session.revoked", session_id=str(principal.session_id)
        )

    async def revoke_token_if_valid(self, token: str) -> None:
        """Revoke whatever session a presented token belongs to (session rotation)."""
        try:
            principal = await self.authenticate(token)
        except LatticeError:
            return
        await self.revoke(principal)

    def _reject(self, reason: str, session_id: uuid.UUID | None = None) -> NoReturn:
        log_event(
            logger,
            logging.INFO,
            "auth.session.rejected",
            reason=reason,
            session_id=str(session_id) if session_id else None,
        )
        raise LatticeError(ErrorCode.AUTHENTICATION_REQUIRED)


async def _load_principal(
    db_session: AsyncSession,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> Principal | None:
    """Load the caller inside a transaction already bound to ``organization_id``."""
    row = (
        await db_session.execute(
            select(Membership, User, Organization)
            .join(User, User.id == Membership.user_id)
            .join(Organization, Organization.id == Membership.organization_id)
            .where(
                Membership.user_id == user_id,
                Membership.organization_id == organization_id,
                Membership.status == "active",
                User.status == "active",
                Organization.status == "active",
            )
        )
    ).one_or_none()
    if row is None:
        return None
    membership, user, organization = row
    return Principal(
        session_id=session_id,
        user_id=user.id,
        organization_id=organization.id,
        membership_id=membership.id,
        email=user.email,
        display_name=user.display_name,
        organization_slug=organization.slug,
        organization_name=organization.name,
        role_label=membership.role_label,
        is_admin=membership.is_admin,
    )
