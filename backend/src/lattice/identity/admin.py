"""Operator actions for identity (Phase 10, Phase 11 P11.3): the organization's OIDC
connection and its lifecycle, its SCIM tokens, the administrator capability, and the
incident controls that replace owner-role SQL (D-25, D-26). Run as ``lattice_owner`` by
the ``lattice sso``, ``scim``, ``member``, ``session`` and ``org`` commands, each with a
stated reason and an audit event in the same transaction:

* ``sso.connection_created`` / ``_disabled`` / ``_enabled`` / ``_updated`` (target: the
  connection). Disabling, and an identity-shape update (issuer or identifying claim),
  revoke the connection's live sessions in the same transaction -- under the
  connection's advisory lock, so a sign-in racing it cannot leave a session behind
  (``lattice.identity.sessions``); the count is recorded;
* ``scim.token_created`` / ``scim.token_revoked`` (target: the token's ID);
* ``access.member_changed`` for the administrator capability and for an operator's
  suspension or reinstatement (before/after snapshots, as every access change);
  suspension revokes the member's sessions;
* ``session.revoked`` (target: one membership or the whole organization; the count);
* ``organization.suspended`` / ``organization.reactivated``: suspension stops sign-in,
  SCIM and the worker for the organization and revokes every session.

Events never hold a secret or a secret reference: a connection's client secret is
recorded as configured or not (and whether a rotation changed the reference). The raw
SCIM token is returned once, to print.
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
from lattice.identity.sessions import CONNECTION_LOCK_SQL
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


async def _provisioned_members_of_disabled(session: AsyncSession, org: uuid.UUID) -> int:
    """Members (not removed) whose directory identity belongs to a disabled connection."""
    count: int = (
        await session.execute(
            text(
                "SELECT count(*) FROM directory_identities d "
                "JOIN oidc_connections c ON c.organization_id = d.organization_id "
                "AND c.id = d.connection_id AND c.status = 'disabled' "
                "JOIN memberships m ON m.organization_id = d.organization_id "
                "AND m.id = d.membership_id AND m.status <> 'removed' "
                "WHERE d.organization_id = :o"
            ),
            {"o": org},
        )
    ).scalar_one()
    return count


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
    new_identity_namespace: bool = False,
) -> ConnectionView:
    if await active_connection(session, org) is not None:
        raise OperatorRejected(
            "the organization already has an active connection; disable it first"
        )
    stranded = await _provisioned_members_of_disabled(session, org)
    if stranded and not new_identity_namespace:
        raise OperatorRejected(
            f"a disabled connection still has {stranded} provisioned member(s); a new "
            "connection would not map them (its directory identities start empty). Use "
            "`lattice sso enable` / `lattice sso update` instead, or confirm a new "
            "identity namespace with --new-identity-namespace"
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
            **({"new_identity_namespace": True} if stranded else {}),
        },
        reason=reason,
    )
    found = await active_connection(session, org)
    assert found is not None  # noqa: S101 - inserted above
    return found


async def _revoke_connection_sessions(
    session: AsyncSession, org: uuid.UUID, connection_id: uuid.UUID
) -> int:
    """Revoke the connection's live sessions. The caller holds the connection lock."""
    revoked = await session.execute(
        text(
            "UPDATE sessions SET revoked_at = now() WHERE organization_id = :o "
            "AND oidc_connection_id = :c AND revoked_at IS NULL RETURNING id"
        ),
        {"o": org, "c": connection_id},
    )
    return len(revoked.all())


async def disable_connection(
    session: AsyncSession, org: uuid.UUID, *, reason: str, actor: str
) -> tuple[ConnectionView, int]:
    """Disable the active connection and revoke every session it issued (D-25). Returns
    the connection and the number of sessions revoked."""
    found = await active_connection(session, org)
    if found is None:
        raise OperatorRejected("the organization has no active connection")
    await session.execute(CONNECTION_LOCK_SQL, {"c": found.id})
    await session.execute(
        text("UPDATE oidc_connections SET status = 'disabled' WHERE id = :c"), {"c": found.id}
    )
    revoked = await _revoke_connection_sessions(session, org, found.id)
    await record_event(
        session,
        org,
        actor=actor,
        action="sso.connection_disabled",
        target={"connection_id": str(found.id)},
        before={"status": "active"},
        after={"status": "disabled", "sessions_revoked": revoked},
        reason=reason,
    )
    return replace(found, status="disabled"), revoked


_CONNECTIONS_SQL = text(
    "SELECT id, login_key, issuer, client_id, subject_claim, "
    "client_secret_ref IS NOT NULL AS confidential, status FROM oidc_connections "
    "WHERE organization_id = :o ORDER BY created_at"
)


async def connections(session: AsyncSession, org: uuid.UUID) -> list[ConnectionView]:
    rows = await session.execute(_CONNECTIONS_SQL, {"o": org})
    return [ConnectionView(**r._mapping) for r in rows]


async def _pick(
    session: AsyncSession, org: uuid.UUID, connection_id: uuid.UUID | None, *, status: str | None
) -> ConnectionView:
    """The connection named by ID, or the only one with ``status`` (refused if several)."""
    found = await connections(session, org)
    if connection_id is not None:
        chosen = [c for c in found if c.id == connection_id]
        if not chosen:
            raise OperatorRejected("the organization has no connection with that ID")
        return chosen[0]
    matching = [c for c in found if status is None or c.status == status]
    if not matching:
        kind = f"{status} " if status else ""
        raise OperatorRejected(f"the organization has no {kind}connection")
    if len(matching) > 1:
        raise OperatorRejected(
            "the organization has several such connections: name one with --connection-id "
            "(lattice sso list)"
        )
    return matching[0]


async def enable_connection(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    connection_id: uuid.UUID | None,
    reason: str,
    actor: str,
) -> ConnectionView:
    """Re-activate a disabled connection: the same ID, login key and directory
    identities, so the members it provisioned sign in again. Sessions revoked by the
    disable stay revoked; SCIM tokens not revoked meanwhile work again."""
    if await active_connection(session, org) is not None:
        raise OperatorRejected("the organization already has an active connection")
    found = await _pick(session, org, connection_id, status="disabled")
    if found.status != "disabled":
        raise OperatorRejected("that connection is not disabled")
    await session.execute(
        text("UPDATE oidc_connections SET status = 'active' WHERE id = :c"), {"c": found.id}
    )
    await record_event(
        session,
        org,
        actor=actor,
        action="sso.connection_enabled",
        target={"connection_id": str(found.id)},
        before={"status": "disabled"},
        after={"status": "active"},
        reason=reason,
    )
    return replace(found, status="active")


_UNSET = object()


async def update_connection(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    connection_id: uuid.UUID | None,
    client_id: str | None = None,
    secret_ref: object = _UNSET,
    issuer: str | None = None,
    subject_claim: str | None = None,
    subjects_unchanged: bool = False,
    reason: str,
    actor: str,
) -> tuple[ConnectionView, int]:
    """Change a connection in place (D-25). ``secret_ref``: a new ``env:`` reference, or
    None for a public (PKCE-only) client. The client ID and the secret reference change
    nothing for sessions. The issuer and the identifying claim define whose subjects the
    directory identities hold: changing either is allowed only with
    ``subjects_unchanged`` (the operator asserts every person keeps the same subject
    value, e.g. an issuer URL rename) and revokes the connection's sessions. A provider
    whose subjects differ needs an identity mapping (Phase 12), not this."""
    wanted = "active" if connection_id is None else None
    found = await _pick(session, org, connection_id, status=wanted)
    current = (
        await session.execute(
            text(
                "SELECT issuer, client_id, client_secret_ref, subject_claim "
                "FROM oidc_connections WHERE id = :c"
            ),
            {"c": found.id},
        )
    ).one()
    changes: dict[str, Any] = {}
    if client_id is not None and client_id != current.client_id:
        changes["client_id"] = client_id
    if secret_ref is not _UNSET and secret_ref != current.client_secret_ref:
        if secret_ref is not None and not (
            isinstance(secret_ref, str) and secret_ref.startswith("env:LATTICE_OIDC_SECRET_")
        ):
            raise OperatorRejected(
                "the client secret must be a reference env:LATTICE_OIDC_SECRET_<NAME>"
            )
        changes["client_secret_ref"] = secret_ref
    if issuer is not None and issuer != current.issuer:
        changes["issuer"] = _check_issuer(issuer)
    if subject_claim is not None and subject_claim != current.subject_claim:
        changes["subject_claim"] = subject_claim
    if not changes:
        raise OperatorRejected("nothing to change")
    identity_shape = bool({"issuer", "subject_claim"} & set(changes))
    if identity_shape and not subjects_unchanged:
        raise OperatorRejected(
            "changing the issuer or the identifying claim changes whose subjects the "
            "provisioned identities hold: confirm with --subjects-unchanged that every "
            "person keeps the same subject value (their sessions are revoked); a provider "
            "with different subjects needs an identity mapping (Phase 12)"
        )
    if identity_shape:
        await session.execute(CONNECTION_LOCK_SQL, {"c": found.id})
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    try:
        await session.execute(
            text(f"UPDATE oidc_connections SET {assignments} WHERE id = :c"),  # noqa: S608 - fixed column names
            {**changes, "c": found.id},
        )
    except DBAPIError as exc:
        raise OperatorRejected(
            "the change was refused by the database (issuer, client ID, secret reference or "
            "claim name has an invalid form)"
        ) from exc
    revoked = await _revoke_connection_sessions(session, org, found.id) if identity_shape else 0

    def shown(values: Any) -> dict[str, Any]:
        return {
            "issuer": values["issuer"],
            "client_id": values["client_id"],
            "subject_claim": values["subject_claim"],
            "client_secret": "reference" if values["client_secret_ref"] else "none",
        }

    before = shown(current._mapping)
    after = shown({**current._mapping, **changes})
    if "client_secret_ref" in changes and before["client_secret"] == after["client_secret"]:
        after["client_secret"] = "reference (rotated)"  # noqa: S105 - a label, not a secret
    if identity_shape:
        after["sessions_revoked"] = revoked
    await record_event(
        session,
        org,
        actor=actor,
        action="sso.connection_updated",
        target={"connection_id": str(found.id)},
        before=before,
        after=after,
        reason=reason,
    )
    view = await _pick(session, org, found.id, status=None)
    return view, revoked


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


# --- Incident controls replacing owner-role SQL (P11.3, D-26) ------------------------------


@dataclass(frozen=True)
class MemberRef:
    membership_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    user_status: str
    provisioned: bool


async def _member(session: AsyncSession, org: uuid.UUID, email: str) -> MemberRef:
    rows = (
        await session.execute(
            text(
                "SELECT m.id, m.user_id, m.status, u.status AS user_status, EXISTS ("
                "SELECT 1 FROM directory_identities d WHERE d.organization_id = m.organization_id "
                "AND d.membership_id = m.id) AS provisioned "
                "FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.organization_id = :o AND u.email = :e"
            ),
            {"o": org, "e": email},
        )
    ).all()
    if len(rows) != 1:
        raise OperatorRejected("no single member of the organization has that e-mail")
    row = rows[0]
    return MemberRef(row.id, row.user_id, row.status, row.user_status, row.provisioned)


async def _revoke_member_sessions(session: AsyncSession, org: uuid.UUID, user_id: uuid.UUID) -> int:
    revoked = await session.execute(
        text(
            "UPDATE sessions SET revoked_at = now() WHERE organization_id = :o AND user_id = :u "
            "AND revoked_at IS NULL RETURNING id"
        ),
        {"o": org, "u": user_id},
    )
    return len(revoked.all())


async def _set_member_status(
    session: AsyncSession,
    org: uuid.UUID,
    member: MemberRef,
    status: str,
    *,
    reason: str,
    actor: str,
) -> None:
    found = await access_snapshot(session, org, member.membership_id)
    assert found is not None  # noqa: S101 - selected above
    before = found[0]
    await session.execute(
        text(
            "UPDATE memberships SET status = :s, authz_updated_at = clock_timestamp() "
            "WHERE organization_id = :o AND id = :m"
        ),
        {"s": status, "o": org, "m": member.membership_id},
    )
    await record_event(
        session,
        org,
        actor=actor,
        action=MEMBER_CHANGED,
        target={"membership_id": str(member.membership_id)},
        before=before.as_json(),
        after=replace(before, status=status).as_json(),
        reason=reason,
    )


async def suspend_member(
    session: AsyncSession, org: uuid.UUID, *, email: str, reason: str, actor: str
) -> tuple[MemberRef, int] | None:
    """Suspend a member now and revoke their sessions (the urgent-revocation procedure).
    None when already suspended (nothing written). For a provisioned member the identity
    provider stays authoritative for lifecycle (D-19): suspend them there too, or its
    next update may reactivate them."""
    member = await _member(session, org, email)
    if member.status == "removed":
        raise OperatorRejected("the member was removed")
    if member.status == "suspended":
        return None
    await _set_member_status(session, org, member, "suspended", reason=reason, actor=actor)
    return member, await _revoke_member_sessions(session, org, member.user_id)


async def reinstate_member(
    session: AsyncSession, org: uuid.UUID, *, email: str, reason: str, actor: str
) -> MemberRef | None:
    """Make a suspended or removed membership active again -- for a removed one, the
    deliberate operator act that D-20 leaves to operators (SCIM removal is terminal for
    the identity provider). The member keeps the access they had; previous sessions stay
    revoked. None when already active."""
    member = await _member(session, org, email)
    if member.status == "active":
        return None
    if member.user_status != "active":
        raise OperatorRejected("the person's user account is not active")
    await _set_member_status(session, org, member, "active", reason=reason, actor=actor)
    return member


async def revoke_sessions(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    email: str | None,
    reason: str,
    actor: str,
) -> int:
    """End one member's sessions (``email``) or every session of the organization
    (None). Recorded even when nothing was live."""
    if email is not None:
        member = await _member(session, org, email)
        revoked = await _revoke_member_sessions(session, org, member.user_id)
        target = {"membership_id": str(member.membership_id)}
    else:
        rows = await session.execute(
            text(
                "UPDATE sessions SET revoked_at = now() WHERE organization_id = :o "
                "AND revoked_at IS NULL RETURNING id"
            ),
            {"o": org},
        )
        revoked = len(rows.all())
        target = {"scope": "organization"}
    await record_event(
        session,
        org,
        actor=actor,
        action="session.revoked",
        target=target,
        before=None,
        after={"sessions_revoked": revoked},
        reason=reason,
    )
    return revoked


async def set_organization_status(
    session: AsyncSession, org: uuid.UUID, *, suspend: bool, reason: str, actor: str
) -> int | None:
    """Suspend (emergency stop) or reactivate an organization. Suspension: sign-in, SCIM
    and the worker stop for it, every request of its sessions fails, and every session is
    revoked (so reactivation never revives one). Returns the sessions revoked, or None
    when the organization already had that status."""
    current: str = (
        await session.execute(text("SELECT status FROM organizations WHERE id = :o"), {"o": org})
    ).scalar_one()
    wanted = "suspended" if suspend else "active"
    if current == wanted:
        return None
    if current not in ("active", "suspended"):
        raise OperatorRejected(f"the organization is {current}")
    await session.execute(
        text("UPDATE organizations SET status = :s WHERE id = :o"), {"s": wanted, "o": org}
    )
    revoked = 0
    if suspend:
        rows = await session.execute(
            text(
                "UPDATE sessions SET revoked_at = now() WHERE organization_id = :o "
                "AND revoked_at IS NULL RETURNING id"
            ),
            {"o": org},
        )
        revoked = len(rows.all())
    await record_event(
        session,
        org,
        actor=actor,
        action="organization.suspended" if suspend else "organization.reactivated",
        target={"organization_id": str(org)},
        before={"status": current},
        after={"status": wanted, **({"sessions_revoked": revoked} if suspend else {})},
        reason=reason,
    )
    return revoked
