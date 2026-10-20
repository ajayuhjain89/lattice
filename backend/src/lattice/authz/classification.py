"""The classification of readable documents, for the AI egress policy (SEC-10).

Egress is not authorization: it decides which *already authorized* evidence may be
sent to a model provider (security-model §6). It needs each evidence document's
classification (``internal`` / ``confidential`` / ``restricted``), which is part of the
access record and so is read only here, inside ``lattice.authz``.

The function doubles as a tripwire: it answers only for documents the reader may read.
A missing or unreadable document aborts the request (``AuthorizationInvariantViolation``)
-- the caller only ever asks about evidence that already passed E3 and E4.
"""

import logging
import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz.access import load_document_access
from lattice.authz.context import AuthorizationContext
from lattice.authz.invariant import AuthorizationInvariantViolation
from lattice.authz.policy import readable
from lattice.log import log_event

logger = logging.getLogger(__name__)


async def readable_document_classifications(
    session: AsyncSession, ctx: AuthorizationContext, document_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    wanted = frozenset(document_ids)
    if not wanted:
        return {}
    loaded = await load_document_access(session, ctx.organization_id, wanted)
    violations = [d for d in wanted if (doc := loaded.get(d)) is None or not readable(ctx, doc)]
    if violations:
        log_event(
            logger,
            logging.ERROR,
            "security.invariant_violated",
            check="readable_document_classifications",
            organization_id=str(ctx.organization_id),
            membership_id=str(ctx.membership_id),
            policy_version=ctx.policy_version,
            checked=len(wanted),
            violations=len(violations),
            document_ids=sorted(str(v) for v in violations),
        )
        raise AuthorizationInvariantViolation()
    return {d: loaded[d].sensitivity for d in wanted}


async def current_readable_classifications(
    session: AsyncSession, ctx: AuthorizationContext, document_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str] | None:
    """The classifications of documents read again later in a request -- the call-time
    egress re-check before a model call (P12.5) -- or None when any of them is no longer
    readable by the asker (deleted, quarantined, access withdrawn meanwhile). That is an
    expected race, not an invariant violation: the caller simply sends nothing."""
    wanted = frozenset(document_ids)
    if not wanted:
        return {}
    loaded = await load_document_access(session, ctx.organization_id, wanted)
    if any((doc := loaded.get(d)) is None or not readable(ctx, doc) for d in wanted):
        return None
    return {d: loaded[d].sensitivity for d in wanted}
