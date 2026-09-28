"""Typed, fail-fast configuration.

Settings come only from the process environment (``LATTICE_*`` variables). The
Makefile loads the repository ``.env`` into the environment; nothing here reads files.

Validation errors are re-raised as :class:`ConfigurationError` whose message names each
offending variable but never includes its value: database URLs carry passwords, and
pydantic's default error text echoes the input.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "LATTICE_"
_ASYNC_POSTGRES_SCHEME = "postgresql+asyncpg://"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ConfigurationError(Exception):
    """Configuration is missing or invalid; the process must not start."""


def _require_asyncpg_url(value: SecretStr | None) -> SecretStr | None:
    if value is not None and not value.get_secret_value().startswith(_ASYNC_POSTGRES_SCHEME):
        raise ValueError(f"must be a PostgreSQL URL starting with {_ASYNC_POSTGRES_SCHEME}")
    return value


class Settings(BaseSettings):
    """Settings for the API process."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore", frozen=True)

    # Deliberately no default: the environment mode must always be explicit.
    env: Environment
    # Runtime role (lattice_app). Checked against unsafe privileges at startup.
    database_url: SecretStr

    dev_auth_enabled: bool = False
    session_cookie_secure: bool = True
    session_idle_timeout_minutes: int = Field(default=720, ge=5, le=7 * 24 * 60)
    session_absolute_timeout_minutes: int = Field(default=7 * 24 * 60, ge=5, le=30 * 24 * 60)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    _check_url = field_validator("database_url")(_require_asyncpg_url)

    @model_validator(mode="after")
    def _enforce_environment_guards(self) -> Self:
        if self.env is Environment.PRODUCTION and self.dev_auth_enabled:
            raise ValueError(
                "LATTICE_DEV_AUTH_ENABLED must be false when LATTICE_ENV=production "
                "(development login is never available in production)"
            )
        if self.env is Environment.PRODUCTION and not self.session_cookie_secure:
            raise ValueError(
                "LATTICE_SESSION_COOKIE_SECURE must be true when LATTICE_ENV=production"
            )
        if self.session_idle_timeout_minutes > self.session_absolute_timeout_minutes:
            raise ValueError(
                "LATTICE_SESSION_IDLE_TIMEOUT_MINUTES must not exceed "
                "LATTICE_SESSION_ABSOLUTE_TIMEOUT_MINUTES"
            )
        return self

    @property
    def dev_auth_active(self) -> bool:
        """Dev login is served only when enabled *and* outside production."""
        return self.dev_auth_enabled and self.env is not Environment.PRODUCTION

    @property
    def session_cookie_name(self) -> str:
        # The __Host- prefix makes browsers reject the cookie unless it is Secure,
        # host-only and Path=/, which blocks subdomain cookie injection.
        return "__Host-lattice_session" if self.session_cookie_secure else "lattice_session"


class MigrationSettings(BaseSettings):
    """Settings for operator tools (migrations, seed) that run as lattice_owner."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore", frozen=True)

    migration_database_url: SecretStr

    _check_url = field_validator("migration_database_url")(_require_asyncpg_url)


def _describe(exc: ValidationError) -> str:
    problems: list[str] = []
    for error in exc.errors(include_input=False, include_url=False):
        location = error.get("loc") or ()
        message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
        if location:
            variable = ENV_PREFIX + str(location[0]).upper()
            problems.append(f"{variable}: {message}")
        else:
            problems.append(message)
    return "Invalid configuration:\n  - " + "\n  - ".join(problems)


def load_settings(**overrides: object) -> Settings:
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc)) from None


def load_migration_settings(**overrides: object) -> MigrationSettings:
    try:
        return MigrationSettings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc)) from None


class ProviderSettings(BaseSettings):
    """Model-provider settings for operator tools (ingestion). Secrets only here, from
    the environment; never in the database or source configuration."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore", frozen=True)

    env: Environment
    openai_api_key: SecretStr | None = None
    openai_embedding_model: str = "text-embedding-3-small"


def load_provider_settings(**overrides: object) -> ProviderSettings:
    try:
        return ProviderSettings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc)) from None
