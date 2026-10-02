"""The access audit trail (security-model §8; threat model T3): written in the same
transaction as every access change, read by administrators.

Actions written here: ``access.member_changed`` (target ``membership_id``; before/after
are ``AccessSnapshot``s), ``access.grant_created`` and ``access.grant_revoked`` (target
``membership_id``, ``document_id``, ``grant_id`` and the document's opaque ``reference``).
The Phase 6 provider and egress events (``provider.*``, ``credential.*``,
``egress.levels_changed``) are written by ``lattice.providers.configuration``.

Actors: ``member:<membership id>`` (the API), ``scim:<token id>`` (the identity
provider's provisioning, Phase 10: ``access.member_changed`` and
``access.identity_changed``), ``operator:seed``, ``operator:admin-grants`` and the CLI's
own operator actors.

Reading (``audit_page``): newest first, keyset pages, no totals. An event that targets a
document is listed only while the viewing administrator can read that document --
decided in SQL with their own predicate, for the page and for the keyset anchor alike,
so events about other documents change neither the result set nor its paging (no
count, timing or existence signal). The events stay in the database. Names of members,
accounts, departments and administrator-defined groups (the directory) are resolved at
read time, ``None`` once deleted.
"""

import json
import uuid
from typing import Any, Final

from sqlalchemy import ColumnElement, Text, cast, exists, null, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.directory import (
    DOCUMENTS,
    AccessSnapshot,
    actor_names,
    entity_names,
    readable_document_refs,
)
from lattice.access.models import (
    Actor,
    AuditDetail,
    AuditEventOut,
    AuditPage,
    NamedRef,
)
from lattice.access.preview import effects
from lattice.authz import AuthorizationContext, readable_documents_predicate
from lattice.tracing.models import AuditEvent

MAX_AUDIT_PAGE: Final = 100
MEMBER_CHANGED: Final = "access.member_changed"
# SCIM (Phase 10): the identity provider changed a member's user name, e-mail or name.
IDENTITY_CHANGED: Final = "access.identity_changed"
GRANT_CREATED: Final = "access.grant_created"
GRANT_REVOKED: Final = "access.grant_revoked"


async def record_event(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    actor: str,
    action: str,
    target: dict[str, Any],
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str,
) -> None:
    reason = " ".join(reason.split())[:500]
    if not reason:
        raise ValueError("an audit event needs a reason")
    session.add(
        AuditEvent(
            organization_id=organization_id,
            actor=actor,
            action=action,
            target=target,
            # SQL NULL, not a JSON null, for "no state" (as the provider events store it).
            before=null() if before is None else before,
            after=null() if after is None else after,
            reason=reason,
        )
    )
    await session.flush()


def _snapshot(value: dict[str, Any] | None) -> AccessSnapshot | None:
    if not value:
        return None
    try:
        return AccessSnapshot(
            status=str(value["status"]),
            clearance=str(value["clearance"]),
            account_scope=str(value["account_scope"]),
            accounts=frozenset(uuid.UUID(i) for i in value["accounts"]),
            departments=frozenset(uuid.UUID(i) for i in value["departments"]),
            groups=frozenset(uuid.UUID(i) for i in value["groups"]),
            is_admin=bool(value.get("is_admin", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _scalar(value: Any) -> str | None:
    """JSON spelling for everything but strings: ``true``, ``3``, ``["a"]``."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _details(target: dict[str, Any], before: Any, after: Any) -> list[AuditDetail]:
    """Provider and egress events: every field of the target, and every field that the
    change set, as strings. These events never hold a secret or a secret reference."""
    found = [AuditDetail(field=k, before=None, after=_scalar(v)) for k, v in sorted(target.items())]
    old = before if isinstance(before, dict) else {}
    new = after if isinstance(after, dict) else {}
    for key in sorted((set(old) | set(new)) - set(target)):
        if old.get(key) != new.get(key):
            found.append(
                AuditDetail(field=key, before=_scalar(old.get(key)), after=_scalar(new.get(key)))
            )
    return found


def _visible_to(viewer: AuthorizationContext) -> ColumnElement[bool]:
    """Events without a target document, or whose document ``viewer`` can read now."""
    document_id = AuditEvent.target["document_id"].astext
    readable = exists(
        select(1).where(
            DOCUMENTS.c.organization_id == viewer.organization_id,
            cast(DOCUMENTS.c.id, Text) == document_id,
            readable_documents_predicate(viewer, DOCUMENTS),
        )
    )
    return or_(document_id.is_(None), readable)


async def audit_page(
    session: AsyncSession, viewer: AuthorizationContext, *, limit: int, before: uuid.UUID | None
) -> AuditPage:
    org = viewer.organization_id
    visible = _visible_to(viewer)
    query = select(AuditEvent).where(AuditEvent.organization_id == org, visible)
    if before is not None:
        # An anchor the viewer may not see is treated as absent, like a missing one.
        anchor = (
            await session.execute(
                select(AuditEvent.created_at, AuditEvent.id).where(
                    AuditEvent.organization_id == org, AuditEvent.id == before, visible
                )
            )
        ).one_or_none()
        if anchor is not None:
            query = query.where(tuple_(AuditEvent.created_at, AuditEvent.id) < tuple_(*anchor))
    size = min(limit, MAX_AUDIT_PAGE)
    events = list(
        (
            await session.execute(
                query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(size + 1)
            )
        ).scalars()
    )
    more = len(events) > size
    events = events[:size]

    members: set[uuid.UUID] = set()
    documents: set[uuid.UUID] = set()
    entities: set[uuid.UUID] = set()
    for e in events:
        if not e.action.startswith("access."):
            continue
        if (m := _uuid(e.target.get("membership_id"))) is not None:
            members.add(m)
        if (d := _uuid(e.target.get("document_id"))) is not None:
            documents.add(d)
        for snap in (_snapshot(e.before), _snapshot(e.after)):
            if snap is not None:
                entities |= snap.accounts | snap.departments | snap.groups
    member_names = await actor_names(
        session, org, [*(e.actor for e in events), *(f"member:{m}" for m in members)]
    )
    refs = await readable_document_refs(session, viewer, documents)
    names = await entity_names(session, org, entities)

    items: list[AuditEventOut] = []
    for e in events:
        kind, _, tool = e.actor.partition(":")
        if kind == "member":
            actor = Actor(kind="member", name=member_names.get(e.actor), operator=None)
        elif kind == "scim":
            actor = Actor(kind="identity_provider", name=None, operator=None)
        else:
            actor = Actor(kind="operator", name=None, operator=tool or kind)
        member = None
        document = None
        found_effects = []
        details: list[AuditDetail] = []
        if e.action.startswith("access."):
            if (m := _uuid(e.target.get("membership_id"))) is not None:
                member = NamedRef(id=m, name=member_names.get(f"member:{m}"))
            if (d := _uuid(e.target.get("document_id"))) is not None:
                document = refs.get(d)  # readable: the query listed only such events
            if e.action == MEMBER_CHANGED:
                after = _snapshot(e.after)
                if after is not None:
                    found_effects = effects(_snapshot(e.before), after, names)
            elif e.action == IDENTITY_CHANGED:
                details = _details({}, e.before, e.after)
        else:
            details = _details(e.target, e.before, e.after)
        items.append(
            AuditEventOut(
                id=e.id,
                created_at=e.created_at,
                action=e.action,
                actor=actor,
                reason=e.reason,
                member=member,
                document=document,
                effects=found_effects,
                details=details,
            )
        )
    return AuditPage(items=items, next_before=events[-1].id if more and events else None)
