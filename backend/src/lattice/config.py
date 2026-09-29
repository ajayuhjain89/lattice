"""Typed, fail-fast configuration.

Settings come only from the process environment (``LATTICE_*`` variables). The
Makefile loads the repository ``.env`` into the environment; nothing here reads files.

Validation errors are re-raised as :class:`ConfigurationError` whose message names each
offending variable but never includes its value: database URLs carry passwords, and
pydantic's default error text echoes the input.
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "LATTICE_"
_ASYNC_POSTGRES_SCHEME = "postgresql+asyncpg://"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ConfigurationError(Exception):
    """Configuration is missing or invalid; the process must not start."""


def parse_host_port(value: str) -> tuple[str, int]:
    """``host:port`` (IPv4 or a name; IPv6 in brackets) with a port in 1-65535."""
    host, sep, port = value.rpartition(":")
    host = host.removeprefix("[").removesuffix("]")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("must be host:port, for example 127.0.0.1:9464")
    return host, int(port)


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
    # The query-embedding provider Ask uses for semantic retrieval (the same choices as
    # `lattice sync --embedding`; its credential, if any, from ProviderSettings). "none":
    # exact + lexical retrieval only. Whether a question may be sent to it is still the
    # egress policy's decision per organization. Generation is not configured here: each
    # organization's generation provider comes from its own recorded configuration.
    query_embedding: Literal["none", "fake", "local", "gemini", "openai"] = "none"

    # --- Operations (Phase 8) ------------------------------------------------------------
    # Deployment-wide generation kill switch: false = no generation provider is built or
    # called for any organization (answers become evidence_only / generation_unavailable,
    # retrieval still runs). The per-organization stop is `lattice provider disable`.
    generation_enabled: bool = True
    # Total time one Ask may spend in generation calls (first call and the regeneration
    # together); on expiry the answer is `failed` / provider_unavailable with its
    # evidence. Raise it only for slow local models (ADR-012).
    generation_deadline_seconds: float = Field(default=120.0, ge=5.0, le=900.0)
    # Largest request body accepted (bytes); larger requests get 413 before parsing.
    max_request_bytes: int = Field(default=64 * 1024, ge=1024, le=10 * 1024 * 1024)
    # Runtime database bounds: a statement longer than this is cancelled (the request
    # fails with 503), and an unreachable database fails a request quickly.
    db_statement_timeout_ms: int = Field(default=30_000, ge=1_000, le=600_000)
    db_connect_timeout_seconds: float = Field(default=5.0, ge=0.5, le=60.0)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    # host:port of the Prometheus metrics listener (a separate port, never proxied by
    # the web app). Unset = no listener. Bind it to an internal interface only.
    metrics_address: str | None = None

    _check_url = field_validator("database_url")(_require_asyncpg_url)

    @field_validator("metrics_address")
    @classmethod
    def _check_metrics_address(cls, value: str | None) -> str | None:
        if value is not None:
            parse_host_port(value)
        return value

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
        if self.env is Environment.PRODUCTION and self.query_embedding == "fake":
            raise ValueError(
                "LATTICE_QUERY_EMBEDDING=fake is a test double and is refused when "
                "LATTICE_ENV=production"
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
    """Model-provider settings for operator tools (ingestion, search, evaluation).
    Secrets only here, from the environment; never in the database or source
    configuration. A credential here is never an approval (security-model §6): what may
    be sent to a provider is decided per organization by the egress policy.

    This is the single-deployment stop-gap; organization-owned provider credentials
    (BYOK) replace it before any multi-tenant deployment (ADR-011)."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX, extra="ignore", frozen=True, populate_by_name=True
    )

    env: Environment
    openai_api_key: SecretStr | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    # Google's documented variable name is accepted as well as Lattice's own.
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("LATTICE_GEMINI_API_KEY", "GEMINI_API_KEY")
    )
    gemini_embedding_model: str = "gemini-embedding-2"
    # unpaid | paid: the tier of the key's project (ADR-010). Lattice cannot check it;
    # unpaid (Google may use the content) is the safe default.
    gemini_data_use: Literal["unpaid", "paid"] = "unpaid"
    # Local open-weight models (ADR-009): the weights cache and the model to run.
    model_dir: Path = Path("~/.cache/lattice/models")
    local_embedding_model: str = "mxbai-embed-large-v1"
    local_embedding_threads: int = Field(default=0, ge=0, le=64)  # 0: runtime default


def load_provider_settings(**overrides: object) -> ProviderSettings:
    try:
        return ProviderSettings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc)) from None
