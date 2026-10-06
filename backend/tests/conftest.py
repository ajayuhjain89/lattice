"""Test harness. Every test runs against real PostgreSQL (the lattice_test database):
the behaviour under test -- row-level security, composite foreign keys, role
privileges, SET LOCAL semantics -- does not exist in SQLite or mocks.

Required environment (see .env.example; `make test` loads .env):
    LATTICE_TEST_DATABASE_URL            lattice_app on lattice_test
    LATTICE_TEST_MIGRATION_DATABASE_URL  lattice_owner on lattice_test
    LATTICE_TEST_SUPERUSER_DATABASE_URL  superuser on lattice_test (role-probe tests)
    LATTICE_TEST_WORKER_DATABASE_URL     lattice_worker on lattice_test (Phase 8)

At session start the schema is rebuilt from scratch (downgrade base, upgrade head),
which also proves the migration chain applies to a clean database.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.api.app import create_app
from lattice.config import OWNER_CREDENTIAL_VARIABLES, Environment, Settings
from lattice.db.engine import Database, create_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
CSRF = {"X-Lattice-Request": "1"}


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Run the tests with `make test` (which loads .env) or "
            "export the LATTICE_TEST_* variables documented in .env.example."
        )
    return value


APP_URL = _require_env("LATTICE_TEST_DATABASE_URL")
OWNER_URL = _require_env("LATTICE_TEST_MIGRATION_DATABASE_URL")
SUPERUSER_URL = _require_env("LATTICE_TEST_SUPERUSER_DATABASE_URL")
WORKER_URL = _require_env("LATTICE_TEST_WORKER_DATABASE_URL")


def alembic_config(url: str = OWNER_URL) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configure_logging"] = False
    return config


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": Environment.TEST,
        "database_url": APP_URL,
        "dev_auth_enabled": True,
        "session_cookie_secure": False,
        # Tests sign in and ask many times per test; the limits have their own tests
        # (tests/test_rate_limits.py), which set them low.
        "rate_limit_ask_per_minute": 1_000,
        "rate_limit_ask_concurrent": 20,
        "rate_limit_access_writes_per_minute": 1_000,
        "rate_limit_sign_in_per_minute": 1_000,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(scope="session", autouse=True)
async def migrated_schema() -> None:
    # env.py calls asyncio.run(), so migrations run in a worker thread.
    await asyncio.to_thread(command.downgrade, alembic_config(), "base")
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")


@pytest.fixture(scope="session")
async def owner_db(migrated_schema: None) -> AsyncIterator[Database]:
    db = Database(create_engine(OWNER_URL, application_name="lattice-tests-owner"))
    yield db
    await db.dispose()


@pytest.fixture(scope="session")
async def app_db(migrated_schema: None) -> AsyncIterator[Database]:
    db = Database(create_engine(APP_URL, application_name="lattice-tests-app"))
    yield db
    await db.dispose()


@pytest.fixture(scope="session")
async def worker_db(migrated_schema: None) -> AsyncIterator[Database]:
    db = Database(create_engine(WORKER_URL, application_name="lattice-tests-worker"))
    yield db
    await db.dispose()


@pytest.fixture(scope="session")
async def superuser_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(SUPERUSER_URL, application_name="lattice-tests-superuser")
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(owner_db: Database) -> None:
    # TRUNCATE is not subject to row-level security; the owner may use it.
    async with owner_db.engine.begin() as conn:
        await conn.execute(text("TRUNCATE organizations, users, pre_auth_rate_limits CASCADE"))


AppFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


def free_metrics_address() -> str:
    """A loopback host:port nobody listens on: production processes must serve metrics."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{probe.getsockname()[1]}"


@pytest.fixture
def production_runtime_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment of a production api or worker: no owner credential.

    ``make test`` loads the developer's ``.env``, which holds the owner credential for
    migrations; a production runtime refuses to start while any of them is set
    (``config.OWNER_CREDENTIAL_VARIABLES``), so production-mode app tests remove them.
    """
    for name in OWNER_CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def app_client() -> AppFactory:
    """Build an app with the given settings overrides, run its lifespan (including the
    runtime-role safety check) and yield an HTTP client bound to it."""

    @asynccontextmanager
    async def factory(
        configure: Callable[[FastAPI], None] | None = None, **overrides: Any
    ) -> AsyncIterator[httpx.AsyncClient]:
        app: FastAPI = create_app(make_settings(**overrides))
        if configure is not None:
            configure(app)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
                yield c

    return factory


@pytest.fixture
async def client(app_client: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client() as c:
        yield c
