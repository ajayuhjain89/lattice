"""Persona matrix for the Halden design customer (docs/authorization-model.md §4).

Each persona's readable set is written out in full, so the matrix documents denials as
explicitly as grants. Every set is computed twice, as the runtime role -- through the
SQL predicate and through the reference policy -- and the two must agree
(``readable_names``).
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from lattice.authz import (
    AccountRef,
    account_visible,
    readable_documents_predicate,
    visible_accounts_predicate,
)
from lattice.authz.sql import ACCOUNTS, DOCUMENTS
from lattice.db.engine import Database

from .conftest import Halden, context_for, readable_names

EVERYONE = {"company-handbook"}

EXPECTED: dict[str, set[str]] = {
    # Account Manager: assigned Acme + Northpeak; Sales; internal; #acct-acme channel.
    "maya": EVERYONE | {"acme-success-plan", "acme-private-channel", "kestrel-shared-with-maya"},
    # Account Manager: assigned Brightwater + Kestrel; no private channels.
    "daniel": EVERYONE | {"kestrel-renewal", "kestrel-shared-with-maya"},
    # Support: all accounts; Support department; department grant on the postmortem.
    "priya": EVERYONE
    | {
        "acme-success-plan",
        "kestrel-renewal",
        "kestrel-shared-with-maya",
        "support-runbook",
        "incident-postmortem",
    },
    # Product: all accounts, internal, Product department.
    "tom": EVERYONE
    | {"acme-success-plan", "kestrel-renewal", "kestrel-shared-with-maya", "product-roadmap"},
    # Engineering: like Product, plus a personal grant on a grants-only channel.
    "sofia": EVERYONE
    | {
        "acme-success-plan",
        "kestrel-renewal",
        "kestrel-shared-with-maya",
        "product-roadmap",
        "eng-private-channel",
    },
    # Leadership (VP CS): confidential; Leadership + CS; Exec staff and #acct-acme groups.
    "grace": EVERYONE
    | {
        "acme-success-plan",
        "acme-exec-escalation",
        "kestrel-renewal",
        "kestrel-shared-with-maya",
        "pricing-strategy",
        "board-mna-memo",
        "acme-private-channel",
    },
    # Leadership (CEO): confidential; Exec staff; personal grant on compensation review.
    "arjun": EVERYONE
    | {
        "acme-success-plan",
        "acme-exec-escalation",
        "kestrel-renewal",
        "kestrel-shared-with-maya",
        "pricing-strategy",
        "board-mna-memo",
        "compensation-review",
    },
    # Administrator: IT department, assigned scope with no accounts. is_admin=true reads
    # nothing extra.
    "lena": EVERYONE | {"it-admin-runbook"},
}

# Why each notable denial happens (asserted below, one clause each).
NOTABLE_DENIALS = {
    ("maya", "acme-exec-escalation"): "confidential + Leadership-only, Maya is internal/Sales",
    ("maya", "kestrel-renewal"): "account not assigned",
    ("daniel", "acme-private-channel"): "grants-only; Daniel is not in #acct-acme",
    ("priya", "pricing-strategy"): "confidential; Support is internal",
    ("priya", "product-roadmap"): "department restriction",
    ("sofia", "incident-postmortem"): "confidential; the grant goes to Support, not Eng",
    ("tom", "board-mna-memo"): "restricted: grants only",
    ("grace", "compensation-review"): "restricted, granted to Arjun alone",
    ("grace", "support-runbook"): "confidential clearance does not bypass departments",
    ("arjun", "acme-private-channel"): "the CEO is not a member of the private channel",
    ("lena", "board-mna-memo"): "administrator capability is not content access",
    ("lena", "acme-success-plan"): "admin has no account assignments",
}
NOBODY = {"deleted-handbook", "disbanded-team-notes", "veridian-handbook", "veridian-acme-plan"}


@pytest.mark.parametrize("person", sorted(EXPECTED))
async def test_persona_reads_exactly_the_expected_documents(
    app_db: Database, halden: Halden, person: str
) -> None:
    assert await readable_names(app_db, halden, person) == EXPECTED[person]


def test_matrix_is_not_happy_path_only() -> None:
    documents = set().union(*EXPECTED.values()) | set(NOBODY) | {"eng-private-channel"}
    for person, allowed in EXPECTED.items():
        assert len(documents - allowed) >= 8, f"{person} must have several denials"
    for document in documents - NOBODY - EVERYONE:
        denied_to = [p for p, allowed in EXPECTED.items() if document not in allowed]
        assert denied_to, f"{document} is denied to nobody"
    for (person, document), _why in NOTABLE_DENIALS.items():
        assert document not in EXPECTED[person]


async def test_nobody_reads_deleted_disbanded_or_other_tenant_documents(
    app_db: Database, halden: Halden
) -> None:
    assert set(halden.documents) >= NOBODY
    for person in EXPECTED:
        assert not (await readable_names(app_db, halden, person)) & NOBODY


async def test_critical_acme_scenario_at_the_authorization_layer(
    app_db: Database, halden: Halden
) -> None:
    """authorization-model.md §10, before retrieval exists: Doc A (internal, Acme) and
    Doc B (confidential, Acme, Leadership). Maya reads A only; Grace reads both."""
    maya = await readable_names(app_db, halden, "maya")
    grace = await readable_names(app_db, halden, "grace")
    assert "acme-success-plan" in maya
    assert "acme-exec-escalation" not in maya
    assert {"acme-success-plan", "acme-exec-escalation"} <= grace


ACCOUNT_VISIBILITY = {
    "maya": {"acme-corp", "northpeak-logistics"},
    "daniel": {"brightwater-health", "kestrel-bank"},
    # Scope "assigned" with no assignments: the admin directory is a separate,
    # capability-gated view; as a member, Lena learns of no accounts.
    "lena": set(),
}


@pytest.mark.parametrize("person", sorted(EXPECTED))
async def test_account_existence_follows_account_scope(
    app_db: Database, halden: Halden, person: str
) -> None:
    ctx = await context_for(app_db, halden.org, halden.members[person])
    async with app_db.tenant_snapshot(halden.org) as s:
        via_sql: set[str] = set(
            (
                await s.execute(select(ACCOUNTS.c.key).where(visible_accounts_predicate(ctx)))
            ).scalars()
        )
    via_reference = {
        key
        for key, account_id in halden.accounts.items()
        if account_visible(ctx, AccountRef(account_id, halden.org))
    }
    expected = ACCOUNT_VISIBILITY.get(person, set(halden.accounts))  # scope "all" -> every one
    assert via_sql == via_reference == expected
    # Readable through a grant is not the same as visible: Maya can read one Kestrel
    # document, but Kestrel is not in her account lists.
    if person == "maya":
        assert "kestrel-shared-with-maya" in EXPECTED["maya"]
        assert "kestrel-bank" not in via_sql


async def test_a_client_account_filter_only_narrows(app_db: Database, halden: Halden) -> None:
    """Filters are extra AND conditions: asking for an out-of-scope account yields the
    same (empty) result as asking for an account that does not exist."""
    ctx = await context_for(app_db, halden.org, halden.members["maya"])

    async def filtered(account_id: object) -> set[str]:
        async with app_db.tenant_snapshot(halden.org) as s:
            rows = await s.execute(
                select(DOCUMENTS.c.id).where(
                    readable_documents_predicate(ctx), DOCUMENTS.c.account_id == account_id
                )
            )
            ids: list[UUID] = list(rows.scalars())
            return {halden.names[i] for i in ids}

    assert await filtered(halden.accounts["acme-corp"]) == {
        "acme-success-plan",
        "acme-private-channel",
    }
    # Kestrel is out of scope; the one Kestrel document Maya holds a grant on is the
    # only thing a Kestrel filter can return -- never kestrel-renewal.
    assert await filtered(halden.accounts["kestrel-bank"]) == {"kestrel-shared-with-maya"}
    assert await filtered(halden.accounts["larkspur-retail"]) == set()
    assert await filtered(uuid4()) == set()
