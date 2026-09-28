"""Organization-scoped provider configuration and credentials (D-16, migration 0008):
references only, audited changes, append-only audit events, structural invariants."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from lattice.db.engine import Database
from lattice.providers import configuration
from lattice.providers.configuration import ConfigurationRejected
from lattice.providers.secrets import EnvironmentSecretStore

from .factories import create_org

ACTOR = "test:operator"


async def _events(owner_db: Database, org: object) -> list[dict[str, object]]:
    async with owner_db.tenant_transaction(org) as s:  # type: ignore[arg-type]
        return [
            dict(r)
            for r in (
                await s.execute(
                    text(
                        "SELECT action, target, before, after, reason, actor FROM audit_events "
                        "ORDER BY created_at, id"
                    )
                )
            ).mappings()
        ]


async def test_every_change_is_audited_with_its_reason(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    async with owner_db.tenant_transaction(org) as s:
        await configuration.configure_provider(
            s,
            org,
            purpose="generation",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            deployment="external_api",
            endpoint=None,
            retention_mode="none",
            training_opt_out=True,
            reason="pilot",
            actor=ACTOR,
        )
        await configuration.approve(
            s,
            org,
            purpose="generation",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            level="confidential",
            reason="contract signed",
            actor=ACTOR,
        )
        await configuration.set_credential(
            s,
            org,
            provider="gemini",
            secret_ref="env:LATTICE_PROVIDER_SECRET_HALDEN",
            data_use="paid",
            reason="BYOK",
            actor=ACTOR,
        )
        await configuration.set_credential(
            s,
            org,
            provider="gemini",
            secret_ref="env:LATTICE_PROVIDER_SECRET_HALDEN_2",
            data_use="paid",
            reason="rotation",
            actor=ACTOR,
        )
        await configuration.revoke_credential(
            s, org, provider="gemini", reason="leaked", actor=ACTOR
        )
        await configuration.set_enabled(
            s,
            org,
            purpose="generation",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            enabled=False,
            reason="incident",
            actor=ACTOR,
        )
        await configuration.set_egress_levels(
            s, org, external_level="confidential", reason="opt in", actor=ACTOR
        )
    events = await _events(owner_db, org)
    assert [e["action"] for e in events] == [
        "provider.configured",
        "provider.approval_changed",
        "credential.set",
        "credential.rotated",
        "credential.revoked",
        "provider.disabled",
        "egress.levels_changed",
    ]
    assert [e["reason"] for e in events][:2] == ["pilot", "contract signed"]
    assert all(e["actor"] == ACTOR for e in events)
    everything = str(events)
    assert "LATTICE_PROVIDER_SECRET_HALDEN" not in everything  # not even the reference
    assert events[1]["before"]["approved_for_level"] == "internal"  # type: ignore[index]
    assert events[1]["after"]["approved_for_level"] == "confidential"  # type: ignore[index]
    async with owner_db.tenant_transaction(org) as s:
        rows = (
            await s.execute(
                text("SELECT status, secret_ref FROM provider_credentials ORDER BY created_at")
            )
        ).all()
    assert [r[0] for r in rows] == ["revoked", "revoked"]  # history kept


async def test_a_reason_is_required(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    with pytest.raises(ConfigurationRejected, match="reason"):
        async with owner_db.tenant_transaction(org) as s:
            await configuration.set_egress_levels(
                s, org, external_level="confidential", reason="   ", actor=ACTOR
            )
    assert await _events(owner_db, org) == []


async def test_audit_events_are_append_only(owner_db: Database, app_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    async with owner_db.tenant_transaction(org) as s:
        await configuration.set_egress_levels(
            s, org, external_level="internal", reason="baseline", actor=ACTOR
        )
    with pytest.raises(DBAPIError, match="append-only"):
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(text("UPDATE audit_events SET reason = 'rewritten'"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_db.tenant_transaction(org) as s:
            await s.execute(text("SELECT 1 FROM audit_events"))


@pytest.mark.parametrize(
    "reference",
    [
        "AIzaSyD-a-raw-key-pasted-by-mistake",
        "env:LATTICE_DATABASE_URL",
        "env:POSTGRES_PASSWORD",
        "file:/etc/passwd",
    ],
)
async def test_only_references_can_be_stored(owner_db: Database, reference: str) -> None:
    org = await create_org(owner_db, "halden")
    with pytest.raises(ConfigurationRejected):
        async with owner_db.tenant_transaction(org) as s:
            await configuration.set_credential(
                s,
                org,
                provider="gemini",
                secret_ref=reference,
                data_use=None,
                reason="x",
                actor=ACTOR,
            )


async def test_the_schema_refuses_a_raw_value_even_without_the_code(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    with pytest.raises(IntegrityError, match="ck_provider_credentials_secret_ref"):
        async with owner_db.tenant_transaction(org) as s:
            await s.execute(
                text(
                    "INSERT INTO provider_credentials (organization_id, provider, secret_ref) "
                    "VALUES (:o, 'gemini', 'AIzaSy raw key with spaces')"
                ),
                {"o": org},
            )


async def test_one_active_credential_and_one_enabled_generation_configuration(
    owner_db: Database,
) -> None:
    org = await create_org(owner_db, "halden")
    with pytest.raises(IntegrityError, match="uq_provider_credentials_one_active"):
        async with owner_db.tenant_transaction(org) as s:
            for ref in ("env:LATTICE_PROVIDER_SECRET_A", "env:LATTICE_PROVIDER_SECRET_B"):
                await s.execute(
                    text(
                        "INSERT INTO provider_credentials (organization_id, provider, secret_ref) "
                        "VALUES (:o, 'gemini', :r)"
                    ),
                    {"o": org, "r": ref},
                )
    with pytest.raises(IntegrityError, match="uq_model_providers_one_enabled_generation"):
        async with owner_db.tenant_transaction(org) as s:
            for provider in ("fake", "gemini"):
                await s.execute(
                    text(
                        "INSERT INTO model_providers (organization_id, purpose, provider, model, "
                        "deployment, retention_mode, training_opt_out) VALUES (:o, 'generation', "
                        ":p, 'm', 'external_api', 'none', true)"
                    ),
                    {"o": org, "p": provider},
                )


async def test_configuring_a_second_generation_provider_disables_the_first(
    owner_db: Database,
) -> None:
    org = await create_org(owner_db, "halden")
    async with owner_db.tenant_transaction(org) as s:
        for provider, deployment, endpoint in (
            ("fake", "external_api", None),
            ("ollama", "local", "http://127.0.0.1:11434"),
        ):
            await configuration.configure_provider(
                s,
                org,
                purpose="generation",
                provider=provider,
                model="m",
                deployment=deployment,
                endpoint=endpoint,
                retention_mode="none",
                training_opt_out=True,
                reason="x",
                actor=ACTOR,
            )
        views = await configuration.provider_overview(s, org, EnvironmentSecretStore(environ={}))
    assert {(v.provider, v.enabled) for v in views} == {("fake", False), ("ollama", True)}
    assert all(v.approved_for_level == "internal" for v in views)


async def test_the_trust_class_of_a_record_cannot_be_changed(owner_db: Database) -> None:
    org = await create_org(owner_db, "halden")
    async with owner_db.tenant_transaction(org) as s:
        await configuration.configure_provider(
            s,
            org,
            purpose="generation",
            provider="ollama",
            model="m",
            deployment="organization_controlled",
            endpoint="https://llm.halden.example",
            retention_mode="none",
            training_opt_out=True,
            reason="x",
            actor=ACTOR,
        )
    with pytest.raises(ConfigurationRejected, match="trust class cannot be changed"):
        async with owner_db.tenant_transaction(org) as s:
            await configuration.configure_provider(
                s,
                org,
                purpose="generation",
                provider="ollama",
                model="m",
                deployment="local",
                endpoint="https://llm.halden.example",
                retention_mode="none",
                training_opt_out=True,
                reason="x",
                actor=ACTOR,
            )
    for bad in ("private", "unknown", ""):
        with pytest.raises(ConfigurationRejected, match="deployment must be"):
            async with owner_db.tenant_transaction(org) as s:
                await configuration.configure_provider(
                    s,
                    org,
                    purpose="generation",
                    provider="x",
                    model="m",
                    deployment=bad,
                    endpoint=None,
                    retention_mode="none",
                    training_opt_out=True,
                    reason="x",
                    actor=ACTOR,
                )
