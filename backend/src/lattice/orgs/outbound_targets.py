"""Operator-registered private outbound targets (phase-12-plan C4; D-15).

An organization administrator can never reach a private address by typing a URL: the
policy (``lattice.outbound``) refuses every non-public address. An organization-
controlled server on a private network, or a verification identity provider, becomes
reachable only when a platform operator registers its exact ``scheme://host:port`` for
that one organization and one purpose (``oidc`` or ``generation``):

    lattice outbound allow --org SLUG --url URL --purpose oidc|generation --reason R
    lattice outbound revoke --org SLUG --id ID --reason R
    lattice outbound list --org SLUG

Rows live in ``outbound_targets`` under tenant row-level security; no runtime role may
write them (an administrator cannot approve their own target), and a revoked row is
kept for the record. Each change is audited (``outbound.target_added`` /
``outbound.target_removed``). A registration is never a network range, a wildcard or a
port range, and never admits link-local, multicast or reserved addresses
(``outbound.classify``).
"""

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.audit import record_event
from lattice.outbound import (
    PURPOSES,
    OutboundPolicy,
    OutboundRefused,
    Resolver,
    Target,
    classify,
    parse_target,
    system_resolver,
)


class TargetRejected(Exception):
    """A refused registration; the message is safe to print."""


@dataclass(frozen=True)
class RegisteredTarget:
    id: uuid.UUID
    purpose: str
    target: Target
    created_at: datetime
    revoked_at: datetime | None


def registration_target(url: str) -> Target:
    """The exact origin to register for ``url`` (its path is ignored)."""
    try:
        target = parse_target(url, base=True)
    except OutboundRefused:
        raise TargetRejected(
            "the URL must be http(s)://host[:port] without credentials, query or fragment"
        ) from None
    try:
        literal = ipaddress.ip_address(target.host)
    except ValueError:
        literal = None
    if literal is not None and classify(literal) == "refused":
        raise TargetRejected(
            "link-local, multicast, reserved and documentation addresses are never registered"
        )
    return target


async def _live(
    session: AsyncSession, org: uuid.UUID, purpose: str | None = None
) -> list[RegisteredTarget]:
    rows = await session.execute(
        text(
            "SELECT id, purpose, scheme, host, port, created_at, revoked_at "
            "FROM outbound_targets WHERE organization_id = :o AND revoked_at IS NULL "
            "AND (CAST(:p AS text) IS NULL OR purpose = :p) ORDER BY created_at, id"
        ),
        {"o": org, "p": purpose},
    )
    return [
        RegisteredTarget(
            r.id, r.purpose, Target(r.scheme, r.host, r.port), r.created_at, r.revoked_at
        )
        for r in rows
    ]


async def list_targets(session: AsyncSession, org: uuid.UUID) -> list[RegisteredTarget]:
    rows = await session.execute(
        text(
            "SELECT id, purpose, scheme, host, port, created_at, revoked_at "
            "FROM outbound_targets WHERE organization_id = :o ORDER BY created_at, id"
        ),
        {"o": org},
    )
    return [
        RegisteredTarget(
            r.id, r.purpose, Target(r.scheme, r.host, r.port), r.created_at, r.revoked_at
        )
        for r in rows
    ]


async def policy_for(
    session: AsyncSession,
    org: uuid.UUID,
    purpose: str,
    *,
    resolver: Resolver = system_resolver,
) -> OutboundPolicy:
    """The outbound policy for one organization and purpose (its live registrations)."""
    if purpose not in PURPOSES:
        raise ValueError("unknown purpose")
    live = await _live(session, org, purpose)
    return OutboundPolicy(frozenset(t.target for t in live), resolver=resolver)


def access_class(policy: OutboundPolicy, url: str) -> str:
    """How the policy classes ``url`` by its shape and the registrations alone (no DNS):
    ``operator_approved`` (a registered target), ``public`` (https on 443 -- every address
    it resolves to is still checked when Lattice connects) or ``not_permitted``."""
    try:
        target = policy.check_url(url)
    except OutboundRefused:
        return "not_permitted"
    return "operator_approved" if policy.is_registered(target) else "public"


async def add_target(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    target: Target,
    purpose: str,
    reason: str,
    actor: str,
) -> tuple[RegisteredTarget, bool]:
    """Register ``target`` (idempotent: an identical live registration is returned with
    ``False`` and nothing is written)."""
    if purpose not in PURPOSES:
        raise TargetRejected("the purpose must be oidc or generation")
    for existing in await _live(session, org, purpose):
        if existing.target == target:
            return existing, False
    target_id: uuid.UUID = (
        await session.execute(
            text(
                "INSERT INTO outbound_targets (organization_id, purpose, scheme, host, port) "
                "VALUES (:o, :p, :s, :h, :port) RETURNING id"
            ),
            {"o": org, "p": purpose, "s": target.scheme, "h": target.host, "port": target.port},
        )
    ).scalar_one()
    await record_event(
        session,
        org,
        actor=actor,
        action="outbound.target_added",
        target={"target_id": str(target_id)},
        before=None,
        after={"purpose": purpose, "origin": target.origin},
        reason=reason,
    )
    found = [t for t in await _live(session, org, purpose) if t.id == target_id]
    return found[0], True


async def revoke_target(
    session: AsyncSession, org: uuid.UUID, target_id: uuid.UUID, *, reason: str, actor: str
) -> RegisteredTarget | None:
    """Revoke a live registration; None when there is none with that ID (in this
    organization)."""
    row = (
        await session.execute(
            text(
                "UPDATE outbound_targets SET revoked_at = now() WHERE organization_id = :o "
                "AND id = :t AND revoked_at IS NULL "
                "RETURNING id, purpose, scheme, host, port, created_at, revoked_at"
            ),
            {"o": org, "t": target_id},
        )
    ).one_or_none()
    if row is None:
        return None
    revoked = RegisteredTarget(
        row.id, row.purpose, Target(row.scheme, row.host, row.port), row.created_at, row.revoked_at
    )
    await record_event(
        session,
        org,
        actor=actor,
        action="outbound.target_removed",
        target={"target_id": str(target_id)},
        before={"purpose": revoked.purpose, "origin": revoked.target.origin},
        after=None,
        reason=reason,
    )
    return revoked
