"""Decision D-8: an explicit grant makes a document readable without disclosing its
account's structured record.

Readability (``readable``) and account visibility (``account_visible``) are separate
decisions. ``visible_document_account`` is what evidence cards, prompt evidence
attributes and facets will use for a document's account field: the account only when
the document is readable **and** the account is inside the reader's scope; otherwise
nothing -- never a substitute name.

The Halden fixture has the case exactly: ``kestrel-shared-with-maya`` is a Kestrel Bank
document granted to Maya personally, and Kestrel is outside her account scope.
"""

import uuid

import pytest
from sqlalchemy import and_, select

from lattice.authz import (
    AccountRef,
    account_visible,
    load_document_access,
    readable,
    readable_documents_predicate,
    visible_accounts_predicate,
    visible_document_account,
)
from lattice.authz.sql import ACCOUNTS, DOCUMENTS
from lattice.db.engine import Database

from .conftest import Halden, context_for
from .test_authz_personas import EXPECTED


async def _shown_accounts(db: Database, world: Halden, person: str) -> dict[str, uuid.UUID | None]:
    """Document name -> the account the reference helper shows, for every readable
    document of ``person``."""
    ctx = await context_for(db, world.org, world.members[person])
    async with db.tenant_snapshot(world.org) as s:
        access = await load_document_access(s, world.org, list(world.documents.values()))
    return {
        world.names[i]: visible_document_account(ctx, doc)
        for i, doc in access.items()
        if readable(ctx, doc)
    }


async def test_a_grant_outside_account_scope_reads_the_document_but_hides_the_account(
    app_db: Database, halden: Halden
) -> None:
    ctx = await context_for(app_db, halden.org, halden.members["maya"])
    async with app_db.tenant_snapshot(halden.org) as s:
        access = await load_document_access(
            s, halden.org, [halden.documents["kestrel-shared-with-maya"]]
        )
    doc = access[halden.documents["kestrel-shared-with-maya"]]
    assert doc.account_id == halden.accounts["kestrel-bank"]
    assert readable(ctx, doc)
    assert not account_visible(ctx, AccountRef(halden.accounts["kestrel-bank"], halden.org))
    assert visible_document_account(ctx, doc) is None


async def test_the_account_owner_sees_the_same_documents_account(
    app_db: Database, halden: Halden
) -> None:
    shown = await _shown_accounts(app_db, halden, "daniel")
    assert shown["kestrel-shared-with-maya"] == halden.accounts["kestrel-bank"]
    maya = await _shown_accounts(app_db, halden, "maya")
    assert maya["acme-success-plan"] == halden.accounts["acme-corp"]
    assert maya["acme-private-channel"] == halden.accounts["acme-corp"]
    assert maya["kestrel-shared-with-maya"] is None


@pytest.mark.parametrize("person", sorted(EXPECTED))
async def test_structured_account_is_shown_only_when_readable_and_visible(
    app_db: Database, halden: Halden, person: str
) -> None:
    ctx = await context_for(app_db, halden.org, halden.members[person])
    async with app_db.tenant_snapshot(halden.org) as s:
        access = await load_document_access(s, halden.org, list(halden.documents.values()))
    for doc in access.values():
        shown = visible_document_account(ctx, doc)
        if not readable(ctx, doc) or doc.account_id is None:
            assert shown is None  # unreadable documents disclose nothing
        elif account_visible(ctx, AccountRef(doc.account_id, doc.organization_id)):
            assert shown == doc.account_id
        else:
            assert shown is None


@pytest.mark.parametrize("person", sorted(EXPECTED))
async def test_the_sql_join_form_agrees(app_db: Database, halden: Halden, person: str) -> None:
    """How retrieval will attach account names to evidence: a LEFT JOIN whose ON
    clause carries the visibility predicate. It must agree with the reference helper."""
    ctx = await context_for(app_db, halden.org, halden.members[person])
    visible = ACCOUNTS.alias("visible_account")
    async with app_db.tenant_snapshot(halden.org) as s:
        rows = (
            await s.execute(
                select(DOCUMENTS.c.id, visible.c.id)
                .select_from(
                    DOCUMENTS.outerjoin(
                        visible,
                        and_(
                            visible.c.id == DOCUMENTS.c.account_id,
                            visible_accounts_predicate(ctx, visible),
                        ),
                    )
                )
                .where(readable_documents_predicate(ctx))
            )
        ).all()
    via_sql = {halden.names[row[0]]: row[1] for row in rows}
    assert via_sql == await _shown_accounts(app_db, halden, person)
