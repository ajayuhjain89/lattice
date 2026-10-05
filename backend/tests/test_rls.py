"""Organization isolation through forced row-level security (SEC-2).

With app.org_id bound to organization A, organization B's rows must behave as if they
do not exist; with no organization bound, no tenant rows are visible at all.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database, bind_organization, create_engine
from lattice.identity.sessions import SessionPolicy, SessionService

from .conftest import APP_URL
from .factories import (
    FIXTURE_DOCUMENT_COLUMNS,
    Member,
    create_member,
    create_org,
    create_source,
    fixture_document_values,
)

TENANT_TABLES = (
    "memberships",
    "departments",
    "membership_departments",
    "accounts",
    "account_assignments",
    "sessions",
    "access_groups",
    "access_group_members",
    "documents",
    "document_grants",
    # Phase 4
    "sources",
    "source_sync_runs",
    "sync_record_failures",
    "chunks",
    "org_settings",
    "model_providers",
    # Phase 6
    "provider_credentials",
    "query_traces",
    "audit_events",
    # Phase 8
    "jobs",
    # Phase 10
    "oidc_connections",
    "oidc_login_attempts",
    "directory_identities",
    "scim_tokens",
    "rate_limit_counters",
    # Phase 11 (P11.4)
    "ask_slots",
)
# Tables the runtime role may not read (written and read by operator tools; of
# oidc_connections it reads only id, organization_id and status since 0014).
RUNTIME_UNREADABLE = frozenset({"audit_events", "oidc_connections", "scim_tokens"})
GLOBAL_TABLES = ("organizations", "users", "external_identities")
ALL_TABLES = (*GLOBAL_TABLES, *TENANT_TABLES)
# No organization and no login role's privilege: reached only through lattice_auth
# functions (P11.4); forced RLS with a lattice_auth policy only (tests/test_shared_limits.py).
PRE_AUTH_TABLES = ("pre_auth_rate_limits",)


@dataclass(frozen=True)
class TwoOrgs:
    a: Member
    b: Member


async def _populate(db: Database, member: Member) -> None:
    source = await create_source(db, member.organization_id)
    async with db.tenant_transaction(member.organization_id) as s:
        dept: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO departments (organization_id, key, name) "
                    "VALUES (:o, 'sales', 'Sales') RETURNING id"
                ),
                {"o": member.organization_id},
            )
        ).scalar_one()
        # Deliberately identical account keys and names in both organizations.
        account: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO accounts (organization_id, key, name) "
                    "VALUES (:o, 'acme-corp', 'Acme Corp') RETURNING id"
                ),
                {"o": member.organization_id},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO membership_departments (organization_id, membership_id, department_id)"
                " VALUES (:o, :m, :d)"
            ),
            {"o": member.organization_id, "m": member.membership_id, "d": dept},
        )
        await s.execute(
            text(
                "INSERT INTO account_assignments (organization_id, membership_id, account_id) "
                "VALUES (:o, :m, :a)"
            ),
            {"o": member.organization_id, "m": member.membership_id, "a": account},
        )
        group: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO access_groups (organization_id, key, origin, name) "
                    "VALUES (:o, 'exec-staff', 'admin', 'Exec staff') RETURNING id"
                ),
                {"o": member.organization_id},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO access_group_members (organization_id, group_id, membership_id) "
                "VALUES (:o, :g, :m)"
            ),
            {"o": member.organization_id, "g": group, "m": member.membership_id},
        )
        document: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO documents (organization_id, account_id, sensitivity, "  # noqa: S608
                    f"access_mode, department_ids, {FIXTURE_DOCUMENT_COLUMNS}) "
                    "VALUES (:o, :a, 'restricted', 'grants_only', ARRAY[CAST(:d AS uuid)], "
                    f"{fixture_document_values(':o')}) RETURNING id"
                ),
                {"o": member.organization_id, "a": account, "d": dept},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO document_grants (organization_id, document_id, principal_type, "
                "group_id, origin) VALUES (:o, :d, 'group', :g, 'admin')"
            ),
            {"o": member.organization_id, "d": document, "g": group},
        )
        # Phase 4: one row per organization in each ingestion table.
        await s.execute(
            text(
                "INSERT INTO chunks (id, organization_id, document_id, ordinal, text, "
                "char_start, char_end, content_hash, embedding_input_hash, token_count, tsv, "
                "embedding_status) VALUES (gen_random_uuid(), :o, :d, 0, 'Fixture', 0, 7, "
                "sha256('a'::bytea), sha256('b'::bytea), 2, to_tsvector('Fixture'), "
                "'policy_excluded')"
            ),
            {"o": member.organization_id, "d": document},
        )
        run: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO source_sync_runs (organization_id, source_id, mode) "
                    "VALUES (:o, :s, 'full') RETURNING id"
                ),
                {"o": member.organization_id, "s": source},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO sync_record_failures (organization_id, sync_run_id, opaque_ref, "
                "stage, error_code) VALUES (:o, :r, 'rec_0123456789abcdef', 'parse', "
                "'malformed_json')"
            ),
            {"o": member.organization_id, "r": run},
        )
        await s.execute(
            text("INSERT INTO org_settings (organization_id) VALUES (:o)"),
            {"o": member.organization_id},
        )
        await s.execute(
            text(
                "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                "deployment, retention_mode, training_opt_out) "
                "VALUES (:o, 'embedding', 'fake', 'fake-model', 'external_api', 'none', true)"
            ),
            {"o": member.organization_id},
        )
        await s.execute(
            text(
                "INSERT INTO provider_credentials (organization_id, provider, secret_ref) "
                "VALUES (:o, 'gemini', 'env:LATTICE_PROVIDER_SECRET_TEST')"
            ),
            {"o": member.organization_id},
        )
        await s.execute(
            text(
                "INSERT INTO query_traces (organization_id, membership_id, question_text, "
                "question_sensitivity, authz_snapshot, retrieval, egress, validation, status, "
                "timings_ms) VALUES (:o, :m, 'q', 'internal', '{}', '{}', '{}', '{}', "
                "'answered', '{}')"
            ),
            {"o": member.organization_id, "m": member.membership_id},
        )
        await s.execute(
            text(
                "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
                "VALUES (:o, 'test', 'provider.configured', '{}', 'test')"
            ),
            {"o": member.organization_id},
        )
        await s.execute(
            text(
                "INSERT INTO jobs (organization_id, kind, source_id, requested_by) "
                "VALUES (:o, 'source_sync', :s, :m)"
            ),
            {"o": member.organization_id, "s": source, "m": member.membership_id},
        )
        # Phase 10: identity provider connection, sign-in attempt, directory identity,
        # SCIM token and a rate-limit counter.
        connection: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO oidc_connections (organization_id, login_key, issuer, "
                    "client_id) VALUES (:o, :k, 'https://idp.example', 'lattice') RETURNING id"
                ),
                {"o": member.organization_id, "k": uuid.uuid4().hex},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO oidc_login_attempts (organization_id, connection_id, state_hash, "
                "binding_hash, nonce, code_verifier, expires_at) VALUES (:o, :c, "
                "sha256(convert_to(:k, 'UTF8')), sha256('b'::bytea), repeat('n', 22), "
                "repeat('v', 43), now() + interval '10 minutes')"
            ),
            {"o": member.organization_id, "c": connection, "k": uuid.uuid4().hex},
        )
        await s.execute(
            text(
                "INSERT INTO directory_identities (organization_id, connection_id, "
                "membership_id, external_id, user_name) VALUES (:o, :c, :m, 'ext-1', 'user')"
            ),
            {"o": member.organization_id, "c": connection, "m": member.membership_id},
        )
        await s.execute(
            text(
                "INSERT INTO scim_tokens (organization_id, connection_id, token_hash) "
                "VALUES (:o, :c, sha256(convert_to(:k, 'UTF8')))"
            ),
            {"o": member.organization_id, "c": connection, "k": uuid.uuid4().hex},
        )
        await s.execute(
            text(
                "INSERT INTO rate_limit_counters (organization_id, bucket, subject, "
                "window_start, count) VALUES (:o, 'ask_minute', :m, now(), 1)"
            ),
            {"o": member.organization_id, "m": member.membership_id},
        )
        await s.execute(
            text(
                "INSERT INTO ask_slots (organization_id, membership_id, slot, lease_id, "
                "expires_at) VALUES (:o, :m, 0, gen_random_uuid(), now() + interval '1 minute')"
            ),
            {"o": member.organization_id, "m": member.membership_id},
        )


_HOUR = timedelta(hours=1)


@pytest.fixture
async def two_orgs(owner_db: Database, app_db: Database) -> TwoOrgs:
    a = await create_member(owner_db, await create_org(owner_db, "org-a"), "a@a.example")
    b = await create_member(owner_db, await create_org(owner_db, "org-b"), "b@b.example")
    await _populate(owner_db, a)
    await _populate(owner_db, b)
    sessions = SessionService(app_db, SessionPolicy(idle_timeout=_HOUR, absolute_timeout=_HOUR))
    await sessions.create(user_id=a.user_id, organization_id=a.organization_id)
    await sessions.create(user_id=b.user_id, organization_id=b.organization_id)
    return TwoOrgs(a, b)


async def _count(db: Database, table: str, org: uuid.UUID | None) -> int:
    if org is None:
        async with db.unscoped_transaction() as s:
            return int((await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())  # noqa: S608
    async with db.tenant_transaction(org) as s:
        return int((await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())  # noqa: S608


async def test_every_application_table_has_rls_enabled_and_forced(app_db: Database) -> None:
    async with app_db.unscoped_transaction() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                    "AND c.relname <> 'alembic_version'"
                )
            )
        ).all()
    assert {r.relname for r in rows} == set(ALL_TABLES) | set(PRE_AUTH_TABLES), (
        "new table? add it to the RLS tests"
    )
    for name, enabled, forced in rows:
        assert enabled, f"{name}: RLS not enabled"
        assert forced, f"{name}: RLS not forced"


async def test_every_table_with_organization_id_has_the_tenant_policy(owner_db: Database) -> None:
    # As the owner: information_schema lists only tables the role holds privileges on.
    async with owner_db.unscoped_transaction() as s:
        tables: set[str] = set(
            (
                await s.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_name = 'organization_id'"
                    )
                )
            ).scalars()
        )
        with_policy: set[str] = set(
            (
                await s.execute(
                    text(
                        "SELECT polrelid::regclass::text FROM pg_policy "
                        "WHERE polname = 'tenant_isolation'"
                    )
                )
            ).scalars()
        )
    assert tables == set(TENANT_TABLES)
    assert tables <= with_policy


@pytest.mark.parametrize("table", ALL_TABLES)
async def test_org_a_sees_only_its_own_rows(
    app_db: Database, owner_db: Database, two_orgs: TwoOrgs, table: str
) -> None:
    db = owner_db if table in RUNTIME_UNREADABLE else app_db
    both = await _count(db, table, two_orgs.a.organization_id) + await _count(
        db, table, two_orgs.b.organization_id
    )
    assert await _count(db, table, two_orgs.a.organization_id) == 1
    assert both == 2


async def test_org_b_rows_cannot_be_selected_by_id_from_org_a(
    app_db: Database, two_orgs: TwoOrgs
) -> None:
    b = two_orgs.b
    async with app_db.tenant_transaction(two_orgs.a.organization_id) as s:
        for sql, param in (
            ("SELECT 1 FROM organizations WHERE id = :x", b.organization_id),
            ("SELECT 1 FROM memberships WHERE id = :x", b.membership_id),
            ("SELECT 1 FROM users WHERE id = :x", b.user_id),
            ("SELECT 1 FROM users WHERE email = :x", b.email),
            ("SELECT 1 FROM external_identities WHERE user_id = :x", b.user_id),
            ("SELECT 1 FROM sessions WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM accounts WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM documents WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM document_grants WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM access_groups WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM access_group_members WHERE membership_id = :x", b.membership_id),
            ("SELECT 1 FROM sources WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM chunks WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM source_sync_runs WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM sync_record_failures WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM org_settings WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM model_providers WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM provider_credentials WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM query_traces WHERE organization_id = :x", b.organization_id),
            ("SELECT 1 FROM query_traces WHERE membership_id = :x", b.membership_id),
            ("SELECT 1 FROM jobs WHERE organization_id = :x", b.organization_id),
        ):
            assert (await s.execute(text(sql), {"x": param})).first() is None, sql


@pytest.mark.parametrize("table", ALL_TABLES)
async def test_no_organization_context_exposes_no_rows(
    app_db: Database, owner_db: Database, two_orgs: TwoOrgs, table: str
) -> None:
    db = owner_db if table in RUNTIME_UNREADABLE else app_db
    assert await _count(db, table, None) == 0


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_rls_is_forced_for_the_table_owner_too(
    owner_db: Database, two_orgs: TwoOrgs, table: str
) -> None:
    # The owner has provisioning access to the global tables only.
    assert await _count(owner_db, table, None) == 0
    assert await _count(owner_db, table, two_orgs.a.organization_id) == 1


async def test_cannot_write_rows_into_another_organization(
    app_db: Database, two_orgs: TwoOrgs
) -> None:
    b = two_orgs.b
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_db.tenant_transaction(two_orgs.a.organization_id) as s:
            await s.execute(
                text(
                    "INSERT INTO sessions (token_hash, user_id, organization_id, expires_at) "
                    "VALUES (:h, :u, :o, now() + interval '1 hour')"
                ),
                {"h": b"\x01" * 32, "u": b.user_id, "o": b.organization_id},
            )


async def test_cannot_update_another_organizations_sessions(
    app_db: Database, two_orgs: TwoOrgs
) -> None:
    async with app_db.tenant_transaction(two_orgs.a.organization_id) as s:
        result = await s.execute(
            text("UPDATE sessions SET revoked_at = now() WHERE organization_id = :o"),
            {"o": two_orgs.b.organization_id},
        )
        assert result.rowcount == 0  # type: ignore[attr-defined]
    async with app_db.tenant_transaction(two_orgs.b.organization_id) as s:
        revoked: int = (
            await s.execute(text("SELECT count(*) FROM sessions WHERE revoked_at IS NOT NULL"))
        ).scalar_one()
    assert revoked == 0


@pytest.mark.parametrize(
    "statement",
    [
        # Phase 10: provisioning inserts memberships with their least-access defaults
        # only (column privileges); the row-level limits are tests/test_app_role_access.py.
        "INSERT INTO memberships (organization_id, user_id, is_admin) VALUES "
        "(lattice_current_org_id(), gen_random_uuid(), true)",
        "INSERT INTO memberships (organization_id, user_id, clearance) VALUES "
        "(lattice_current_org_id(), gen_random_uuid(), 'confidential')",
        "INSERT INTO memberships (organization_id, user_id, role_label) VALUES "
        "(lattice_current_org_id(), gen_random_uuid(), 'x')",
        "UPDATE memberships SET is_admin = true",
        "DELETE FROM sessions",
        "UPDATE sessions SET organization_id = organization_id",
        "UPDATE users SET status = 'disabled'",
        "UPDATE users SET home_organization_id = NULL",
        "DELETE FROM users",
        "INSERT INTO external_identities (user_id, issuer, subject) VALUES "
        "(gen_random_uuid(), 'dev', 'x')",
        # Phase 10: identity provider configuration is the operator's.
        "INSERT INTO oidc_connections (organization_id, login_key, issuer, client_id) VALUES "
        "(lattice_current_org_id(), 'kkkkkkkkkkkkkkkkkkkkkk', 'https://i.example', 'c')",
        # 0014: only id, organization_id and status (the session-creation check, D-25).
        "SELECT client_id FROM oidc_connections",
        "SELECT issuer, client_secret_ref, login_key FROM oidc_connections",
        "SELECT * FROM oidc_connections",
        "SELECT 1 FROM scim_tokens",
        "UPDATE scim_tokens SET revoked_at = NULL",
        "UPDATE directory_identities SET external_id = 'x'",
        "UPDATE directory_identities SET membership_id = membership_id",
        "DELETE FROM directory_identities",
        "UPDATE oidc_login_attempts SET nonce = 'x'",
        "DELETE FROM rate_limit_counters",
        "UPDATE audit_events SET txid = txid",
        "ALTER TABLE accounts DISABLE ROW LEVEL SECURITY",
        "SET ROLE lattice_auth",
        # Phase 2: authorization facts are read-only at runtime.
        "INSERT INTO access_groups (organization_id, key, origin, name) "
        "VALUES (lattice_current_org_id(), 'x', 'admin', 'x')",
        "INSERT INTO documents (organization_id, sensitivity, access_mode, "  # noqa: S608
        f"{FIXTURE_DOCUMENT_COLUMNS}) VALUES (lattice_current_org_id(), 'internal', 'policy', "
        f"{fixture_document_values('lattice_current_org_id()')})",
        "UPDATE documents SET access_mode = 'policy'",
        "UPDATE documents SET sensitivity = 'internal'",
        "DELETE FROM documents",
        "UPDATE document_grants SET membership_id = NULL",
        "ALTER TABLE documents NO FORCE ROW LEVEL SECURITY",
        # Phase 4: ingestion is an operator tool (lattice_owner); the runtime role only
        # reads the corpus and its operational history.
        "INSERT INTO sources (organization_id, key, connector_type, name, "
        "default_sensitivity, authority) VALUES (lattice_current_org_id(), 'x', "
        "'ticketing', 'x', 'internal', 'working')",
        "UPDATE sources SET status = 'active'",
        "UPDATE documents SET body = ''",
        "DELETE FROM chunks",
        "UPDATE chunks SET embedding = NULL",
        "UPDATE chunks SET text = ''",
        "DELETE FROM source_sync_runs",
        "DELETE FROM sync_record_failures",
        "UPDATE org_settings SET external_egress_level = 'restricted'",
        "INSERT INTO org_settings (organization_id) VALUES (lattice_current_org_id())",
        "UPDATE model_providers SET approved_for_level = 'restricted'",
        # Phase 6: provider configuration and credentials are administrative writes
        # (operator tools); traces are append-only for the runtime; audit events are
        # append-only (Phase 9: the runtime may insert access events only).
        "UPDATE model_providers SET enabled = true",
        "INSERT INTO model_providers (organization_id, purpose, provider, model, deployment, "
        "retention_mode, training_opt_out) VALUES (lattice_current_org_id(), 'generation', "
        "'gemini', 'm', 'external_api', 'none', true)",
        "INSERT INTO provider_credentials (organization_id, provider, secret_ref) VALUES "
        "(lattice_current_org_id(), 'gemini', 'env:LATTICE_PROVIDER_SECRET_X')",
        "UPDATE provider_credentials SET secret_ref = 'env:LATTICE_PROVIDER_SECRET_Y'",
        "UPDATE provider_credentials SET status = 'revoked', revoked_at = now()",
        "DELETE FROM provider_credentials",
        "UPDATE query_traces SET answer = NULL",
        "UPDATE query_traces SET question_text = ''",
        "DELETE FROM query_traces",
        "DELETE FROM audit_events",
        "UPDATE audit_events SET reason = 'x'",
        # Phase 9: access management writes only what migration 0012 grants (the exact
        # set, and its row-level limits, are tests/test_app_role_access.py).
        "UPDATE memberships SET role_label = 'x'",
        "UPDATE memberships SET user_id = user_id",
        "DELETE FROM memberships",
        "UPDATE document_grants SET origin = 'admin'",
        "UPDATE access_group_members SET group_id = group_id",
        "UPDATE account_assignments SET account_id = account_id",
        "INSERT INTO accounts (organization_id, key, name) VALUES (lattice_current_org_id(), "
        "'x', 'x')",
        "INSERT INTO departments (organization_id, key, name) VALUES "
        "(lattice_current_org_id(), 'x', 'x')",
    ],
)
async def test_runtime_role_lacks_unneeded_privileges(
    app_db: Database, two_orgs: TwoOrgs, statement: str
) -> None:
    with pytest.raises(DBAPIError, match=r"permission denied|must be owner"):
        async with app_db.tenant_transaction(two_orgs.a.organization_id) as s:
            await s.execute(text(statement))


# --- Transaction / connection-pool lifecycle -------------------------------------------


@pytest.fixture
async def single_connection_db() -> AsyncIterator[Database]:
    """pool_size=1, max_overflow=1 but used sequentially: every transaction reuses the
    same physical connection, which is exactly where a leaked setting would show."""
    db = Database(create_engine(APP_URL, application_name="lattice-tests-pool", pool_size=1))
    yield db
    await db.dispose()


async def _backend_pid_and_org(db: Database) -> tuple[int, uuid.UUID | None, str | None]:
    async with db.unscoped_transaction() as s:
        row = (
            await s.execute(
                text(
                    "SELECT pg_backend_pid(), lattice_current_org_id(), "
                    "current_setting('app.org_id', true)"
                )
            )
        ).one()
    return int(row[0]), row[1], row[2]


async def test_binding_is_discarded_after_commit_on_a_reused_connection(
    single_connection_db: Database, two_orgs: TwoOrgs
) -> None:
    pid_before, _, _ = await _backend_pid_and_org(single_connection_db)
    async with single_connection_db.tenant_transaction(two_orgs.a.organization_id) as s:
        assert (await s.execute(text("SELECT count(*) FROM memberships"))).scalar_one() == 1
    pid_after, org, raw = await _backend_pid_and_org(single_connection_db)
    assert pid_after == pid_before, "test must reuse the same pooled connection"
    # current_setting returns '' once the GUC has been used on a connection; the
    # policy helper must turn that into NULL rather than erroring or matching.
    assert raw in (None, "")
    assert org is None
    assert await _count(single_connection_db, "memberships", None) == 0


async def test_binding_is_discarded_after_rollback_on_a_reused_connection(
    single_connection_db: Database, two_orgs: TwoOrgs
) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with single_connection_db.tenant_transaction(two_orgs.a.organization_id) as s:
            await s.execute(text("SELECT 1 FROM memberships"))
            raise RuntimeError("boom")
    _, org, _ = await _backend_pid_and_org(single_connection_db)
    assert org is None
    assert await _count(single_connection_db, "accounts", None) == 0


async def test_binding_is_discarded_after_a_database_error(
    single_connection_db: Database, two_orgs: TwoOrgs
) -> None:
    with pytest.raises(DBAPIError):
        async with single_connection_db.tenant_transaction(two_orgs.a.organization_id) as s:
            await s.execute(text("SELECT 1/0"))
    _, org, _ = await _backend_pid_and_org(single_connection_db)
    assert org is None


async def test_session_reused_after_its_transaction_fails_closed(
    app_db: Database, two_orgs: TwoOrgs
) -> None:
    async with app_db.tenant_transaction(two_orgs.a.organization_id) as s:
        pass
    # Autobegin opens a new transaction without the org bound: it must see nothing.
    count: int = (await s.execute(text("SELECT count(*) FROM memberships"))).scalar_one()
    await s.close()
    assert count == 0


async def test_concurrent_requests_for_different_orgs_never_mix(
    two_orgs: TwoOrgs,
) -> None:
    db = Database(create_engine(APP_URL, application_name="lattice-tests-concurrency", pool_size=3))
    orgs = [two_orgs.a.organization_id, two_orgs.b.organization_id]

    async def visible_orgs(org: uuid.UUID) -> tuple[uuid.UUID, set[uuid.UUID]]:
        async with db.tenant_transaction(org) as s:
            await asyncio.sleep(0)  # yield to interleave with other tasks
            seen: set[uuid.UUID] = set(
                (await s.execute(text("SELECT organization_id FROM memberships"))).scalars()
            )
            await s.execute(text("SELECT pg_sleep(0.001)"))
            seen |= set((await s.execute(text("SELECT id FROM organizations"))).scalars())
        return org, seen

    try:
        results = await asyncio.gather(*(visible_orgs(orgs[i % 2]) for i in range(40)))
    finally:
        await db.dispose()
    for org, seen in results:
        assert seen == {org}


async def test_bind_organization_rejects_non_uuid_values(app_db: Database) -> None:
    async with app_db.unscoped_transaction() as s:
        with pytest.raises(TypeError):
            await bind_organization(s, "00000000-0000-0000-0000-000000000000")  # type: ignore[arg-type]


async def test_malformed_org_setting_fails_closed_with_an_error(app_db: Database) -> None:
    with pytest.raises(DBAPIError, match="invalid input syntax for type uuid"):
        async with app_db.unscoped_transaction() as s:
            await s.execute(text("SELECT set_config('app.org_id', 'not-a-uuid', true)"))
            await s.execute(text("SELECT count(*) FROM memberships"))
