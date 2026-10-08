"""Production migrations and deployment checks (Phase 11, P11.2; D-23).

``lattice migrate`` refuses while application sessions remain, while another migration
holds the lock, with the wrong credential and against a schema it does not know; it fences
application connections while it runs and always lifts the fence. ``lattice
check-deployment`` detects schema and privilege drift and a fence left behind.
"""

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from lattice.cli import main
from lattice.config import (
    ConfigurationError,
    Environment,
    owner_credential_problem,
)
from lattice.db.deployment import (
    Plan,
    check_deployment,
    committed_snapshot,
    snapshot_differences,
    snapshot_of,
)
from lattice.db.engine import Database
from lattice.db.migrate import (
    MIGRATION_LOCK_KEY,
    MigrationRefused,
    application_sessions,
    database_status,
    migrate,
    migration_headers,
    release_fence,
    set_fence,
    status_for,
)
from lattice.db.schema import SCHEMA_REVISION

from .conftest import APP_URL, BACKEND_DIR, OWNER_URL, SUPERUSER_URL, WORKER_URL, alembic_config

VERSIONS = BACKEND_DIR / "alembic" / "versions"


def _engine(url: str) -> AsyncEngine:
    return create_async_engine(url, poolclass=pool.NullPool)


@pytest.fixture
async def quiet_database(app_db: Database, worker_db: Database) -> AsyncIterator[None]:
    """No application session on the test database: the session-wide pools let go."""
    await app_db.engine.dispose()
    await worker_db.engine.dispose()
    yield
    await app_db.engine.dispose()
    await worker_db.engine.dispose()


async def _checks(plan: Plan | None = None) -> dict[str, tuple[bool, str]]:
    return {c.name: (c.ok, c.detail) for c in await check_deployment(OWNER_URL, plan or Plan())}


# --- migration headers -------------------------------------------------------------------


def test_every_migration_declares_reversibility_and_locks() -> None:
    headers = migration_headers(alembic_config())
    files = sorted(p.name[:4] for p in VERSIONS.glob("0*.py"))
    assert sorted(headers) == files
    assert SCHEMA_REVISION in headers
    for header in headers.values():
        assert header.locks


def test_the_header_format_is_exact() -> None:
    for path in VERSIONS.glob("0*.py"):
        text_ = path.read_text()
        assert re.search(r"^Reversible: (yes|no) \(.+\)$", text_, re.MULTILINE), path.name
        assert re.search(r"^Locks: .+$", text_, re.MULTILINE), path.name


def test_status_states() -> None:
    chain = ["0001", "0002", "0003"]
    assert status_for(None, chain).state == "unmigrated"
    assert status_for("0003", chain).state == "current"
    pending = status_for("0001", chain)
    assert (pending.state, pending.pending) == ("pending", ("0002", "0003"))
    assert status_for("9999", chain).state == "ahead"


def test_a_downgrade_is_planned_only_over_reversible_revisions() -> None:
    from lattice.db.migrate import Header, undo_plan

    def headers(*reversible: bool) -> dict[str, Header]:
        revs = ["0011", "0012", SCHEMA_REVISION]
        return {r: Header(r, flag, "x") for r, flag in zip(revs, reversible, strict=True)}

    chain = ["0011", "0012", SCHEMA_REVISION]
    current = status_for(SCHEMA_REVISION, chain)
    assert undo_plan(current, "0011", headers(True, True, True)) == (SCHEMA_REVISION, "0012")
    with pytest.raises(MigrationRefused, match=f"{SCHEMA_REVISION} declare no data-safe"):
        undo_plan(current, "0012", headers(True, True, False))
    with pytest.raises(MigrationRefused, match="not an older revision"):
        undo_plan(current, SCHEMA_REVISION, headers(True, True, True))
    with pytest.raises(MigrationRefused, match="starts from this build's revision"):
        undo_plan(status_for("0012", chain), "0011", headers(True, True, True))


async def test_migrate_refuses_to_downgrade_an_irreversible_revision(
    quiet_database: None,
) -> None:
    with pytest.raises(MigrationRefused, match="roll forward"):
        await migrate(OWNER_URL, lock_wait_seconds=1, report=lambda _: None, downgrade_to="0012")
    status, _, _ = await database_status(OWNER_URL)
    assert status.state == "current"


# --- lattice migrate ---------------------------------------------------------------------


async def test_migrate_succeeds_when_no_application_session_remains(
    quiet_database: None,
) -> None:
    outcome = await migrate(OWNER_URL, lock_wait_seconds=1, report=lambda _: None)
    assert (outcome.before, outcome.after, outcome.applied) == (
        SCHEMA_REVISION,
        SCHEMA_REVISION,
        (),
    )
    assert (await _checks())["database access"][0] is True


@pytest.mark.parametrize("url", [APP_URL, WORKER_URL], ids=["api", "worker"])
async def test_migrate_refuses_while_an_application_session_is_open(
    quiet_database: None, url: str
) -> None:
    engine = _engine(url)
    try:
        async with engine.connect() as held:
            await held.execute(text("SELECT 1"))
            with pytest.raises(MigrationRefused, match="still connected"):
                await migrate(OWNER_URL, lock_wait_seconds=1, report=lambda _: None)
    finally:
        await engine.dispose()
    # The refusal lifted its fence: the application roles can connect again.
    checks = await _checks()
    assert checks["database access"] == (True, checks["database access"][1])


async def test_migrate_applies_pending_revisions_and_verifies(quiet_database: None) -> None:
    head = migration_headers()[SCHEMA_REVISION]
    assert head.reversible is False  # the head cannot be downgraded safely in production
    below = f"{int(SCHEMA_REVISION) - 1:04d}"
    await asyncio.to_thread(command.downgrade, alembic_config(), below)
    try:
        status, _, _ = await database_status(OWNER_URL)
        assert (status.state, status.pending) == ("pending", (SCHEMA_REVISION,))
        lines: list[str] = []
        outcome = await migrate(OWNER_URL, lock_wait_seconds=1, report=lines.append)
    finally:
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    assert (outcome.before, outcome.after, outcome.applied) == (
        below,
        SCHEMA_REVISION,
        (SCHEMA_REVISION,),
    )
    assert lines[0] == "lock: acquired"
    assert "fence: lifted" in lines
    assert any(line.startswith("verify:") for line in lines)


async def test_migrate_builds_a_fresh_database(quiet_database: None) -> None:
    """A new deployment: the bootstrap made an empty database; `lattice migrate` applies
    the whole chain and verifies it."""
    await asyncio.to_thread(command.downgrade, alembic_config(), "base")
    try:
        status, _, _ = await database_status(OWNER_URL)
        assert (status.state, status.database_revision) == ("unmigrated", None)
        assert (await _checks())["schema revision"][0] is False
        outcome = await migrate(OWNER_URL, lock_wait_seconds=1, report=lambda _: None)
    finally:
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    assert (outcome.before, outcome.after) == (None, SCHEMA_REVISION)
    assert outcome.applied[0] == "0001"
    assert outcome.applied[-1] == SCHEMA_REVISION


async def test_a_concurrent_migration_is_refused(quiet_database: None) -> None:
    engine = _engine(OWNER_URL)
    try:
        async with engine.connect() as holder:
            await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
            _, _, locked = await database_status(OWNER_URL)
            assert locked == 1
            with pytest.raises(MigrationRefused, match="another migration holds"):
                await migrate(OWNER_URL, lock_wait_seconds=0.5, report=lambda _: None)
            await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("url", "reason"),
    [(APP_URL, "does not own the public schema"), (SUPERUSER_URL, "superuser or has BYPASSRLS")],
    ids=["runtime-credential", "superuser"],
)
async def test_migrate_refuses_the_wrong_credential(url: str, reason: str) -> None:
    with pytest.raises(MigrationRefused, match=reason):
        await migrate(url, lock_wait_seconds=0.5, report=lambda _: None)


async def test_migrate_refuses_a_schema_this_build_does_not_know(quiet_database: None) -> None:
    engine = _engine(OWNER_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE alembic_version SET version_num = '9999'"))
        with pytest.raises(MigrationRefused, match="roll forward"):
            await migrate(OWNER_URL, lock_wait_seconds=1, report=lambda _: None)
        assert (await _checks())["schema revision"][0] is False
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE alembic_version SET version_num = :v"), {"v": SCHEMA_REVISION}
            )
        await engine.dispose()


async def test_the_fence_blocks_new_application_connections(quiet_database: None) -> None:
    owner = _engine(OWNER_URL)
    app = _engine(APP_URL)
    try:
        async with owner.connect() as conn:
            await set_fence(conn, closed=True)
        with pytest.raises(Exception, match="permission denied for database"):
            async with app.connect():
                pass
        # A migration killed at this point leaves the fence: reported, then released.
        detail = (await _checks())["database access"]
        assert detail[0] is False
        assert "migration fence" in detail[1]
        assert await release_fence(OWNER_URL) == ["lattice_app", "lattice_worker"]
        async with app.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        async with owner.connect() as conn:
            await set_fence(conn, closed=False)
        await owner.dispose()
        await app.dispose()


async def test_the_owner_sees_application_sessions_of_other_roles(quiet_database: None) -> None:
    """The pre-flight works without pg_read_all_stats (a managed admin may not grant it):
    role and application name of another role's session are visible to every role."""
    app = _engine(APP_URL)
    owner = _engine(OWNER_URL)
    try:
        async with app.connect() as held, owner.connect() as conn:
            await held.execute(text("SELECT 1"))
            sessions = await application_sessions(conn)
        assert [(s.role, s.count) for s in sessions] == [("lattice_app", 1)]
    finally:
        await app.dispose()
        await owner.dispose()


# --- lattice check-deployment ------------------------------------------------------------


async def test_check_deployment_passes_on_a_freshly_migrated_database() -> None:
    checks = await _checks()
    assert all(ok for ok, _ in checks.values()), checks


async def test_the_committed_privilege_snapshot_is_current() -> None:
    """Regenerate with: lattice check-deployment --write-privilege-snapshot
    src/lattice/db/privilege_snapshot.json (against a freshly migrated database)."""
    differences = snapshot_differences(committed_snapshot(), await snapshot_of(OWNER_URL))
    assert differences == []


async def test_privilege_drift_is_detected(superuser_engine: AsyncEngine) -> None:
    async with superuser_engine.begin() as conn:
        await conn.execute(text("GRANT SELECT ON audit_events TO lattice_worker"))
    try:
        ok, detail = (await _checks())["privilege snapshot"]
        assert ok is False
        assert "lattice_worker" in detail
        assert "audit_events" in detail
    finally:
        async with superuser_engine.begin() as conn:
            await conn.execute(text("REVOKE SELECT ON audit_events FROM lattice_worker"))


async def test_schema_drift_and_unforced_rls_are_detected() -> None:
    engine = _engine(OWNER_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE drift_probe (id integer PRIMARY KEY)"))
        checks = await _checks()
        assert checks["schema matches the models"][0] is False
        rls = checks["forced row-level security"]
        assert rls == (False, "drift_probe lacks enabled+forced RLS")
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS drift_probe"))
        await engine.dispose()


async def test_the_connection_budget_is_checked() -> None:
    assert (await _checks(Plan(api_instances=2)))["connection budget"][0] is True
    ok, detail = (await _checks(Plan(api_instances=20, api_pool_size=5)))["connection budget"]
    assert ok is False
    assert "plan needs" in detail


# --- credential separation and configuration ---------------------------------------------


def test_production_runtime_processes_refuse_the_owner_credential() -> None:
    environ = {"LATTICE_MIGRATION_DATABASE_URL": "postgresql+asyncpg://x", "OTHER": "1"}
    problem = owner_credential_problem(Environment.PRODUCTION, environ)
    assert problem is not None
    assert "LATTICE_MIGRATION_DATABASE_URL" in problem
    assert "postgresql" not in problem  # names only, never values
    assert owner_credential_problem(Environment.TEST, environ) is None
    assert owner_credential_problem(Environment.PRODUCTION, {"OTHER": "1"}) is None


def test_create_app_refuses_the_owner_credential_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lattice.api.app import create_app

    from .conftest import make_settings

    monkeypatch.setenv("LATTICE_OWNER_PASSWORD", "x")
    settings = make_settings(
        env=Environment.PRODUCTION,
        dev_auth_enabled=False,
        session_cookie_secure=True,
        public_url="https://lattice.example.com",
        client_key_secret="k" * 32,
        metrics_address="127.0.0.1:9464",
    )
    with pytest.raises(ConfigurationError, match="LATTICE_OWNER_PASSWORD"):
        create_app(settings)


def _production_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LATTICE_MIGRATION_DATABASE_URL", "LATTICE_OWNER_PASSWORD", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LATTICE_ENV", "production")
    monkeypatch.setenv("LATTICE_DATABASE_URL", APP_URL)
    monkeypatch.setenv("LATTICE_PUBLIC_URL", "https://lattice.example.com")
    monkeypatch.setenv("LATTICE_DEV_AUTH_ENABLED", "false")
    monkeypatch.setenv("LATTICE_SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("LATTICE_CLIENT_KEY_SECRET", "k" * 32)
    monkeypatch.setenv("LATTICE_METRICS_ADDRESS", "127.0.0.1:9464")


def test_check_config_accepts_a_valid_production_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _production_api_env(monkeypatch)
    assert main(["check-config", "api"]) == 0
    assert "OK: api configuration is valid (LATTICE_ENV=production)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (("LATTICE_PUBLIC_URL", None), "LATTICE_PUBLIC_URL"),
        (("LATTICE_DATABASE_URL", None), "LATTICE_DATABASE_URL"),
        (("LATTICE_DEV_AUTH_ENABLED", "true"), "LATTICE_DEV_AUTH_ENABLED"),
        (("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL), "LATTICE_MIGRATION_DATABASE_URL"),
    ],
    ids=["no-public-url", "no-database", "dev-sign-in", "owner-credential"],
)
def test_check_config_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change: tuple[str, str | None],
    expected: str,
) -> None:
    _production_api_env(monkeypatch)
    name, value = change
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    assert main(["check-config", "api"]) == 2
    err = capsys.readouterr().err
    assert expected in err
    assert "postgresql+asyncpg://" not in err  # never a value


def test_check_config_worker_refuses_the_owner_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LATTICE_ENV", "production")
    monkeypatch.setenv("LATTICE_WORKER_DATABASE_URL", WORKER_URL)
    monkeypatch.setenv("LATTICE_METRICS_ADDRESS", "127.0.0.1:9465")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    assert main(["check-config", "worker"]) == 2
    monkeypatch.delenv("POSTGRES_PASSWORD")
    monkeypatch.delenv("LATTICE_MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.delenv("LATTICE_OWNER_PASSWORD", raising=False)
    assert main(["check-config", "worker"]) == 0


def test_version_reports_the_build_without_a_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LATTICE_BUILD_REVISION", "0123abc")
    assert main(["version", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "revision": "0123abc",
        "schema_revision": SCHEMA_REVISION,
        "version": "unknown",
    }


def test_migrate_status_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    assert main(["migrate", "--status", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "current"
    assert report["build_revision"] == SCHEMA_REVISION
    assert report["migration_running"] is False


def test_a_downgrade_check_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LATTICE_MIGRATION_DATABASE_URL", OWNER_URL)
    assert main(["migrate", "--downgrade-to", "0012", "--check"]) == 1
    assert "declare no data-safe downgrade" in capsys.readouterr().err


def test_the_snapshot_resource_ships_with_the_package() -> None:
    path = BACKEND_DIR / "src" / "lattice" / "db" / "privilege_snapshot.json"
    assert json.loads(path.read_text()) == committed_snapshot()


async def test_secret_keys_are_compared_by_id(owner_db: Database, quiet_database: None) -> None:
    """P12.1 (C3.3): key IDs in use must be configured; material is never read."""
    from pydantic import SecretStr

    from lattice.db.deployment import run_checks
    from lattice.vault.keys import Keyring
    from lattice.vault.records import store_secret

    from .factories import create_org

    assert (await _checks())["secret keys"] == (True, "no encrypted credential stored")
    org = await create_org(owner_db, "halden")
    key = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="
    keyring = Keyring.from_environment(
        {"LATTICE_SECRET_KEYS": f"k1:{key}", "LATTICE_SECRET_ACTIVE_KEY": "k1"}
    )
    async with owner_db.tenant_transaction(org) as s:
        await store_secret(
            s,
            keyring,
            organization_id=org,
            owner_kind="provider_api_key",
            owner_id=uuid.uuid4(),
            value=SecretStr("sk-test-value"),
        )
    await owner_db.engine.dispose()

    async def run(environ: dict[str, str]) -> tuple[bool, str]:
        engine = _engine(OWNER_URL)
        try:
            async with engine.connect() as conn:
                found = {c.name: (c.ok, c.detail) for c in await run_checks(conn, Plan(), environ)}
        finally:
            await engine.dispose()
        return found["secret keys"]

    assert await run({"LATTICE_SECRET_KEYS": f"k1:{key}"}) == (
        True,
        "key ID(s) in use: k1; all configured",
    )
    ok, problem = await run({"LATTICE_SECRET_KEYS": f"k2:{key}"})
    assert ok is False
    assert "k1" in problem
    assert key not in problem
    ok, note = await run({})
    assert ok is True
    assert "not compared" in note
