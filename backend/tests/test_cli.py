"""`lattice check-db-role` mirrors the API startup check."""

import pytest

from lattice.cli import main

from .conftest import APP_URL, OWNER_URL


def test_check_db_role_accepts_app_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATTICE_ENV", "test")
    monkeypatch.setenv("LATTICE_DATABASE_URL", APP_URL)
    assert main(["check-db-role"]) == 0


def test_check_db_role_rejects_owner_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATTICE_ENV", "test")
    monkeypatch.setenv("LATTICE_DATABASE_URL", OWNER_URL)
    assert main(["check-db-role"]) == 1


def test_check_db_role_reports_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LATTICE_DATABASE_URL", raising=False)
    monkeypatch.setenv("LATTICE_ENV", "test")
    assert main(["check-db-role"]) == 2
