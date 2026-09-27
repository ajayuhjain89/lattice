"""The API refuses to run with a database role that could bypass row-level security."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from lattice.api.app import create_app
from lattice.db.engine import create_engine
from lattice.db.role_check import (
    UnsafeDatabaseRoleError,
    inspect_runtime_role,
    verify_runtime_role,
)

from .conftest import APP_URL, OWNER_URL, SUPERUSER_URL, make_settings

PROBE_PASSWORD = "probe-password-not-a-secret"


def _url_for(role: str) -> str:
    url = make_url(APP_URL).set(username=role, password=PROBE_PASSWORD)
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def probe_role(
    superuser: AsyncEngine, name: str, attributes: str, member_of: str | None = None
) -> AsyncIterator[str]:
    """Create a throwaway login role (as superuser) and drop it afterwards."""
    database = make_url(APP_URL).database
    async with superuser.begin() as conn:
        await conn.execute(
            text(f"CREATE ROLE {name} LOGIN PASSWORD '{PROBE_PASSWORD}' {attributes}")
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE {database} TO {name}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {name}"))
        if member_of:
            await conn.execute(text(f"GRANT {member_of} TO {name} WITH INHERIT FALSE"))
    try:
        yield _url_for(name)
    finally:
        async with superuser.begin() as conn:
            await conn.execute(text(f"DROP OWNED BY {name}"))
            await conn.execute(text(f"DROP ROLE {name}"))


async def _problems(url: str) -> tuple[str, ...]:
    engine = create_engine(url, application_name="lattice-tests-probe", pool_size=1)
    try:
        return (await inspect_runtime_role(engine)).problems
    finally:
        await engine.dispose()


async def test_runtime_app_role_is_safe() -> None:
    assert await _problems(APP_URL) == ()


async def test_owner_role_is_refused() -> None:
    problems = await _problems(OWNER_URL)
    assert any("owns" in p for p in problems)


async def test_superuser_is_refused() -> None:
    problems = await _problems(SUPERUSER_URL)
    assert "role is a superuser" in problems


async def test_bypassrls_role_is_refused(superuser_engine: AsyncEngine) -> None:
    async with probe_role(superuser_engine, "lattice_probe_bypass", "BYPASSRLS") as url:
        assert "role has BYPASSRLS" in await _problems(url)


async def test_member_of_bypassrls_role_is_refused(superuser_engine: AsyncEngine) -> None:
    async with superuser_engine.begin() as conn:
        await conn.execute(text("CREATE ROLE lattice_probe_bypass_group NOLOGIN BYPASSRLS"))
    try:
        async with probe_role(
            superuser_engine, "lattice_probe_member", "", member_of="lattice_probe_bypass_group"
        ) as url:
            problems = await _problems(url)
            assert "role is a member of a superuser or BYPASSRLS role" in problems
    finally:
        async with superuser_engine.begin() as conn:
            await conn.execute(text("DROP ROLE lattice_probe_bypass_group"))


async def test_table_without_forced_rls_is_refused(superuser_engine: AsyncEngine) -> None:
    async with superuser_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE accounts NO FORCE ROW LEVEL SECURITY"))
    try:
        problems = await _problems(APP_URL)
        assert "table accounts does not have forced row-level security" in problems
    finally:
        async with superuser_engine.begin() as conn:
            await conn.execute(text("ALTER TABLE accounts FORCE ROW LEVEL SECURITY"))


async def test_api_startup_refuses_bypassrls_role(superuser_engine: AsyncEngine) -> None:
    async with probe_role(superuser_engine, "lattice_probe_startup", "BYPASSRLS") as url:
        app = create_app(make_settings(database_url=url))
        with pytest.raises(UnsafeDatabaseRoleError, match="BYPASSRLS"):
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover - startup must fail before this runs


async def test_api_startup_refuses_owner_role() -> None:
    app = create_app(make_settings(database_url=OWNER_URL))
    with pytest.raises(UnsafeDatabaseRoleError):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover


async def test_verify_passes_for_app_role(app_db: object) -> None:
    engine = create_engine(APP_URL, application_name="lattice-tests-verify", pool_size=1)
    try:
        await verify_runtime_role(engine)
    finally:
        await engine.dispose()
