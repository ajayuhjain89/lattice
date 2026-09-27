"""The pre-generation tripwire (E4): abort on any unreadable evidence; never filter.

``assert_all_readable`` re-reads access columns and applies the reference policy. It
is a proof that the SQL predicate held, so every failure is an incident: the request
stops, a ``security.invariant_violated`` event is logged with IDs and counts only, and
the API renders a fixed message that says nothing about the documents involved.
"""

import json
import logging
import uuid

import pytest

from lattice.authz import AuthorizationInvariantViolation, assert_all_readable
from lattice.db.engine import Database
from lattice.errors import DEFAULT_MESSAGE, ErrorCode
from lattice.log import JsonFormatter

from .conftest import Halden, context_for


async def _check(db: Database, world: Halden, person: str, names: list[str]) -> None:
    ctx = await context_for(db, world.org, world.members[person])
    ids = [world.documents[n] if n in world.documents else uuid.UUID(n) for n in names]
    async with db.tenant_snapshot(world.org) as s:
        await assert_all_readable(s, ctx, ids)


async def test_passes_when_every_document_is_readable(app_db: Database, halden: Halden) -> None:
    await _check(
        app_db,
        halden,
        "maya",
        ["acme-success-plan", "acme-private-channel", "kestrel-shared-with-maya"],
    )
    await _check(app_db, halden, "maya", [])


@pytest.mark.parametrize(
    "offending",
    [
        "acme-exec-escalation",  # unreadable in the same org
        "board-mna-memo",  # restricted
        "deleted-handbook",  # deleted after retrieval would have been wrong
        "veridian-handbook",  # another tenant's document
        str(uuid.UUID(int=424242)),  # no such document
    ],
)
async def test_aborts_instead_of_filtering(
    app_db: Database, halden: Halden, offending: str
) -> None:
    # Readable evidence alongside the bad item does not rescue the request.
    with pytest.raises(AuthorizationInvariantViolation):
        await _check(app_db, halden, "maya", ["acme-success-plan", offending, "company-handbook"])


async def test_violation_is_logged_with_ids_and_counts_only(
    app_db: Database, halden: Halden, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        caplog.at_level(logging.ERROR, logger="lattice.authz.invariant"),
        pytest.raises(AuthorizationInvariantViolation),
    ):
        await _check(app_db, halden, "maya", ["acme-success-plan", "board-mna-memo"])

    [record] = [r for r in caplog.records if r.getMessage() == "security.invariant_violated"]
    fields = record.fields  # type: ignore[attr-defined]
    assert fields["checked"] == 2
    assert fields["violations"] == 1
    assert fields["document_ids"] == [str(halden.documents["board-mna-memo"])]
    assert fields["membership_id"] == str(halden.members["maya"])
    rendered = JsonFormatter(include_exception_text=False).format(record)
    json.loads(rendered)
    # No access metadata or policy reasoning reaches the log sink (SEC-8).
    for leaked in (
        "restricted",
        "confidential",
        "grants_only",
        "exec",
        "Exec staff",
        "clearance",
        "sensitivity",
        "department",
        "reason",
    ):
        assert leaked not in rendered, leaked


def test_the_error_says_nothing_about_the_documents() -> None:
    error = AuthorizationInvariantViolation()
    assert error.code is ErrorCode.SECURITY_INVARIANT_VIOLATED
    assert error.status == 500
    assert error.message == DEFAULT_MESSAGE[ErrorCode.SECURITY_INVARIANT_VIOLATED]
    assert error.details is None
