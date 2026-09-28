"""The ingestion operator commands: `lattice sync`, `reindex`, `source disconnect`,
`apply-admin-grants`. Exit codes, counts-only output (never titles, record IDs or
filenames), and configuration errors that name what is wrong."""

from pathlib import Path

import pytest

from lattice.cli import main

from ..conftest import OWNER_URL
from .conftest import DATA

HALDEN = DATA / "halden"
SOURCES = str(HALDEN / "sources.yaml")


@pytest.fixture
def operator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATTICE_ENV", "test")
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    monkeypatch.delenv("LATTICE_OPENAI_API_KEY", raising=False)
    assert main(["seed", "--manifest", str(HALDEN / "org.yaml")]) == 0


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


FORBIDDEN = ("Enterprise refund policy", "HAL-4951", "Fernhill", "tickets.jsonl", "Project Tern")


def _clean(output: str) -> None:
    for value in FORBIDDEN:
        assert value not in output


@pytest.mark.usefixtures("operator_env")
def test_sync_commands(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(capsys, "sync", "--org", "halden", "--sources", SOURCES, "--dry-run")
    assert code == 0
    assert "halden/drive (full): dry run -- seen 48, would create 48" in out
    assert "failures: access_unmappable:account x1" in out
    _clean(out)

    code, out = _run(capsys, "sync", "--org", "halden", "--sources", SOURCES, "--embedding", "fake")
    assert code == 3  # completed, but partial: one support ticket could not be mapped
    assert "halden/support (full): partial" in out
    assert "halden/drive (full): succeeded" in out
    assert "excluded by AI egress policy" in out
    _clean(out)

    code, out = _run(capsys, "sync", "--org", "halden", "--sources", SOURCES, "--source", "drive")
    assert code == 0
    assert "unchanged 48" in out

    code, out = _run(
        capsys,
        "sync",
        "--org",
        "halden",
        "--sources",
        SOURCES,
        "--source",
        "drive",
        "--mode",
        "acl_only",
    )
    assert (code, "halden/drive (acl_only): succeeded" in out) == (0, True)

    code, out = _run(
        capsys,
        "reindex",
        "--org",
        "halden",
        "--sources",
        SOURCES,
        "--source",
        "drive",
        "--reembed",
        "--embedding",
        "fake",
    )
    assert code == 0
    assert "halden/drive (reindex): succeeded" in out

    grants = str(HALDEN / "admin-grants.yaml")
    code, out = _run(capsys, "apply-admin-grants", "--org", "halden", "--grants", grants)
    assert (code, "admin grants added 1, unchanged 0, removed 0" in out) == (0, True)
    code, out = _run(capsys, "apply-admin-grants", "--org", "halden", "--grants", grants)
    assert "added 0, unchanged 1" in out

    code, out = _run(capsys, "source", "disconnect", "--org", "halden", "--source", "slack")
    assert code == 0
    assert "halden/slack: disconnected and purged -- documents 148" in out
    code, out = _run(capsys, "source", "disconnect", "--org", "halden", "--source", "slack")
    assert (code, "unknown source: slack" in out) == (2, True)


@pytest.mark.usefixtures("operator_env")
def test_admin_grant_problems_fail_without_writing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    bad = tmp_path / "grants.yaml"
    bad.write_text(
        "grants:\n  - document: doc:kestrel-sso-incident-customer-rca\n"
        "    member: maya.okafor@halden.example\n    reason: r\n    granted_on: 2026-09-16\n"
    )
    code, out = _run(capsys, "apply-admin-grants", "--org", "halden", "--grants", str(bad))
    assert code == 1
    assert "entry #1: unknown_document" in out
    assert "FAILED -- nothing was written" in out
    bad.write_text("grants:\n  - document: doc:x\n")
    code, out = _run(capsys, "apply-admin-grants", "--org", "halden", "--grants", str(bad))
    assert (code, "invalid grants file" in out) == (2, True)


@pytest.mark.usefixtures("operator_env")
@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--org", "nowhere", "--sources", SOURCES], "unknown organization: nowhere"),
        (["--org", "halden", "--sources", SOURCES, "--source", "wiki"], "unknown source key"),
        (["--org", "halden", "--sources", "/nonexistent/sources.yaml"], "sources file not found"),
        (["--org", "halden", "--sources", SOURCES, "--embedding", "openai"], "LATTICE_OPENAI"),
    ],
)
def test_sync_configuration_errors_exit_2(
    capsys: pytest.CaptureFixture[str], argv: list[str], message: str
) -> None:
    code, out = _run(capsys, "sync", *argv)
    assert code == 2
    assert message in out


@pytest.mark.usefixtures("operator_env")
def test_an_invalid_sources_file_names_the_problem(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n  - key: Drive!\n    connector_type: document_repository\n    name: D\n"
        "    path: documents\n    default_sensitivity: internal\n    authority: working\n"
    )
    code, out = _run(capsys, "sync", "--org", "halden", "--sources", str(bad))
    assert code == 2
    assert "sources.0.key" in out
