"""Typed, fail-fast configuration.

Settings come only from the process environment (``LATTICE_*`` variables). The
Makefile loads the repository ``.env`` into the environment; nothing here reads files.

Validation errors are re-raised as :class:`ConfigurationError` whose message names each
offending variable but never includes its value: database URLs carry passwords, and
pydantic's default error text echoes the input.
"""

from collections.abc import Mapping
from enum import StrEnum
from ipaddress import IPv4Network, IPv6Network, ip_network
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


def parse_trusted_proxies(value: str) -> list[IPv4Network | IPv6Network]:
    """Comma-separated IP addresses or CIDR networks. A wildcard, a hostname or an empty
    item is refused: trusting forwarding headers is an explicit list of ingress peers."""
    networks: list[IPv4Network | IPv6Network] = []
    for item in value.split(","):
        item = item.strip()
        if not item or item == "*":
            raise ValueError("must list IP addresses or CIDR networks (never '*')")
        try:
            networks.append(ip_network(item, strict=False))
        except ValueError:
            raise ValueError("must list IP addresses or CIDR networks (never '*')") from None
    return networks


def _require_asyncpg_url(value: SecretStr | None) -> SecretStr | None:
    if value is not None and not value.get_secret_value().startswith(_ASYNC_POSTGRES_SCHEME):
        raise ValueError(f"must be a PostgreSQL URL starting with {_ASYNC_POSTGRES_SCHEME}")
    return value


# The schema owner's credential belongs to the migration job and operators only
# (deployment.md §5.3). A production api or worker refuses to start if any of these is in
# its environment: it would be one compromised process away from owning the schema.
OWNER_CREDENTIAL_VARIABLES = (
    "LATTICE_MIGRATION_DATABASE_URL",
    "LATTICE_OWNER_PASSWORD",
    "POSTGRES_PASSWORD",
)


def owner_credential_problem(env: Environment, environ: Mapping[str, str]) -> str | None:
    """Names (never values) of owner-credential variables a production runtime process
    must not hold; None outside production or when none is set."""
    if env is not Environment.PRODUCTION:
        return None
    present = [name for name in OWNER_CREDENTIAL_VARIABLES if environ.get(name)]
    if not present:
        return None
    return (
        f"{', '.join(present)} must not be set for the api or worker when "
        "LATTICE_ENV=production: the schema owner's credential belongs to the migration "
        "job and operators only"
    )


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

    # --- Deployment (Phase 11; ADR-016, docs/deployment.md §4) -----------------------------
    # The ingress addresses (IPs or CIDR networks, comma-separated) whose X-Forwarded-For
    # and X-Forwarded-Proto the API accepts. Unset = no forwarding header is trusted from
    # anyone: the client is the TCP peer. Never "*". Run uvicorn with --no-proxy-headers
    # so the server's own default (trusting 127.0.0.1) cannot add a second, implicit hop.
    trusted_proxies: str | None = None
    # Test double only: seconds the fake generation provider waits before answering, to
    # prove long Asks survive the ingress. Refused (must be 0) in production.
    fake_generation_delay_seconds: float = Field(default=0.0, ge=0.0, le=600.0)

    # --- Rate limits (Phase 10; lattice.ratelimit) -----------------------------------------
    # Every limit applies to every role (administrators included) and must be at least 1:
    # none can be turned off. Asks per membership per minute / per day and in flight;
    # access-management writes (and previews) per administrator per minute; SCIM requests
    # per token per minute; pre-authentication requests (development sign-in, OIDC start
    # and callback, failed SCIM authentication) per client address per minute, per process.
    rate_limit_ask_per_minute: int = Field(default=10, ge=1, le=10_000)
    rate_limit_ask_per_day: int = Field(default=500, ge=1, le=1_000_000)
    rate_limit_ask_concurrent: int = Field(default=2, ge=1, le=100)
    rate_limit_access_writes_per_minute: int = Field(default=60, ge=1, le=10_000)
    rate_limit_scim_per_minute: int = Field(default=600, ge=1, le=100_000)
    rate_limit_sign_in_per_minute: int = Field(default=30, ge=1, le=10_000)

    # --- OIDC sign-in (Phase 10; ADR-015) ----------------------------------------------------
    # The origin users reach Lattice at (the web app), e.g. https://lattice.example.com.
    # OIDC redirect URIs are built from it: <public_url>/api/v1/auth/oidc/<key>/callback.
    # Required (https) in production; defaults to the development web origin otherwise.
    public_url: str | None = None
    # Network bounds for identity provider calls (discovery, keys, token exchange).
    oidc_http_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)

    _check_url = field_validator("database_url")(_require_asyncpg_url)

    @field_validator("public_url")
    @classmethod
    def _check_public_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.rstrip("/")
        scheme, sep, rest = value.partition("://")
        if scheme not in ("http", "https") or not sep or not rest or "/" in rest or "@" in rest:
            raise ValueError("must be an origin such as https://lattice.example.com (no path)")
        return value

    @field_validator("metrics_address")
    @classmethod
    def _check_metrics_address(cls, value: str | None) -> str | None:
        if value is not None:
            parse_host_port(value)
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def _check_trusted_proxies(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return ",".join(str(network) for network in parse_trusted_proxies(value))

    @model_validator(mode="after")
    def _enforce_environment_guards(self) -> Self:
        if self.env is Environment.PRODUCTION and self.fake_generation_delay_seconds:
            raise ValueError(
                "LATTICE_FAKE_GENERATION_DELAY_SECONDS is a test setting and must be 0 when "
                "LATTICE_ENV=production"
            )
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
        if self.env is Environment.PRODUCTION and not (
            self.public_url and self.public_url.startswith("https://")
        ):
            raise ValueError(
                "LATTICE_PUBLIC_URL must be set to the https:// origin of the web app when "
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
    def web_origin(self) -> str:
        return self.public_url or "http://127.0.0.1:3000"

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


class WorkerSettings(BaseSettings):
    """Settings for the background worker (``lattice worker``; Phase 8, ADR-013)."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore", frozen=True)

    env: Environment
    # The worker's own runtime role (lattice_worker): writes ingestion tables through
    # row-level security. Checked at startup like the API's role; never lattice_owner.
    worker_database_url: SecretStr
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # The embedding provider for requested syncs (as `lattice sync --embedding`). Use
    # the provider the corpus was embedded with; `none` leaves new chunks pending.
    sync_embedding: Literal["none", "fake", "local", "gemini", "openai"] = "none"
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=300.0)
    # A running job's lease; the worker renews it while the sync runs. A job whose
    # lease expired (the worker died) is retried by another poll, up to its attempts.
    worker_lease_seconds: int = Field(default=300, ge=30, le=3600)
    # Liveness (Phase 11): a file whose modification time the worker renews at every loop
    # iteration and lease renewal; the container health check reads its age. Unset = the
    # heartbeat is only the metric.
    worker_heartbeat_file: Path | None = None
    # Statement timeout for the worker's own connections (syncs write in batches).
    db_statement_timeout_ms: int = Field(default=300_000, ge=1_000, le=3_600_000)
    db_connect_timeout_seconds: float = Field(default=5.0, ge=0.5, le=60.0)
    metrics_address: str | None = None

    _check_url = field_validator("worker_database_url")(_require_asyncpg_url)

    @field_validator("metrics_address")
    @classmethod
    def _check_metrics_address(cls, value: str | None) -> str | None:
        if value is not None:
            parse_host_port(value)
        return value

    @model_validator(mode="after")
    def _enforce_environment_guards(self) -> Self:
        if self.env is Environment.PRODUCTION and self.sync_embedding == "fake":
            raise ValueError(
                "LATTICE_SYNC_EMBEDDING=fake is a test double and is refused when "
                "LATTICE_ENV=production"
            )
        return self


def load_worker_settings(**overrides: object) -> WorkerSettings:
    try:
        return WorkerSettings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc)) from None


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
