"""Development-only identity provider (docs/authorization-model.md §2).

Resolves a seeded persona (issuer ``dev``, subject = the user's email) to a user and
one of their active organizations, with no password. It must never run in production;
three independent guards enforce that:

1. configuration: ``LATTICE_DEV_AUTH_ENABLED=true`` with ``LATTICE_ENV=production``
   fails validation, so the process does not start;
2. routing: the dev-login route is only mounted when ``Settings.dev_auth_active``;
3. here: constructing the provider outside development/test raises.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text

from lattice.config import Environment, Settings
from lattice.db.engine import Database
from lattice.errors import ErrorCode, LatticeError

DEV_ISSUER = "dev"

_IDENTITY_MEMBERSHIPS_SQL = text("SELECT * FROM lattice_identity_memberships(:issuer, :subject)")


class DevAuthDisabledError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedIdentity:
    user_id: uuid.UUID
    organization_id: uuid.UUID


def normalize_subject(subject: str) -> str:
    return subject.strip().lower()


class DevIdentityProvider:
    def __init__(self, settings: Settings, db: Database) -> None:
        if settings.env not in (Environment.DEVELOPMENT, Environment.TEST) or not (
            settings.dev_auth_enabled
        ):
            raise DevAuthDisabledError("Development login is not available in this environment")
        self._db = db

    async def resolve(self, subject: str, organization_slug: str | None) -> ResolvedIdentity:
        """Pick the organization to sign in to.

        ``organization_slug`` is a *selector* validated against the identity's own active
        memberships. It is not trusted as tenant authority: after sign-in, the tenant of
        every request comes from the server-side session.
        """
        async with self._db.unscoped_transaction() as db_session:
            rows = (
                (
                    await db_session.execute(
                        _IDENTITY_MEMBERSHIPS_SQL,
                        {"issuer": DEV_ISSUER, "subject": normalize_subject(subject)},
                    )
                )
                .mappings()
                .all()
            )

        if organization_slug is not None:
            wanted = organization_slug.strip().lower()
            rows = [r for r in rows if r["organization_slug"] == wanted]
        if len(rows) != 1:
            # Unknown persona, no active membership, or ambiguous: one generic failure,
            # so the response does not reveal which organizations exist.
            raise LatticeError(ErrorCode.AUTHENTICATION_FAILED)
        return ResolvedIdentity(
            user_id=rows[0]["user_id"], organization_id=rows[0]["organization_id"]
        )
