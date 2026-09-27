"""Fail-fast configuration (docs/architecture.md §8)."""

import pytest

from lattice.config import ConfigurationError, Environment, load_settings

from .conftest import APP_URL

VALID = {"env": "development", "database_url": APP_URL}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # `make test` loads .env; make sure no LATTICE_* variable leaks into these tests.
    import os

    for name in list(os.environ):
        if name.startswith("LATTICE_") and not name.startswith("LATTICE_TEST_"):
            monkeypatch.delenv(name)


def test_valid_configuration_loads() -> None:
    settings = load_settings(**VALID)
    assert settings.env is Environment.DEVELOPMENT
    assert settings.dev_auth_enabled is False  # opt-in only
    assert settings.session_cookie_secure is True  # secure by default


@pytest.mark.parametrize("missing", ["env", "database_url"])
def test_missing_required_setting_names_the_variable(missing: str) -> None:
    values = {k: v for k, v in VALID.items() if k != missing}
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(**values)
    assert f"LATTICE_{missing.upper()}" in str(excinfo.value)


def test_missing_setting_from_real_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATTICE_ENV", "development")
    with pytest.raises(ConfigurationError, match="LATTICE_DATABASE_URL"):
        load_settings()


def test_environment_mode_must_be_explicit_and_valid() -> None:
    with pytest.raises(ConfigurationError, match="LATTICE_ENV"):
        load_settings(env="staging", database_url=APP_URL)


def test_invalid_value_is_named_but_never_echoed() -> None:
    secret_url = "postgres://lattice_app:hunter2-super-secret@db/lattice"
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env="development", database_url=secret_url)
    message = str(excinfo.value)
    assert "LATTICE_DATABASE_URL" in message
    assert "hunter2-super-secret" not in message
    assert secret_url not in message


def test_production_with_dev_auth_refuses_to_start() -> None:
    with pytest.raises(ConfigurationError, match="LATTICE_DEV_AUTH_ENABLED"):
        load_settings(env="production", database_url=APP_URL, dev_auth_enabled=True)


def test_production_requires_secure_cookies() -> None:
    with pytest.raises(ConfigurationError, match="LATTICE_SESSION_COOKIE_SECURE"):
        load_settings(env="production", database_url=APP_URL, session_cookie_secure=False)


def test_idle_timeout_cannot_exceed_absolute_timeout() -> None:
    with pytest.raises(ConfigurationError, match="IDLE_TIMEOUT"):
        load_settings(
            **VALID, session_idle_timeout_minutes=600, session_absolute_timeout_minutes=60
        )


def test_secure_cookie_uses_host_prefix() -> None:
    assert load_settings(**VALID).session_cookie_name == "__Host-lattice_session"
    insecure = load_settings(**VALID, session_cookie_secure=False)
    assert insecure.session_cookie_name == "lattice_session"


def test_database_url_is_secret_in_repr() -> None:
    settings = load_settings(**VALID)
    assert APP_URL not in repr(settings)
    assert APP_URL not in str(settings.model_dump())
