"""Explanations: accurate for trusted callers, and never an existence oracle.

``explain`` (internal) names every relevant policy clause. ``explain_own_access`` and
``explain_for_reviewer`` return ``None`` -- the same value, with the same type -- for a
missing document and for one the caller cannot read (SEC-5).
"""

import logging
import uuid
from datetime import UTC, datetime

import pytest

from lattice.authz import (
    POLICY_VERSION,
    AccessReason,
    AuthorizationContext,
    DocumentAccess,
    explain,
    explain_for_reviewer,
    explain_own_access,
    load_document_access,
)
from lattice.db.engine import Database

from .conftest import Halden, context_for

R = AccessReason


async def _doc(db: Database, world: Halden, name: str) -> DocumentAccess:
    document_id = world.documents[name]
    org = world.veridian if name.startswith("veridian") else world.org
    async with db.tenant_snapshot(org) as s:
        return (await load_document_access(s, org, [document_id]))[document_id]


CASES = [
    # (person, document, readable, path, reasons)
    (
        "maya",
        "acme-success-plan",
        True,
        "policy",
        (R.CLEARANCE_COVERS_SENSITIVITY, R.ACCOUNT_ASSIGNED, R.NO_DEPARTMENT_RESTRICTION),
    ),
    (
        "priya",
        "acme-success-plan",
        True,
        "policy",
        (R.CLEARANCE_COVERS_SENSITIVITY, R.ACCOUNT_SCOPE_ALL, R.NO_DEPARTMENT_RESTRICTION),
    ),
    (
        "tom",
        "product-roadmap",
        True,
        "policy",
        (R.CLEARANCE_COVERS_SENSITIVITY, R.NOT_ACCOUNT_SPECIFIC, R.DEPARTMENT_MEMBER),
    ),
    ("arjun", "compensation-review", True, "grant", (R.GRANT_TO_MEMBER,)),
    ("priya", "incident-postmortem", True, "grant", (R.GRANT_TO_DEPARTMENT,)),
    ("grace", "board-mna-memo", True, "grant", (R.GRANT_TO_GROUP,)),
    # Every failing clause is reported, not only the first.
    (
        "maya",
        "acme-exec-escalation",
        False,
        "none",
        (R.CLEARANCE_INSUFFICIENT, R.ACCOUNT_ASSIGNED, R.DEPARTMENT_NOT_MATCHED),
    ),
    (
        "lena",
        "kestrel-renewal",
        False,
        "none",
        (R.CLEARANCE_COVERS_SENSITIVITY, R.ACCOUNT_NOT_IN_SCOPE, R.NO_DEPARTMENT_RESTRICTION),
    ),
    ("tom", "board-mna-memo", False, "none", (R.RESTRICTED_WITHOUT_GRANT,)),
    ("daniel", "acme-private-channel", False, "none", (R.GRANTS_ONLY_WITHOUT_GRANT,)),
    ("grace", "deleted-handbook", False, "none", (R.DOCUMENT_INACTIVE,)),
    ("grace", "veridian-handbook", False, "none", (R.DIFFERENT_ORGANIZATION,)),
]


@pytest.mark.parametrize(
    ("person", "document", "is_readable", "path", "reasons"),
    CASES,
    ids=[f"{p}-{d}" for p, d, *_ in CASES],
)
async def test_internal_explanation_names_the_policy_reasons(
    app_db: Database,
    halden: Halden,
    person: str,
    document: str,
    is_readable: bool,
    path: str,
    reasons: tuple[AccessReason, ...],
) -> None:
    ctx = await context_for(app_db, halden.org, halden.members[person])
    explanation = explain(ctx, await _doc(app_db, halden, document))
    assert explanation.readable is is_readable
    assert explanation.path == path
    assert explanation.reasons == reasons
    assert explanation.policy_version == POLICY_VERSION


async def test_own_access_explains_only_what_grants_access(
    app_db: Database, halden: Halden
) -> None:
    maya = await context_for(app_db, halden.org, halden.members["maya"])
    own = explain_own_access(maya, await _doc(app_db, halden, "acme-private-channel"))
    assert own is not None
    assert own.reasons == (R.GRANT_TO_GROUP,)
    assert own.policy_version == POLICY_VERSION
    assert not hasattr(own, "matched_grants")  # no principal IDs, no group names


@pytest.mark.parametrize(
    ("person", "document"),
    [
        ("maya", "acme-exec-escalation"),  # same org, unreadable: clearance + department
        ("maya", "kestrel-renewal"),  # unreadable: account scope
        ("maya", "board-mna-memo"),  # restricted
        ("daniel", "acme-private-channel"),  # grants-only, not a channel member
        ("maya", "deleted-handbook"),  # deleted
        ("maya", "veridian-handbook"),  # another tenant
    ],
)
async def test_own_access_is_not_an_existence_oracle(
    app_db: Database, halden: Halden, person: str, document: str
) -> None:
    ctx = await context_for(app_db, halden.org, halden.members[person])
    unreadable = explain_own_access(ctx, await _doc(app_db, halden, document))
    missing = explain_own_access(ctx, None)
    assert unreadable is None
    assert missing is None


async def test_reviewer_explanations_are_gated_on_the_reviewer_s_own_access(
    app_db: Database, halden: Halden
) -> None:
    grace = await context_for(app_db, halden.org, halden.members["grace"])
    maya = await context_for(app_db, halden.org, halden.members["maya"])
    escalation = await _doc(app_db, halden, "acme-exec-escalation")
    memo = await _doc(app_db, halden, "compensation-review")

    # Grace can read the escalation, so she may learn why Maya cannot.
    why_not = explain_for_reviewer(grace, maya, escalation)
    assert why_not is not None
    assert not why_not.readable
    assert R.CLEARANCE_INSUFFICIENT in why_not.reasons
    # Grace cannot read the compensation review: no explanation, as if it did not exist.
    assert explain_for_reviewer(grace, maya, memo) is None
    assert explain_for_reviewer(grace, maya, None) is None
    # The reviewee cannot turn it around either.
    assert explain_for_reviewer(maya, grace, escalation) is None


async def test_reviewer_explanations_do_not_cross_organizations(
    app_db: Database, halden: Halden
) -> None:
    grace = await context_for(app_db, halden.org, halden.members["grace"])
    foreign = grace.model_copy(update={"organization_id": halden.veridian})
    handbook = await _doc(app_db, halden, "company-handbook")
    assert explain_for_reviewer(grace, foreign, handbook) is None


def test_explaining_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    ctx = AuthorizationContext(
        organization_id=uuid.UUID(int=1),
        membership_id=uuid.UUID(int=2),
        membership_status="active",
        clearance="internal",
        account_scope="all",
        assigned_account_ids=frozenset(),
        department_ids=frozenset(),
        group_ids=frozenset(),
        authz_updated_at=datetime(2026, 9, 27, tzinfo=UTC),
        policy_version=POLICY_VERSION,
    )
    doc = DocumentAccess(
        id=uuid.UUID(int=3),
        organization_id=uuid.UUID(int=1),
        status="active",
        sensitivity="restricted",
        access_mode="grants_only",
        account_id=None,
        department_ids=frozenset(),
    )
    with caplog.at_level(logging.DEBUG):
        explain(ctx, doc)
        explain_own_access(ctx, doc)
        explain_for_reviewer(ctx, ctx, doc)
    assert caplog.records == []
