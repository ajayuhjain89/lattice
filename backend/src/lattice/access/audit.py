"""The access audit trail (security-model §8; threat model T3): written in the same
transaction as every access change, read by administrators.

Actions written here: ``access.member_changed`` (target ``membership_id``; before/after
are ``AccessSnapshot``s; a member added by hand also records, in ``after.identity``, the
identifier and e-mail they were bound to), ``access.grant_created`` and
``access.grant_revoked`` (target ``membership_id``, ``document_id``, ``grant_id`` and the
document's opaque ``reference``).
The Phase 6 provider and egress events (``provider.*``, ``credential.*``,
``egress.levels_changed``) are written by ``lattice.providers.configuration``; the
account events (``account.created`` / ``updated`` / ``archived`` / ``reactivated``, target
``account_id``; ``account.mapping_changed``, target ``source_id``, ``ref_kind``,
``external_ref``, before/after ``account_id``) by ``lattice.accounts`` (P12.3), shown with
the account and source names resolved at read time; the source events (``source.created``,
``updated``, ``paused``, ``resumed``, ``disconnected``, ``import_accepted``,
``import_rejected``, ``sync_requested``; target ``source_id`` and, for imports,
``import_id``) by ``lattice.sources`` and ``lattice.jobs.queue`` (P12.6), with the source's
name resolved at read time.

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
    SOURCES,
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


_REF_KINDS: Final = {"ticketing_organization": "Ticketing organization", "chat_channel": "Channel"}
_ACCOUNT_FIELDS: Final = ("key", "name", "aliases", "status", "lifecycle")


async def _source_names(
    session: AsyncSession, organization_id: uuid.UUID, ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(
        select(SOURCES.c.id, SOURCES.c.name).where(
            SOURCES.c.organization_id == organization_id, SOURCES.c.id.in_(sorted(ids))
        )
    )
    return {r.id: r.name for r in rows}


def _account_value(value: Any) -> str | None:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "none"
    return _scalar(value)


def _account_details(
    e: AuditEvent, names: dict[uuid.UUID, str], sources: dict[uuid.UUID, str]
) -> list[AuditDetail]:
    """Account events with names, resolved now (``None`` once a source is gone)."""
    before = e.before if isinstance(e.before, dict) else {}
    after = e.after if isinstance(e.after, dict) else {}

    def account(part: dict[str, Any]) -> str | None:
        found = _uuid(part.get("account_id"))
        return None if found is None else names.get(found)

    if e.action == "account.mapping_changed":
        source = _uuid(e.target.get("source_id"))
        kind = _REF_KINDS.get(str(e.target.get("ref_kind")), "Reference")
        return [
            AuditDetail(field="source", before=None, after=sources.get(source) if source else None),
            AuditDetail(
                field="reference", before=None, after=f"{kind} {e.target.get('external_ref')}"
            ),
            AuditDetail(
                field="account",
                before=account(before) if before else None,
                after=account(after) if after else "Not mapped",
            ),
        ]
    found = [AuditDetail(field="account", before=None, after=account(e.target))]
    for key in _ACCOUNT_FIELDS:
        old, new = before.get(key), after.get(key)
        if key in after and (not before or old != new):
            found.append(
                AuditDetail(
                    field=key,
                    before=_account_value(old) if before else None,
                    after=_account_value(new),
                )
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


REQUEST_REFUSED: Final = "scim.request_refused"


async def audit_page(
    session: AsyncSession,
    viewer: AuthorizationContext,
    *,
    limit: int,
    before: uuid.UUID | None,
    actor_kind: str | None = None,
) -> AuditPage:
    """``actor_kind="identity_provider"``: only the identity provider's events (the SCIM
    provisioning activity, P12.4)."""
    org = viewer.organization_id
    visible = _visible_to(viewer)
    if actor_kind == "identity_provider":
        visible = visible & AuditEvent.actor.like("scim:%")
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
        if e.action == REQUEST_REFUSED and (m := _uuid(e.target.get("membership_id"))):
            members.add(m)
        if not e.action.startswith("access."):
            continue
        if (m := _uuid(e.target.get("membership_id"))) is not None:
            members.add(m)
        if (d := _uuid(e.target.get("document_id"))) is not None:
            documents.add(d)
        for snap in (_snapshot(e.before), _snapshot(e.after)):
            if snap is not None:
                entities |= snap.accounts | snap.departments | snap.groups
    accounts: set[uuid.UUID] = set()
    sources: set[uuid.UUID] = set()
    for e in events:
        if e.action.startswith("source.") and (src := _uuid(e.target.get("source_id"))):
            sources.add(src)
        if e.action.startswith("account."):
            for part in (e.target, e.before, e.after):
                if isinstance(part, dict) and (a := _uuid(part.get("account_id"))) is not None:
                    accounts.add(a)
            if (src := _uuid(e.target.get("source_id"))) is not None:
                sources.add(src)
    entities |= accounts
    source_names = await _source_names(session, org, sources)
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
                identity = e.after.get("identity") if isinstance(e.after, dict) else None
                if e.before is None and isinstance(identity, dict):
                    # A member added by hand (P12.2): the identifier they were bound to.
                    details = [
                        AuditDetail(field=k, before=None, after=_scalar(identity.get(v)))
                        for k, v in (("sign_in_identifier", "identifier"), ("email", "email"))
                    ]
                if after is not None:
                    found_effects = effects(
                        _snapshot(e.before), after, names, created=kind == "member"
                    )
            elif e.action == IDENTITY_CHANGED:
                details = _details({}, e.before, e.after)
        elif e.action.startswith("account."):
            details = _account_details(e, names, source_names)
        elif e.action.startswith("source."):
            # Source events (P12.6): the source by name (resolved now; None once purged),
            # then what changed. Counts, codes, sizes and IDs only -- never content.
            source = _uuid(e.target.get("source_id"))
            shown = {k: v for k, v in e.target.items() if k != "source_id"}
            details = [
                AuditDetail(
                    field="source",
                    before=None,
                    after=source_names.get(source) if source else None,
                ),
                *_details(shown, e.before, e.after),
            ]
        elif e.action == REQUEST_REFUSED:
            # The member the provider's request collided with, by name; the token and
            # operation as details; never a value from the request (none is stored).
            if (m := _uuid(e.target.get("membership_id"))) is not None:
                member = NamedRef(id=m, name=member_names.get(f"member:{m}"))
            shown = {k: v for k, v in e.target.items() if k != "membership_id"}
            details = _details(shown, e.before, e.after)
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
