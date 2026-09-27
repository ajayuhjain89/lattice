"""Test harness. Every test runs against real PostgreSQL (the lattice_test database):
the behaviour under test -- row-level security, composite foreign keys, role
privileges, SET LOCAL semantics -- does not exist in SQLite or mocks.

Required environment (see .env.example; `make test` loads .env):
    LATTICE_TEST_DATABASE_URL            lattice_app on lattice_test
    LATTICE_TEST_MIGRATION_DATABASE_URL  lattice_owner on lattice_test
    LATTICE_TEST_SUPERUSER_DATABASE_URL  superuser on lattice_test (role-probe tests)

At session start the schema is rebuilt from scratch (downgrade base, upgrade head),
which also proves the migration chain applies to a clean database.
"""

import asyncio
import os
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
from lattice.config import Environment, Settings
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
async def superuser_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(SUPERUSER_URL, application_name="lattice-tests-superuser")
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(owner_db: Database) -> None:
    # TRUNCATE is not subject to row-level security; the owner may use it.
    async with owner_db.engine.begin() as conn:
        await conn.execute(text("TRUNCATE organizations, users CASCADE"))


AppFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


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
