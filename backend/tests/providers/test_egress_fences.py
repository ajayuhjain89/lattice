"""The 0021 fences, from the API role's own connection (ADR-017; P12.5): what a compromised
API process could try with arbitrary SQL.

Proves: no direct write to the policy, credential, ciphertext, grant or version tables;
only the test facts are writable; the apply function decides escalation itself and
refuses one without a matching single-use grant of the same administrator and session
(another change, another ceiling, another provider, another session, a consumed grant);
a grant needs a fresh ``auth_time`` and a step-up attempt of that administrator and
session; another organization's credential cannot be touched; every fenced write needs
its audit event in the same transaction; and every writer -- operators too -- raises the
policy version."""

# Test SQL composed of this module's fixed fragments only (S608 does not apply).
# ruff: noqa: S608
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lattice.db.engine import Database

from ..factories import create_member, create_oidc_connection, create_org

STATE = "S" * 43
RANDOM32 = "sha256(gen_random_uuid()::text::bytea)"
BINDING = "B" * 43


@pytest.fixture
async def org(owner_db: Database) -> dict[str, Any]:
    org = await create_org(owner_db, "halden")
    connection = await create_oidc_connection(owner_db, org)
    lena = await create_member(owner_db, org, "lena@halden.example", is_admin=True)
    maya = await create_member(owner_db, org, "maya@halden.example")
    async with owner_db.tenant_transaction(org) as s:
        await s.execute(
            text(
                "INSERT INTO directory_identities (organization_id, connection_id, "
                "membership_id, external_id, user_name, managed_by) "
                "VALUES (:o, :c, :m, 'sub-lena', 'lena', 'organization')"
            ),
            {"o": org, "c": connection, "m": lena.membership_id},
        )
        sessions: list[uuid.UUID] = []
        for _ in range(2):
            sessions.append(
                (
                    await s.execute(
                        text(
                            "INSERT INTO sessions (token_hash, user_id, organization_id, "
                            f"expires_at) VALUES ({RANDOM32}, :u, :o, "
                            "now() + interval '1 hour') RETURNING id"
                        ),
                        {"u": lena.user_id, "o": org},
                    )
                ).scalar_one()
            )
        provider: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                    "deployment, retention_mode, training_opt_out, enabled) VALUES (:o, "
                    "'generation', 'fake', 'fake-extractive-v1', 'external_api', 'none', true, "
                    "true) RETURNING id"
                ),
                {"o": org},
            )
        ).scalar_one()
    return {
        "org": org,
        "connection": connection,
        "lena": lena.membership_id,
        "maya": maya.membership_id,
        "session": sessions[0],
        "other_session": sessions[1],
        "provider": provider,
    }


async def _app(app_db: Database, org: uuid.UUID, sql: str, **params: Any) -> Any:
    async with app_db.tenant_transaction(org) as s:
        result: Any = await s.execute(text(sql), params)
        return result.scalar() if result.returns_rows else None


async def _version(db: Database, org: uuid.UUID) -> int:
    async with db.tenant_transaction(org) as s:
        found: int = (
            await s.execute(
                text(
                    "SELECT coalesce((SELECT version FROM egress_policy_versions "
                    "WHERE organization_id = :o), 0)"
                ),
                {"o": org},
            )
        ).scalar_one()
    return found


def _approve(provider: uuid.UUID, level: str = "internal") -> str:
    return json.dumps({"kind": "provider", "provider_id": str(provider), "approval": level})


async def _apply(
    app_db: Database,
    o: dict[str, Any],
    change: str,
    *,
    grant: uuid.UUID | None = None,
    session: uuid.UUID | None = None,
    event: bool = True,
    action: str = "provider.approval_changed",
) -> Any:
    async with app_db.tenant_transaction(o["org"]) as s:
        version: int = (
            await s.execute(
                text(
                    "SELECT coalesce((SELECT version FROM egress_policy_versions "
                    "WHERE organization_id = :o), 0)"
                ),
                {"o": o["org"]},
            )
        ).scalar_one()
        result: Any = (
            await s.execute(
                text("SELECT lattice_apply_egress_change(:a, :s, CAST(:c AS jsonb), :v, :g)"),
                {
                    "a": o["lena"],
                    "s": session or o["session"],
                    "c": change,
                    "v": version,
                    "g": grant,
                },
            )
        ).scalar_one()
        if event:
            await s.execute(
                text(
                    "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
                    "VALUES (:o, :a, :x, CAST(:t AS jsonb), 'r')"
                ),
                {
                    "o": o["org"],
                    "a": f"member:{o['lena']}",
                    "x": action,
                    "t": json.dumps(
                        {"provider_id": str(o["provider"])}
                        | ({"grant_id": str(grant)} if grant else {})
                    ),
                },
            )
        return result


async def _grant(
    owner_db: Database,
    app_db: Database,
    o: dict[str, Any],
    change: str,
    *,
    auth_time: datetime | None = None,
    session: uuid.UUID | None = None,
) -> uuid.UUID:
    """A consumed step-up attempt of Lena's session (as the callback leaves it), then the
    grant through the fenced function with its event."""
    version = await _version(owner_db, o["org"])
    async with owner_db.tenant_transaction(o["org"]) as s:
        attempt: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO oidc_login_attempts (organization_id, connection_id, "
                    "state_hash, binding_hash, nonce, code_verifier, return_to, expires_at, "
                    "consumed_at, purpose, membership_id, session_id, change, reason, "
                    f"policy_version) VALUES (:o, :c, {RANDOM32}, {RANDOM32}, "
                    ":n, :v, '/settings/providers', now() + interval '10 minutes', now(), "
                    "'step_up', :m, :s, CAST(:ch AS jsonb), 'review', :pv) RETURNING id"
                ),
                {
                    "o": o["org"],
                    "c": o["connection"],
                    "n": "N" * 43,
                    "v": "V" * 64,
                    "m": o["lena"],
                    "s": session or o["session"],
                    "ch": change,
                    "pv": version,
                },
            )
        ).scalar_one()
    async with app_db.tenant_transaction(o["org"]) as s:
        grant: uuid.UUID = (
            await s.execute(
                text("SELECT lattice_create_step_up_grant(:a, :s, :t, :at, NULL)"),
                {
                    "a": o["lena"],
                    "s": session or o["session"],
                    "t": attempt,
                    "at": auth_time or datetime.now(UTC),
                },
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO audit_events (organization_id, actor, action, target, reason) "
                "VALUES (:o, :a, 'egress.step_up_completed', CAST(:t AS jsonb), 'r')"
            ),
            {"o": o["org"], "a": f"member:{o['lena']}", "t": json.dumps({"grant_id": str(grant)})},
        )
    return grant


# --- no direct writes ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE org_settings SET external_egress_level = 'restricted'",
        "INSERT INTO org_settings (organization_id, external_egress_level) "
        "VALUES (lattice_current_org_id(), 'restricted')",
        "UPDATE model_providers SET approved_for_level = 'restricted', approved_at = now()",
        "UPDATE model_providers SET enabled = true",
        "UPDATE model_providers SET deployment = 'local'",
        "UPDATE model_providers SET endpoint = 'https://evil.example'",
        "INSERT INTO model_providers (organization_id, purpose, provider, model, deployment, "
        "retention_mode, training_opt_out) VALUES (lattice_current_org_id(), 'generation', "
        "'gemini', 'x', 'local', 'none', true)",
        "DELETE FROM model_providers",
        "UPDATE provider_credentials SET status = 'revoked', revoked_at = now()",
        "INSERT INTO provider_credentials (organization_id, provider, secret_ref) "
        "VALUES (lattice_current_org_id(), 'gemini', 'env:LATTICE_PROVIDER_SECRET_OTHER')",
        "UPDATE encrypted_secrets SET destroyed_at = now(), nonce = NULL, ciphertext = NULL",
        "INSERT INTO step_up_grants (organization_id, membership_id, session_id, attempt_id, "
        "change, change_digest, reason, policy_version, auth_time, expires_at) "
        "SELECT lattice_current_org_id(), gen_random_uuid(), gen_random_uuid(), "
        f"gen_random_uuid(), '{{}}', {RANDOM32}, 'r', 0, now(), "
        "now() + interval '1 minute'",
        "UPDATE step_up_grants SET consumed_at = NULL",
        "UPDATE egress_policy_versions SET version = 0",
        "INSERT INTO egress_policy_versions (organization_id) VALUES (lattice_current_org_id())",
        "UPDATE oidc_connections SET step_up_acr_values = NULL",
        "UPDATE oidc_login_attempts SET change = '{}'::jsonb",
    ],
)
async def test_the_api_role_has_no_direct_write(
    app_db: Database, org: dict[str, Any], sql: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await _app(app_db, org["org"], sql)


async def test_only_the_test_facts_are_writable(app_db: Database, org: dict[str, Any]) -> None:
    await _app(
        app_db,
        org["org"],
        "UPDATE model_providers SET last_test_at = now(), last_test_result = 'valid'",
    )
    assert await _app(app_db, org["org"], "SELECT last_test_result FROM model_providers") == "valid"


async def test_the_helpers_are_not_callable(app_db: Database, org: dict[str, Any]) -> None:
    for sql in (
        "SELECT lattice_egress_cells(lattice_current_org_id())",
        'SELECT lattice_egress_write(lattice_current_org_id(), \'{"kind": "settings"}\')',
        "SELECT lattice_egress_version(lattice_current_org_id())",
        "SELECT lattice_require_policy_actor(gen_random_uuid())",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            await _app(app_db, org["org"], sql)


# --- the apply function -------------------------------------------------------------------


async def test_an_escalation_without_a_grant_is_refused(
    app_db: Database, org: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError, match="step_up_required"):
        await _apply(app_db, org, _approve(org["provider"]))
    # Lowering needs none: the database decides, from the cells.
    disabled = json.dumps(
        {"kind": "provider", "provider_id": str(org["provider"]), "enabled": False}
    )
    lowered = await _apply(app_db, org, disabled, action="provider.disabled")
    assert lowered["escalation"] is False


async def test_a_grant_applies_its_own_change_once(
    owner_db: Database, app_db: Database, org: dict[str, Any]
) -> None:
    grant = await _grant(owner_db, app_db, org, _approve(org["provider"]))
    with pytest.raises(DBAPIError, match="grant_mismatch"):  # another level
        await _apply(app_db, org, _approve(org["provider"], "confidential"), grant=grant)
    with pytest.raises(DBAPIError, match="grant_invalid"):  # another session
        await _apply(
            app_db, org, _approve(org["provider"]), grant=grant, session=org["other_session"]
        )
    result = await _apply(app_db, org, _approve(org["provider"]), grant=grant)
    assert result["escalation"] is True
    assert result["grant"] == str(grant)
    # Withdrawn by an operator, the same change is an escalation again: the grant is spent.
    async with owner_db.tenant_transaction(org["org"]) as s:
        await s.execute(text("UPDATE model_providers SET approved_at = NULL"))
    version = await _version(owner_db, org["org"])
    async with owner_db.tenant_transaction(org["org"]) as s:
        await s.execute(
            text("UPDATE step_up_grants SET policy_version = :v WHERE id = :g"),
            {"v": version, "g": grant},
        )
    with pytest.raises(DBAPIError, match="grant_consumed"):
        await _apply(app_db, org, _approve(org["provider"]), grant=grant)


async def test_a_grant_for_one_ceiling_does_not_authorize_another(
    owner_db: Database, app_db: Database, org: dict[str, Any]
) -> None:
    async with owner_db.tenant_transaction(org["org"]) as s:  # approved up to restricted
        await s.execute(
            text(
                "UPDATE model_providers SET approved_at = now(), approved_for_level = 'restricted'"
            )
        )
    confidential = json.dumps({"kind": "settings", "external_egress_level": "confidential"})
    restricted = json.dumps({"kind": "settings", "external_egress_level": "restricted"})
    grant = await _grant(owner_db, app_db, org, confidential)
    with pytest.raises(DBAPIError, match="grant_mismatch"):
        await _apply(app_db, org, restricted, grant=grant, action="egress.levels_changed")
    applied = await _apply(app_db, org, confidential, grant=grant, action="egress.levels_changed")
    assert applied["escalation"] is True


async def test_a_grant_for_one_provider_does_not_authorize_another(
    owner_db: Database, app_db: Database, org: dict[str, Any]
) -> None:
    async with owner_db.tenant_transaction(org["org"]) as s:
        other: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                    "deployment, retention_mode, training_opt_out, enabled) VALUES (:o, "
                    "'generation', 'fake', 'fake-2', 'external_api', 'none', true, false) "
                    "RETURNING id"
                ),
                {"o": org["org"]},
            )
        ).scalar_one()
    grant = await _grant(owner_db, app_db, org, _approve(other))
    with pytest.raises(DBAPIError, match="grant_mismatch"):
        await _apply(app_db, org, _approve(org["provider"]), grant=grant)


async def test_a_grant_needs_a_fresh_authentication_of_this_session(
    owner_db: Database, app_db: Database, org: dict[str, Any]
) -> None:
    for auth_time in (
        datetime.now(UTC) - timedelta(minutes=6),
        datetime.now(UTC) + timedelta(minutes=5),
        datetime.now(UTC) - timedelta(minutes=3),  # before the attempt began
    ):
        with pytest.raises(DBAPIError, match="step_up_not_fresh"):
            await _grant(owner_db, app_db, org, _approve(org["provider"]), auth_time=auth_time)
    async with owner_db.tenant_transaction(org["org"]) as s:
        await s.execute(
            text("UPDATE sessions SET revoked_at = now() WHERE id = :s"), {"s": org["session"]}
        )
    with pytest.raises(DBAPIError, match="session_ended"):
        await _grant(owner_db, app_db, org, _approve(org["provider"]))


async def test_a_suspended_organization_cannot_escalate_even_with_a_grant(
    owner_db: Database, app_db: Database, org: dict[str, Any]
) -> None:
    grant = await _grant(owner_db, app_db, org, _approve(org["provider"]))
    async with owner_db.tenant_transaction(org["org"]) as s:
        await s.execute(
            text("UPDATE organizations SET status = 'suspended' WHERE id = :o"), {"o": org["org"]}
        )
    with pytest.raises(DBAPIError, match="organization_inactive"):
        await _apply(app_db, org, _approve(org["provider"]), grant=grant)
    with pytest.raises(DBAPIError, match="organization_inactive"):
        await _grant(owner_db, app_db, org, _approve(org["provider"], "confidential"))


async def test_a_member_or_an_outsider_cannot_call_the_functions(
    owner_db: Database, app_db: Database, org: dict[str, Any]
) -> None:
    outsider_org = await create_org(owner_db, "veridian")
    outsider = await create_member(owner_db, outsider_org, "vera@veridian.example", is_admin=True)
    for actor in (org["maya"], outsider.membership_id):
        for sql in (
            "SELECT lattice_preview_egress_change(:a, CAST(:c AS jsonb))",
            "SELECT lattice_apply_egress_change(:a, NULL, CAST(:c AS jsonb), 0, NULL)",
        ):
            with pytest.raises(DBAPIError, match="not_administrator"):
                await _app(app_db, org["org"], sql, a=actor, c=_approve(org["provider"]))


async def test_another_organizations_credential_cannot_be_touched(
    owner_db: Database, app_db: Database, org: dict[str, Any]
) -> None:
    other = await create_org(owner_db, "veridian")
    async with owner_db.tenant_transaction(other) as s:
        foreign: uuid.UUID = (
            await s.execute(
                text(
                    "INSERT INTO provider_credentials (organization_id, provider, secret_ref) "
                    "VALUES (:o, 'gemini', 'env:LATTICE_PROVIDER_SECRET_VERIDIAN') RETURNING id"
                ),
                {"o": other},
            )
        ).scalar_one()
    with pytest.raises(DBAPIError, match="credential_changed"):
        await _app(
            app_db,
            org["org"],
            "SELECT lattice_revoke_provider_credential(:a, 'gemini', :e)",
            a=org["lena"],
            e=foreign,
        )
    async with owner_db.tenant_transaction(other) as s:
        status: str = (
            await s.execute(
                text("SELECT status FROM provider_credentials WHERE id = :c"), {"c": foreign}
            )
        ).scalar_one()
    assert status == "active"


# --- audit coupling -----------------------------------------------------------------------


async def test_a_fenced_write_without_its_event_does_not_commit(
    owner_db: Database, app_db: Database, org: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError, match="without an audit event"):
        await _apply(
            app_db,
            org,
            json.dumps({"kind": "settings", "default_query_sensitivity": "restricted"}),
            event=False,
        )
    with pytest.raises(DBAPIError, match="without an audit event"):
        await _app(
            app_db,
            org["org"],
            "SELECT lattice_create_provider(:a, 'gemini', 'gemini-3.5-flash-lite', NULL, true)",
            a=org["lena"],
        )
    with pytest.raises(DBAPIError, match="without an audit event"):
        await _app(
            app_db,
            org["org"],
            "SELECT lattice_set_provider_credential(:a, 'gemini', NULL, gen_random_uuid(), "
            f"gen_random_uuid(), 'a', substring({RANDOM32} for 12), "
            f"{RANDOM32} || '\\x0102030405060708'::bytea, NULL, 'paid')",
            a=org["lena"],
        )


# --- the policy version -------------------------------------------------------------------


async def test_every_writer_raises_the_policy_version(
    owner_db: Database, worker_db: Database, org: dict[str, Any]
) -> None:
    start = await _version(owner_db, org["org"])
    async with owner_db.tenant_transaction(org["org"]) as s:  # an operator
        await s.execute(
            text("INSERT INTO org_settings (organization_id) VALUES (:o)"), {"o": org["org"]}
        )
    after_settings = await _version(owner_db, org["org"])
    assert after_settings == start + 1
    async with worker_db.tenant_transaction(org["org"]) as s:  # a sync's embedding record
        await s.execute(
            text(
                "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                "deployment, retention_mode, training_opt_out) VALUES (:o, 'embedding', "
                "'fake', 'fake-hash', 'external_api', 'none', true)"
            ),
            {"o": org["org"]},
        )
    assert await _version(owner_db, org["org"]) == after_settings + 1
    async with owner_db.tenant_transaction(org["org"]) as s:  # test facts are not policy
        await s.execute(
            text("UPDATE model_providers SET last_test_at = now(), last_test_result = 'valid'")
        )
    assert await _version(owner_db, org["org"]) == after_settings + 1
