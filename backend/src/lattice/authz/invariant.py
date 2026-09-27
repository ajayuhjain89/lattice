"""The pre-generation authorization tripwire (enforcement point E4).

``assert_all_readable`` is **not** an authorization layer. The SQL predicate (E3) is
the boundary: unauthorized rows never leave PostgreSQL. This function runs
immediately before permitted evidence crosses into generation (or any other
sensitive boundary) and proves E3 held for this request, using the independent Python
reference policy over access columns re-read from the database.

If any document is unreadable -- or cannot be found in the member's organization at
all -- E3 was wrong. The request is aborted and a security event is logged. It never
filters: silently dropping the offending evidence and continuing would hide a broken
predicate.

Run it in the same REPEATABLE READ snapshot as context resolution and retrieval
(``Database.tenant_snapshot``), so a permission change committed mid-request cannot
cause a false alarm or a check against different permissions than the search used.
"""

import logging
import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz.access import load_document_access
from lattice.authz.context import AuthorizationContext
from lattice.authz.policy import readable
from lattice.errors import ErrorCode, LatticeError
from lattice.log import log_event

logger = logging.getLogger(__name__)


class AuthorizationInvariantViolation(LatticeError):
    """Evidence reached a sensitive boundary without being readable. A bug, never an
    expected outcome. The API renders the fixed ``security_invariant_violated`` message,
    which contains nothing about the documents involved."""

    def __init__(self) -> None:
        super().__init__(ErrorCode.SECURITY_INVARIANT_VIOLATED)


async def assert_all_readable(
    session: AsyncSession, ctx: AuthorizationContext, document_ids: Iterable[uuid.UUID]
) -> None:
    """Raise ``AuthorizationInvariantViolation`` unless every document is readable."""
    wanted = frozenset(document_ids)
    if not wanted:
        return
    loaded = await load_document_access(session, ctx.organization_id, wanted)
    violations = sorted(
        document_id
        for document_id in wanted
        if (doc := loaded.get(document_id)) is None or not readable(ctx, doc)
    )
    if violations:
        # IDs and counts only (SEC-8): no titles, no access attributes, no reasons.
        log_event(
            logger,
            logging.ERROR,
            "security.invariant_violated",
            check="assert_all_readable",
            organization_id=str(ctx.organization_id),
            membership_id=str(ctx.membership_id),
            policy_version=ctx.policy_version,
            checked=len(wanted),
            violations=len(violations),
            document_ids=[str(v) for v in violations],
        )
        raise AuthorizationInvariantViolation()
