"""The shared connector contract kit (docs/connector-design.md §2, §7).

The same assertions run against every connector: over both organizations' real exports
(stable, normalized, unique IDs; zone-aware timestamps; plain text; deterministic
order; record keys exactly the dataset's) and over malformed exports (a bad record
yields a ``RecordError`` and iteration continues; a source-level problem raises
``SourceUnavailable``; errors carry codes and identifiers, never content).

A new connector joins by adding itself to ``CONNECTORS`` and its malformed fixtures to
``MALFORMED``.
"""

import ast
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

import lattice.connectors
from lattice.connectors.contract import (
    Connector,
    RecordError,
    SourceRecord,
    SourceUnavailable,
    normalize_external_id,
)
from lattice.connectors.markdown import MarkdownConfig, MarkdownConnector
from lattice.connectors.registry import build_connector
from lattice.connectors.slack import SlackConfig, SlackConnector
from lattice.connectors.tickets import TicketsConfig, TicketsConnector
from lattice.dataset.lint import Dataset, load_dataset
from lattice.sources.config import load_sources_file

from .conftest import DATA, GOLDEN, ORGS, PREFIX

_HTML = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s[^<>]*)?/?>")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

CONNECTORS = [
    (slug, source.key, source.connector_type, DATA / slug / source.path)
    for slug in ORGS
    for source in load_sources_file(DATA / slug / "sources.yaml").sources
]
IDS = [f"{slug}-{key}" for slug, key, _, _ in CONNECTORS]


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATA, GOLDEN)


def _read(connector: Connector) -> list[SourceRecord | RecordError]:
    return list(connector.records())


# --- every connector over the real exports ----------------------------------------------


@pytest.mark.parametrize(("slug", "key", "connector_type", "path"), CONNECTORS, ids=IDS)
def test_real_exports_read_cleanly_and_match_the_dataset(
    dataset: Dataset, slug: str, key: str, connector_type: str, path: Path
) -> None:
    items = _read(build_connector(connector_type, path))  # type: ignore[arg-type]
    errors = [i for i in items if isinstance(i, RecordError)]
    records = [i for i in items if isinstance(i, SourceRecord)]
    assert errors == []
    expected = {k for k, r in dataset.worlds[slug].raw.records.items() if r.source == key}
    keys = [f"{PREFIX[connector_type]}:{r.external_id}" for r in records]
    assert len(keys) == len(set(keys)), "external IDs must be unique within a source"
    assert set(keys) == expected


@pytest.mark.parametrize(("slug", "key", "connector_type", "path"), CONNECTORS, ids=IDS)
def test_records_satisfy_the_contract(slug: str, key: str, connector_type: str, path: Path) -> None:
    records = [
        r
        for r in _read(build_connector(connector_type, path))  # type: ignore[arg-type]
        if isinstance(r, SourceRecord)
    ]
    assert records
    for r in records:
        assert r.external_id == normalize_external_id(r.external_id)
        assert 1 <= len(r.external_id) <= 256
        assert r.updated_at.utcoffset() is not None
        assert r.created_at is None or r.created_at.utcoffset() is not None
        assert r.uri is None or r.uri.startswith("https://")
        texts = [r.title, *(p.text for p in r.parts), *(p.heading or "" for p in r.parts)]
        for value in texts:
            assert not _HTML.search(value), r.external_id
            assert not _CONTROL.search(value), r.external_id
        for part in r.parts:
            assert part.occurred_at is None or part.occurred_at.utcoffset() is not None
        assert len({p.anchor for p in r.parts}) == len(r.parts)


@pytest.mark.parametrize(("slug", "key", "connector_type", "path"), CONNECTORS, ids=IDS)
def test_iteration_is_deterministic(slug: str, key: str, connector_type: str, path: Path) -> None:
    first = _read(build_connector(connector_type, path))  # type: ignore[arg-type]
    second = _read(build_connector(connector_type, path))  # type: ignore[arg-type]
    assert first == second


@pytest.mark.parametrize(("slug", "key", "connector_type", "path"), CONNECTORS, ids=IDS)
def test_capabilities_are_declared_honestly(
    slug: str, key: str, connector_type: str, path: Path
) -> None:
    connector = build_connector(connector_type, path)  # type: ignore[arg-type]
    caps = connector.capabilities
    # Local exports are full snapshots (the deletion sweep depends on it) and have no
    # checkpoints.
    assert caps.snapshot
    assert not caps.incremental
    groups = connector.access_groups()
    records = [r for r in _read(connector) if isinstance(r, SourceRecord)]
    uses_hints = any(
        r.access.sensitivity or r.access.grants_only or r.access.group_refs for r in records
    )
    assert caps.acl_export or (not uses_hints and not groups)


def test_private_channels_are_reported_as_groups_with_member_emails() -> None:
    connector = SlackConnector(SlackConfig(path=DATA / "halden" / "slack"))
    groups = {g.external_ref: g for g in connector.access_groups()}
    private = json.loads((DATA / "halden" / "slack" / "groups.json").read_text())
    assert set(groups) == {c["id"] for c in private}
    acme = groups["G0ACCTACME1"]
    assert acme.name == "#acct-acme"
    assert "maya.okafor@halden.example" in acme.member_emails
    assert all(e == e.lower() for g in groups.values() for e in g.member_emails)
    records = [r for r in connector.records() if isinstance(r, SourceRecord)]
    for r in records:
        if r.external_id.startswith("G0ACCTACME1:"):
            assert r.access.grants_only
            assert r.access.group_refs == ("G0ACCTACME1",)
        if r.external_id.startswith("C0"):
            assert not r.access.grants_only
            assert r.access.group_refs == ()


def test_connectors_import_nothing_beyond_source_interpretation() -> None:
    """Connectors never touch the database, providers, ingestion or authorization."""
    package = Path(lattice.connectors.__file__).parent
    allowed_lattice = ("lattice.connectors",)
    allowed_third_party = {"pydantic", "yaml"}
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{path.name}: use absolute imports"
                names = [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                if top == "lattice":
                    assert name.startswith(allowed_lattice), f"{path.name} imports {name}"
                else:
                    assert top in sys.stdlib_module_names or top in allowed_third_party, (
                        f"{path.name} imports {name}"
                    )


def test_record_errors_cannot_carry_content() -> None:
    with pytest.raises(ValidationError):
        RecordError(stage="parse", code="Bad JSON near 'Project Tern'")
    with pytest.raises(ValidationError):
        RecordError(stage="parse", code="malformed_json", params={"line": "Project Tern memo"})
    RecordError(stage="validate", code="invalid_record", params={"field": "created", "line": 3})


# --- malformed exports ------------------------------------------------------------------

SECRET = "ZEPHYR-PAYLOAD-91"  # appears only inside malformed records' content

FRONT = (
    "---\nid: {id}\ntitle: {title}\nauthor: Ana Lima\ncreated: 2026-01-05T09:00:00Z\n"
    "updated: 2026-01-06T09:00:00Z\n---\n## Section\n{body}\n"
)


def _markdown(root: Path) -> None:
    root.mkdir()
    (root / "a-good.md").write_text(FRONT.format(id="good", title="Good", body="Fine text."))
    (root / "b-no-front.md").write_text(f"# {SECRET}\nno front matter\n")
    (root / "c-unterminated.md").write_text(f"---\nid: x\ntitle: {SECRET}\n")
    (root / "d-bad-yaml.md").write_text(f"---\nid: [unclosed\n---\n{SECRET}\n")
    (root / "e-bad-date.md").write_text(
        FRONT.format(id="bad-date", title=SECRET, body="x").replace(
            "2026-01-05T09:00:00Z", "yesterday"
        )
    )
    (root / "f-dup1.md").write_text(FRONT.format(id="dup", title=SECRET, body="one"))
    (root / "g-dup2.md").write_text(FRONT.format(id="dup", title=SECRET, body="two"))
    (root / "h-html.md").write_text(
        FRONT.format(id="html", title="Html", body=f"<script>{SECRET}</script>")
    )
    (root / "i-empty.md").write_text(
        FRONT.format(id="empty", title=SECRET, body="").replace("## Section\n", "")
    )
    (root / "j-latin1.md").write_bytes(f"---\nid: l\ntitle: caf\xe9 {SECRET}\n".encode("latin-1"))
    (root / "k-bad-grant.md").write_text(
        FRONT.format(id="bad-grant", title=SECRET, body="x").replace(
            "---\n## Section", "grants: [user:someone]\n---\n## Section"
        )
    )
    (root / "z-good.md").write_text(FRONT.format(id="last", title="Last", body="Still read."))


def _ticket(ticket_id: str, **overrides: object) -> str:
    ticket: dict[str, object] = {
        "id": ticket_id,
        "subject": "Subject",
        "organization_id": 1,
        "status": "open",
        "priority": "low",
        "created_at": "2026-01-05T09:00:00Z",
        "updated_at": "2026-01-05T10:00:00Z",
        "requester": {"name": "Rea Quester", "email": "rea@example.com"},
        "comments": [
            {
                "id": 1,
                "author": {"name": "Rea Quester", "email": "rea@example.com"},
                "author_role": "end-user",
                "public": True,
                "created_at": "2026-01-05T09:00:00Z",
                "body": "Body text.",
            }
        ],
    }
    ticket.update(overrides)
    return json.dumps(ticket)


def _tickets(root: Path) -> None:
    root.mkdir()
    lines = [
        _ticket("T-1"),
        "",
        f'{{"id": "T-2", "subject": "{SECRET}",',
        json.dumps([SECRET]),
        _ticket("T-3", created_at="not a date", subject=SECRET),
        _ticket("T-4", comments=[]),
        _ticket("T-5", subject=SECRET),
        _ticket("T-5", subject=SECRET),
        _ticket("T-6"),
    ]
    (root / "a.jsonl").write_text("\n".join(lines) + "\n")
    (root / "b.jsonl").write_bytes(
        f'{{"id": "T-7", "subject": "caf\xe9 {SECRET}"}}\n'.encode("latin-1")
    )
    (root / "c.jsonl").write_text(_ticket("T-8") + "\n")


def _slack_base(root: Path) -> None:
    root.mkdir()
    (root / "users.json").write_text(
        json.dumps(
            [
                {"id": "U1", "real_name": "Uma One", "profile": {"email": "Uma@Example.com"}},
                {"id": "U2", "real_name": "Ugo Two", "profile": {"email": "ugo@example.com"}},
            ]
        )
    )
    (root / "channels.json").write_text(json.dumps([{"id": "C1", "name": "general"}]))
    (root / "groups.json").write_text(
        json.dumps([{"id": "G1", "name": "private", "members": ["U1", "U9"]}])
    )


def _msg(ts: str, text: str, **extra: object) -> dict[str, object]:
    return {"type": "message", "user": "U1", "ts": ts, "text": text, **extra}


def _slack(root: Path) -> None:
    _slack_base(root)
    general = root / "general"
    general.mkdir()
    (general / "2026-01-05.json").write_text(
        json.dumps(
            [
                _msg("1767603600.000100", "hello"),
                # a root whose export lacks reply_count: still one thread, no day record
                _msg("1767603700.000100", "root", thread_ts="1767603700.000100"),
                _msg("1767603800.000100", "reply", thread_ts="1767603700.000100", user="U2"),
                _msg("1767603900.000100", f"orphan {SECRET}", thread_ts="1767000000.000100"),
                {"type": "message", "ts": "not-a-ts", "text": SECRET},
                _msg("1767604000.000100", "<@U2> &amp; <@U7> &lt;3"),
            ]
        )
    )
    (general / "2026-01-06.json").write_text(f"[{{'broken': '{SECRET}'")
    (root / "private").mkdir()
    (root / "private" / "2026-01-05.json").write_text(
        json.dumps([_msg("1767603600.000200", "secret plans")])
    )
    (root / "unknown-channel").mkdir()


Fixture = Callable[[Path], None]
MALFORMED: dict[str, tuple[Fixture, Callable[[Path], Connector]]] = {
    "markdown": (_markdown, lambda p: MarkdownConnector(MarkdownConfig(path=p))),
    "tickets": (_tickets, lambda p: TicketsConnector(TicketsConfig(path=p))),
    "slack": (_slack, lambda p: SlackConnector(SlackConfig(path=p))),
}

EXPECTED_ERRORS = {
    "markdown": [
        ("parse", "missing_front_matter", None),
        ("parse", "unterminated_front_matter", None),
        ("parse", "malformed_front_matter", None),
        ("validate", "invalid_record", "bad-date"),
        ("validate", "duplicate_external_id", "dup"),
        ("validate", "duplicate_external_id", "dup"),
        ("validate", "invalid_record", "html"),
        ("validate", "empty_record", "empty"),
        ("read", "unreadable_file", None),
        ("validate", "invalid_record", "bad-grant"),
    ],
    "tickets": [
        ("parse", "blank_line", None),
        ("parse", "malformed_json", None),
        ("parse", "malformed_json", None),
        ("validate", "invalid_record", "T-3"),
        ("validate", "invalid_record", "T-4"),
        ("validate", "duplicate_external_id", "T-5"),
        ("validate", "duplicate_external_id", "T-5"),
        ("read", "unreadable_file", None),
    ],
    "slack": [
        ("validate", "invalid_message", None),
        ("parse", "malformed_json", None),
        ("validate", "orphan_reply", "C1:1767000000.000100"),
        ("validate", "unknown_channel", None),
    ],
}
GOOD = {
    "markdown": {"good", "last"},
    "tickets": {"T-1", "T-6", "T-8"},
    "slack": {"C1:1767603700.000100", "C1:day:2026-01-05", "G1:day:2026-01-05"},
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_records_are_reported_and_iteration_continues(tmp_path: Path, name: str) -> None:
    build, make = MALFORMED[name]
    root = tmp_path / name
    build(root)
    items = list(make(root).records())
    errors = [i for i in items if isinstance(i, RecordError)]
    records = [i for i in items if isinstance(i, SourceRecord)]
    assert sorted((e.stage, e.code, e.external_id or "") for e in errors) == sorted(
        (stage, code, ext or "") for stage, code, ext in EXPECTED_ERRORS[name]
    )
    assert {r.external_id for r in records} == GOOD[name]
    # People appear by name: metadata emails (requester, comment author, Slack profile)
    # are never copied into content.
    for r in records:
        dumped = r.model_dump_json(exclude={"access"})
        assert "rea@example.com" not in dumped
        assert "uma@example.com" not in dumped.lower()
    # Errors carry fixed codes and identifiers only: content never appears in them
    # (a location is a filename, for operators).
    for error in errors:
        assert SECRET not in error.model_dump_json(exclude={"location"})


def test_slack_markup_is_made_readable_and_threads_are_grouped(tmp_path: Path) -> None:
    root = tmp_path / "slack"
    _slack(root)
    records = {
        r.external_id: r
        for r in SlackConnector(SlackConfig(path=root)).records()
        if isinstance(r, SourceRecord)
    }
    day = records["C1:day:2026-01-05"]
    assert [p.text for p in day.parts] == ["hello", "@Ugo Two & @unknown <3"]
    thread = records["C1:1767603700.000100"]
    assert thread.kind == "chat_thread"
    assert [p.text for p in thread.parts] == ["root", "reply"]
    assert [p.author for p in thread.parts] == ["Uma One", "Ugo Two"]
    private = records["G1:day:2026-01-05"]
    assert private.access.grants_only
    assert private.access.group_refs == ("G1",)
    (group,) = SlackConnector(SlackConfig(path=root)).access_groups()
    # U9 is not a known user: not a member. Emails are lowercased.
    assert group.member_emails == ("uma@example.com",)


@pytest.mark.parametrize(
    ("name", "breakage", "code"),
    [
        ("markdown", "missing", "source_unavailable:missing_export"),
        ("tickets", "missing", "source_unavailable:missing_export"),
        ("slack", "missing", "source_unavailable:missing_export"),
        ("slack", "no_users", "source_unavailable:incomplete_export"),
        ("slack", "bad_channels", "source_unavailable:malformed_export"),
        ("slack", "duplicate_channel", "source_unavailable:malformed_export"),
    ],
)
def test_source_level_problems_raise_source_unavailable(
    tmp_path: Path, name: str, breakage: str, code: str
) -> None:
    _, make = MALFORMED[name]
    root = tmp_path / name
    if breakage != "missing":
        _slack_base(root)
        if breakage == "no_users":
            (root / "users.json").unlink()
        elif breakage == "bad_channels":
            (root / "channels.json").write_text("{not json")
        else:
            (root / "groups.json").write_text(json.dumps([{"id": "C1", "name": "dup"}]))
    connector = make(root)
    with pytest.raises(SourceUnavailable) as caught:
        connector.check()
        list(connector.records())
    assert caught.value.code == code
