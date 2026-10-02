"""Operator actions for identity (Phase 10): the organization's OIDC connection, its
SCIM tokens, and the administrator capability. Run as ``lattice_owner`` by the
``lattice sso``, ``lattice scim`` and ``lattice member`` commands, each with a stated
reason and an audit event in the same transaction:

* ``sso.connection_created`` / ``sso.connection_disabled`` (target: the connection);
* ``scim.token_created`` / ``scim.token_revoked`` (target: the token's ID);
* ``access.member_changed`` for the administrator capability (before/after snapshots,
  as every access change) -- the only way ``is_admin`` changes; the API cannot (0012).

Events never hold a secret or a secret reference: a connection's client secret is
recorded as configured or not. The raw SCIM token is returned once, to print.
"""

import secrets
import uuid
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.audit import MEMBER_CHANGED, record_event
from lattice.access.directory import access_snapshot
from lattice.provisioning.tokens import hash_token, new_token


class OperatorRejected(Exception):
    """A refused operator action; the message is safe to print."""


@dataclass(frozen=True)
class ConnectionView:
    id: uuid.UUID
    login_key: str
    issuer: str
    client_id: str
    subject_claim: str
    confidential: bool
    status: str


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _check_issuer(issuer: str) -> str:
    parts = urlsplit(issuer)
    local = parts.hostname in _LOCAL_HOSTS
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise OperatorRejected("the issuer must be an https:// URL (http only for localhost)")
    if not parts.netloc or "@" in parts.netloc or parts.query or parts.fragment:
        raise OperatorRejected("the issuer must be a plain URL without credentials or query")
    return issuer


async def active_connection(session: AsyncSession, org: uuid.UUID) -> ConnectionView | None:
    row = (
        await session.execute(
            text(
                "SELECT id, login_key, issuer, client_id, subject_claim, "
                "client_secret_ref IS NOT NULL AS confidential, status FROM oidc_connections "
                "WHERE organization_id = :o AND status = 'active'"
            ),
            {"o": org},
        )
    ).one_or_none()
    return None if row is None else ConnectionView(**row._mapping)


async def add_connection(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    issuer: str,
    client_id: str,
    secret_ref: str | None,
    subject_claim: str,
    reason: str,
    actor: str,
) -> ConnectionView:
    if await active_connection(session, org) is not None:
        raise OperatorRejected(
            "the organization already has an active connection; disable it first"
        )
    if secret_ref is not None and not secret_ref.startswith("env:LATTICE_OIDC_SECRET_"):
        raise OperatorRejected(
            "the client secret must be a reference env:LATTICE_OIDC_SECRET_<NAME>"
        )
    login_key = secrets.token_urlsafe(24)
    try:
        connection_id: uuid.UUID = (
            await session.execute(
                text(
                    "INSERT INTO oidc_connections (organization_id, login_key, issuer, "
                    "client_id, client_secret_ref, subject_claim) "
                    "VALUES (:o, :k, :i, :c, :s, :claim) RETURNING id"
                ),
                {
                    "o": org,
                    "k": login_key,
                    "i": _check_issuer(issuer),
                    "c": client_id,
                    "s": secret_ref,
                    "claim": subject_claim,
                },
            )
        ).scalar_one()
    except DBAPIError as exc:  # a CHECK constraint: report it without the values
        raise OperatorRejected(
            "the connection was refused by the database (issuer, client ID, secret reference "
            "or claim name has an invalid form)"
        ) from exc
    await record_event(
        session,
        org,
        actor=actor,
        action="sso.connection_created",
        target={"connection_id": str(connection_id)},
        before=None,
        after={
            "issuer": issuer,
            "client_id": client_id,
            "subject_claim": subject_claim,
            "client_secret": "reference" if secret_ref else "none",
        },
        reason=reason,
    )
    found = await active_connection(session, org)
    assert found is not None  # noqa: S101 - inserted above
    return found


async def disable_connection(
    session: AsyncSession, org: uuid.UUID, *, reason: str, actor: str
) -> ConnectionView:
    found = await active_connection(session, org)
    if found is None:
        raise OperatorRejected("the organization has no active connection")
    await session.execute(
        text("UPDATE oidc_connections SET status = 'disabled' WHERE id = :c"), {"c": found.id}
    )
    await record_event(
        session,
        org,
        actor=actor,
        action="sso.connection_disabled",
        target={"connection_id": str(found.id)},
        before={"status": "active"},
        after={"status": "disabled"},
        reason=reason,
    )
    return replace(found, status="disabled")


async def create_scim_token(
    session: AsyncSession, org: uuid.UUID, *, reason: str, actor: str
) -> tuple[uuid.UUID, str]:
    connection = await active_connection(session, org)
    if connection is None:
        raise OperatorRejected("SCIM needs an active OIDC connection (lattice sso add)")
    token = new_token()
    token_id: uuid.UUID = (
        await session.execute(
            text(
                "INSERT INTO scim_tokens (organization_id, connection_id, token_hash) "
                "VALUES (:o, :c, :h) RETURNING id"
            ),
            {"o": org, "c": connection.id, "h": hash_token(token)},
        )
    ).scalar_one()
    await record_event(
        session,
        org,
        actor=actor,
        action="scim.token_created",
        target={"token_id": str(token_id)},
        before=None,
        after={"connection_id": str(connection.id)},
        reason=reason,
    )
    return token_id, token


async def revoke_scim_token(
    session: AsyncSession, org: uuid.UUID, token_id: uuid.UUID, *, reason: str, actor: str
) -> bool:
    revoked = (
        await session.execute(
            text(
                "UPDATE scim_tokens SET revoked_at = now() WHERE organization_id = :o "
                "AND id = :t AND revoked_at IS NULL RETURNING id"
            ),
            {"o": org, "t": token_id},
        )
    ).one_or_none()
    if revoked is None:
        return False
    await record_event(
        session,
        org,
        actor=actor,
        action="scim.token_revoked",
        target={"token_id": str(token_id)},
        before={"revoked": False},
        after={"revoked": True},
        reason=reason,
    )
    return True


async def scim_tokens(session: AsyncSession, org: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT id, created_at, revoked_at FROM scim_tokens WHERE organization_id = :o "
            "ORDER BY created_at"
        ),
        {"o": org},
    )
    return [dict(r._mapping) for r in rows]


async def set_admin(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    email: str,
    grant: bool,
    reason: str,
    actor: str,
) -> bool:
    """Give or take the administrator capability (no content access, SEC-15). False when
    the member already is (or is not) an administrator: nothing is written."""
    rows = (
        await session.execute(
            text(
                "SELECT m.id, m.status FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.organization_id = :o AND u.email = :e"
            ),
            {"o": org, "e": email},
        )
    ).all()
    if len(rows) != 1:
        # None, or two people with this address (a global and an owned user): ambiguous.
        raise OperatorRejected("no single member of the organization has that e-mail")
    membership_id, status = rows[0]
    if status == "removed":
        raise OperatorRejected("the member was removed")
    found = await access_snapshot(session, org, membership_id)
    assert found is not None  # noqa: S101 - selected above
    before = found[0]
    if before.is_admin == grant:
        return False
    await session.execute(
        text(
            "UPDATE memberships SET is_admin = :a, authz_updated_at = clock_timestamp() "
            "WHERE organization_id = :o AND id = :m"
        ),
        {"a": grant, "o": org, "m": membership_id},
    )
    await record_event(
        session,
        org,
        actor=actor,
        action=MEMBER_CHANGED,
        target={"membership_id": str(membership_id)},
        before=before.as_json(),
        after=replace(before, is_admin=grant).as_json(),
        reason=reason,
    )
    return True
