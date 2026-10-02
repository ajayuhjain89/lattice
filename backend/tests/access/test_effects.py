"""Effects are rules derived from two snapshots alone (no database, no documents)."""

import uuid

from lattice.access.directory import AccessSnapshot
from lattice.access.preview import effects

ACME, KESTREL, SALES = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NAMES = {ACME: "Acme Corp", KESTREL: "Kestrel Bank", SALES: "Sales"}


def snap(**fields: object) -> AccessSnapshot:
    values: dict[str, object] = {
        "status": "active",
        "clearance": "internal",
        "account_scope": "assigned",
        "accounts": frozenset({ACME}),
        "departments": frozenset({SALES}),
        "groups": frozenset(),
        "is_admin": False,
    }
    values.update(fields)
    return AccessSnapshot(**values)  # type: ignore[arg-type]


def test_a_new_member_is_described_in_full() -> None:
    found = [
        (e.kind, e.before, e.after, e.subject and e.subject.name)
        for e in effects(None, snap(is_admin=True), NAMES)
    ]
    assert found == [
        ("provisioned", None, "active", None),
        ("clearance", None, "internal", None),
        ("account_scope", None, "assigned", None),
        ("administrator", "false", "true", None),
        ("account_added", None, None, "Acme Corp"),
        ("department_added", None, None, "Sales"),
    ]


def test_changes_are_listed_in_a_fixed_order_and_deleted_names_are_none() -> None:
    gone = uuid.uuid4()
    after = snap(
        status="suspended",
        clearance="confidential",
        accounts=frozenset({KESTREL, gone}),
        departments=frozenset(),
    )
    found = [(e.kind, e.subject and e.subject.name) for e in effects(snap(), after, NAMES)]
    assert found == [
        ("suspended", None),
        ("clearance", None),
        ("account_added", None),
        ("account_added", "Kestrel Bank"),
        ("account_removed", "Acme Corp"),
        ("department_removed", "Sales"),
    ]


def test_no_change_has_no_effects() -> None:
    assert effects(snap(), snap(), NAMES) == []
