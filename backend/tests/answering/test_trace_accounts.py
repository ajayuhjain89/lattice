"""A stored trace must not outlive account visibility (security-model §8, §10; D-8).

The stored answer can carry account-derived data through two structured paths -- the
clarification choices built from visible account mentions, and model text drafted from
prompt evidence whose ``account`` attribute is present only for visible accounts -- and
the trace's scope names the account the request was scoped to. Each is re-authorized
when the trace is read: once any of those accounts is no longer visible to the asker,
the answer is hidden and the scope's account withheld, exactly as when it is deleted.
"""

import uuid
from typing import Any

import pytest

from lattice.db.engine import Database
from lattice.tracing.recorder import AdminTrace, AskerTrace, read_trace

from ..retrieval.world import Org, new_org
from .helpers import Harness, claim, configure_generation, draft, handles, run

PROMISE = "We promised a SCIM date in October to the customer."
RENEWAL = "The renewal is at risk after the July incident."


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.account("acme-corp", "Acme Corp", aliases=["Acme"])
    await org.account("acme-health", "Acme Health", aliases=["Acme"])
    await org.account("kestrel", "Kestrel Bank")
    await org.account("northpeak", "Northpeak Logistics")
    await org.member("maya", scope="assigned", accounts=["acme-corp", "acme-health", "kestrel"])
    await org.member("tom", scope="all")
    await org.member("lena", scope="all")
    await org.execute("UPDATE memberships SET is_admin = true WHERE id = :m", m=org.members["lena"])
    await org.document("promise", "Commitments log", [PROMISE])
    # Readable to Maya through an explicit grant whatever her account scope (D-8).
    await org.document(
        "renewal", "Renewal notes", [RENEWAL], account="kestrel", grants=[("membership", "maya")]
    )
    await configure_generation(org)
    return org


async def _view(
    app_db: Database, org: Org, trace_id: uuid.UUID, viewer: str = "maya", *, admin: bool = False
) -> AskerTrace | AdminTrace | None:
    return await read_trace(
        app_db,
        organization_id=org.id,
        viewer_membership_id=org.members[viewer],
        viewer_is_admin=admin,
        trace_id=trace_id,
    )


async def _unassign(org: Org, account: str) -> None:
    await org.execute(
        "DELETE FROM account_assignments WHERE membership_id = :m AND account_id = :a",
        m=org.members["maya"],
        a=org.accounts[account],
    )


def _leaks(view: AskerTrace | AdminTrace, org: Org, account: str, *names: str) -> list[str]:
    dumped = view.model_dump_json()
    return [s for s in (str(org.accounts[account]), account, *names) if s in dumped]


async def test_clarification_choices_are_hidden_once_an_account_is_no_longer_visible(
    app_db: Database, org: Org
) -> None:
    outcome = await run(app_db, org, "maya", "What did we promise Acme?", Harness(), persist=True)
    assert outcome.answer.status == "ambiguous"
    assert outcome.answer.clarification is not None
    assert {c.name for c in outcome.answer.clarification.choices} == {"Acme Corp", "Acme Health"}
    trace_id = outcome.answer.trace_id

    before = await _view(app_db, org, trace_id)
    assert isinstance(before, AskerTrace)
    assert not before.answer_hidden
    assert _leaks(before, org, "acme-health", "Acme Health")

    # An account the answer does not depend on: nothing is hidden.
    await _unassign(org, "kestrel")
    unrelated = await _view(app_db, org, trace_id)
    assert unrelated == before

    await _unassign(org, "acme-health")
    after = await _view(app_db, org, trace_id)
    assert isinstance(after, AskerTrace)
    assert after.answer_hidden
    assert after.answer is None
    assert _leaks(after, org, "acme-health", "Acme Health") == []

    # Being an administrator gives no data access to one's own old trace.
    await org.execute("UPDATE memberships SET is_admin = true WHERE id = :m", m=org.members["maya"])
    as_admin = await _view(app_db, org, trace_id, admin=True)
    assert as_admin == after


async def test_account_metadata_from_prompt_evidence_and_scope_is_reauthorized(
    app_db: Database, org: Org
) -> None:
    # The model saw account="Kestrel Bank" on the evidence and repeated it.
    def build(prompt: str) -> dict[str, Any]:
        h = handles(prompt)["Renewal notes"]
        return draft(claim("Kestrel Bank's renewal is at risk.", (h, "renewal is at risk")))

    outcome = await run(
        app_db,
        org,
        "maya",
        "What is the renewal risk?",
        Harness(outputs=[build]),
        persist=True,
        scope={"account": "kestrel"},
    )
    assert outcome.answer.status == "answered"
    trace_id = outcome.answer.trace_id
    before = await _view(app_db, org, trace_id)
    assert isinstance(before, AskerTrace)
    assert _leaks(before, org, "kestrel", "Kestrel Bank") == ["kestrel", "Kestrel Bank"]

    await _unassign(org, "kestrel")
    after = await _view(app_db, org, trace_id)
    assert isinstance(after, AskerTrace)
    # The document is still readable through its grant: this is the account path alone.
    assert org.chunks["renewal"][0] in after.evidence_ids
    assert after.answer_hidden
    assert after.answer is None
    assert "account" not in after.scope
    assert _leaks(after, org, "kestrel", "Kestrel Bank") == []


async def test_no_longer_visible_and_deleted_accounts_read_identically(
    app_db: Database, org: Org
) -> None:
    outcome = await run(app_db, org, "maya", "What did we promise Acme?", Harness(), persist=True)
    trace_id = outcome.answer.trace_id
    await _unassign(org, "acme-health")
    revoked = await _view(app_db, org, trace_id)
    await org.execute("DELETE FROM accounts WHERE id = :a", a=org.accounts["acme-health"])
    deleted = await _view(app_db, org, trace_id)
    assert isinstance(revoked, AskerTrace)
    assert revoked.answer_hidden
    assert revoked == deleted
    # Another member, admin or not, learns nothing about the accounts involved; a
    # non-administrator gets exactly what a nonexistent trace gives.
    assert await _view(app_db, org, trace_id, "tom") is None
    assert await _view(app_db, org, uuid.uuid4(), "tom") is None
    admin = await _view(app_db, org, trace_id, "lena", admin=True)
    assert isinstance(admin, AdminTrace)
    assert _leaks(admin, org, "acme-corp", "Acme") == []


async def test_unknown_account_dependencies_hide_the_answer(app_db: Database, org: Org) -> None:
    outcome = await run(app_db, org, "maya", "What did we promise Acme?", Harness(), persist=True)
    # A row written before the dependencies were recorded (migration 0009).
    await org.execute(
        "UPDATE query_traces SET answer_account_ids = NULL WHERE id = :t",
        t=outcome.answer.trace_id,
    )
    view = await _view(app_db, org, outcome.answer.trace_id)
    assert isinstance(view, AskerTrace)
    assert view.answer_hidden
    assert view.answer is None
