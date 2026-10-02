"""Why a member can or cannot read one document -- for an administrator who can read
that document themselves (``authz.explain_for_reviewer``; security-model §10). For any
other document, or a member of another organization, there is no answer at all (the
route's 404), so this is never an oracle over documents the administrator cannot read."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.directory import (
    actor_names,
    entity_names,
    group_views,
    readable_document_refs,
)
from lattice.access.models import DocumentExplanation, GrantVia
from lattice.authz import (
    AuthorizationContext,
    explain_for_reviewer,
    load_document_access,
    resolve_member_context,
)


async def explain_member_document(
    session: AsyncSession,
    viewer: AuthorizationContext,
    membership_id: uuid.UUID,
    document_id: uuid.UUID,
) -> DocumentExplanation | None:
    org = viewer.organization_id
    subject = await resolve_member_context(
        session, organization_id=org, membership_id=membership_id
    )
    if subject is None:
        return None
    doc = (await load_document_access(session, org, [document_id])).get(document_id)
    explanation = explain_for_reviewer(viewer, subject, doc)
    if explanation is None:
        return None
    ref = (await readable_document_refs(session, viewer, [document_id])).get(document_id)
    if ref is None:  # pragma: no cover - explain_for_reviewer already required readability
        return None
    grants = explanation.matched_grants
    members = await actor_names(
        session,
        org,
        [f"member:{g.principal_id}" for g in grants if g.principal_type == "membership"],
    )
    departments = await entity_names(
        session, org, [g.principal_id for g in grants if g.principal_type == "department"]
    )
    groups = await group_views(
        session, viewer, [g.principal_id for g in grants if g.principal_type == "group"]
    )
    via: list[GrantVia] = []
    for g in grants:
        if g.principal_type == "membership":
            via.append(
                GrantVia(
                    principal_type="membership",
                    name=members.get(f"member:{g.principal_id}"),
                )
            )
        elif g.principal_type == "department":
            via.append(
                GrantVia(
                    principal_type="department",
                    name=departments.get(g.principal_id),
                )
            )
        else:
            # A private group the viewer is not in: acknowledged, never identified.
            group = groups.get(g.principal_id)
            via.append(GrantVia(principal_type="group", name=group.name if group else None))
    return DocumentExplanation(
        document=ref,
        readable=explanation.readable,
        path=explanation.path,
        reasons=[str(r) for r in explanation.reasons],
        via=via,
    )
