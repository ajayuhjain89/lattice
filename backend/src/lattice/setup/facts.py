"""Facts for the setup record, read as the administrator's API role through row-level
security bound to their organization (phase-12-plan C12). Counts, flags and times only:
no titles, names of people, channels or hidden groups, no content, no secret -- not even
a credential reference (availability is checked by the secret store and only the
boolean leaves this module)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.orgs.outbound_targets import access_class, policy_for
from lattice.providers import records
from lattice.providers.egress import may_send, provider_decision
from lattice.providers.generation_factory import GenerationFactory, GenerationUnavailable
from lattice.providers.secrets import SecretStore
from lattice.setup.derive import AiFacts, SetupFacts
from lattice.vault.records import load_sealed

_PEOPLE_SQL = text(
    """
    SELECT
      count(*) FILTER (WHERE m.status = 'active' AND u.status = 'active') AS active_members,
      count(*) FILTER (WHERE m.status = 'active' AND u.status = 'active' AND m.is_admin)
        AS active_administrators
    FROM memberships m JOIN users u ON u.id = m.user_id
    WHERE m.organization_id = :o
    """
)
# The active connection, else the most recently changed disabled one.
_CONNECTION_SQL = text(
    "SELECT status, issuer, last_success_at, last_failure_at, last_failure_code, "
    "last_test_at, last_test_result FROM oidc_connections WHERE organization_id = :o "
    "ORDER BY (status = 'active') DESC, updated_at DESC, id LIMIT 1"
)
# SCIM tokens: live and ever issued; whether a live one has authenticated a request
# (``last_used_at``, P12.4); members the identity provider manages (0017 ``managed_by``)
# that are still present; its refused requests in the last day (``scim.request_refused``).
_SCIM_SQL = text(
    """
    SELECT
      count(*) FILTER (WHERE t.revoked_at IS NULL) AS live_tokens,
      count(*) AS tokens_ever,
      bool_or(t.revoked_at IS NULL AND t.last_used_at IS NOT NULL) AS token_used,
      (SELECT count(*) FROM memberships m
        JOIN directory_identities d ON d.organization_id = m.organization_id
         AND d.membership_id = m.id AND d.managed_by = 'identity_provider'
        WHERE m.organization_id = :o AND m.status <> 'removed') AS provisioned_members,
      (SELECT count(*) FROM audit_events e WHERE e.organization_id = :o
        AND e.action = 'scim.request_refused'
        AND e.created_at > now() - interval '24 hours') AS refused_recently
    FROM scim_tokens t WHERE t.organization_id = :o
    """
)


@dataclass(frozen=True)
class IdentityFacts:
    """The identity and provisioning part of the setup facts (P12.4)."""

    connection: str = "none"
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_failure_code: str | None = None
    last_test_at: datetime | None = None
    last_test_result: str | None = None
    issuer_access: str = "public"
    live_tokens: int = 0
    tokens_ever: int = 0
    token_used: bool = False
    provisioned_members: int = 0
    refused_recently: int = 0


async def identity_facts(session: AsyncSession, org: uuid.UUID) -> IdentityFacts:
    connection = (await session.execute(_CONNECTION_SQL, {"o": org})).one_or_none()
    scim = (await session.execute(_SCIM_SQL, {"o": org})).one()
    access = "public"
    if connection is not None:
        access = access_class(await policy_for(session, org, "oidc"), connection.issuer)
    return IdentityFacts(
        connection="none" if connection is None else connection.status,
        last_success_at=None if connection is None else connection.last_success_at,
        last_failure_at=None if connection is None else connection.last_failure_at,
        last_failure_code=None if connection is None else connection.last_failure_code,
        last_test_at=None if connection is None else connection.last_test_at,
        last_test_result=None if connection is None else connection.last_test_result,
        issuer_access=access,
        live_tokens=scim.live_tokens,
        tokens_ever=scim.tokens_ever,
        token_used=bool(scim.token_used),
        provisioned_members=scim.provisioned_members,
        refused_recently=scim.refused_recently,
    )


_SOURCES_SQL = text(
    """
    WITH connected AS (
      SELECT id, account_scoped, config, managed_by, import_id FROM sources
      WHERE organization_id = :o AND status IN ('active', 'paused')
    ), latest AS (
      SELECT DISTINCT ON (r.source_id) r.source_id, r.id, r.status, r.started_at
      FROM source_sync_runs r JOIN connected c ON c.id = r.source_id
      WHERE r.organization_id = :o
      ORDER BY r.source_id, r.started_at DESC, r.id
    )
    SELECT
      (SELECT count(*) FROM connected) AS connected_sources,
      (SELECT count(*) FROM latest WHERE status IN ('succeeded', 'partial')) AS synced_sources,
      (SELECT count(*) FROM latest WHERE status = 'failed') AS failed_sources,
      (SELECT count(DISTINCT j.source_id) FROM jobs j JOIN connected c ON c.id = j.source_id
        WHERE j.organization_id = :o AND j.status IN ('queued', 'running')
          AND j.kind = 'source_sync') AS syncing_sources,
      -- P12.6: organization-managed sources with no current import, and those whose
      -- latest upload was refused.
      (SELECT count(*) FROM connected c WHERE c.managed_by = 'organization'
        AND c.import_id IS NULL) AS awaiting_import_sources,
      (SELECT count(*) FROM connected c WHERE c.managed_by = 'organization'
        AND c.import_id IS NULL AND (
          SELECT i.rejected_code FROM source_imports i
          WHERE i.organization_id = :o AND i.source_id = c.id
          ORDER BY i.created_at DESC, i.id LIMIT 1) IS NOT NULL) AS rejected_import_sources,
      -- P12.8: those whose current import failed its check (no sync can run).
      (SELECT count(*) FROM connected c JOIN source_imports i
        ON i.organization_id = :o AND i.id = c.import_id
        WHERE c.managed_by = 'organization' AND i.check_error_code IS NOT NULL)
        AS invalid_import_sources,
      (SELECT count(*) FROM connected c
        WHERE c.account_scoped OR EXISTS (
          SELECT 1 FROM account_external_refs m
          WHERE m.organization_id = :o AND m.source_id = c.id)) AS account_sources,
      -- Still unmapped now (no mapping, or unresolved after an unmap): one mapped since
      -- the run waits for the next sync instead.
      (SELECT count(*) FROM (
         SELECT DISTINCT f.sync_run_id, f.message_params ->> 'external_account_ref' AS ref,
                l.source_id
         FROM sync_record_failures f JOIN latest l ON l.id = f.sync_run_id
         WHERE f.organization_id = :o AND f.error_code = 'access_unmappable:account'
           AND f.message_params ? 'external_account_ref') w
        WHERE NOT EXISTS (
          SELECT 1 FROM account_external_refs m
          WHERE m.organization_id = :o AND m.source_id = w.source_id
            AND m.external_ref = w.ref AND m.account_id IS NOT NULL)) AS unmapped_references,
      (SELECT count(*) FROM account_external_refs m JOIN latest l ON l.source_id = m.source_id
        WHERE m.organization_id = :o AND m.updated_at > l.started_at)
        AS references_awaiting_sync
    """
)
_ANSWERED_SQL = text(
    "SELECT EXISTS (SELECT 1 FROM query_traces WHERE organization_id = :o "
    "AND status IN ('answered', 'partial')), "
    "EXISTS (SELECT 1 FROM query_traces WHERE organization_id = :o "
    "AND status = 'evidence_only')"
)


_LAST_TEST_SQL = text(
    "SELECT last_test_result, last_test_credential_id FROM model_providers "
    "WHERE organization_id = :o AND id = :p AND last_test_at IS NOT NULL"
)


async def ai_facts(
    session: AsyncSession,
    org: uuid.UUID,
    default_sensitivity: str,
    *,
    factory: GenerationFactory,
    secrets: SecretStore,
) -> AiFacts:
    """The organization's generation configuration as the invocation rule sees it."""
    config = await records.generation_config(session, org)
    if config is None:
        return AiFacts()
    if not config.enabled:
        return AiFacts(configured=True, provider=config.record.provider, model=config.record.model)
    credential = await records.active_credential(session, org, config.record.provider)
    base = AiFacts(
        configured=True,
        enabled=True,
        provider=config.record.provider,
        model=config.record.model,
        approved=config.record.explicitly_approved,
    )
    try:
        declared = factory.declare(config, credential)
    except GenerationUnavailable:
        return AiFacts(**{**base.__dict__, "unusable": True})
    outbound_permitted = True
    if declared.deployment == "organization_controlled":
        policy = await policy_for(session, org, "generation")
        outbound_permitted = access_class(policy, config.endpoint or "") != "not_permitted"
    last_test = (await session.execute(_LAST_TEST_SQL, {"o": org, "p": config.id})).one_or_none()
    current_test = (
        None
        if last_test is None
        or last_test.last_test_credential_id != (credential.id if credential else None)
        else str(last_test.last_test_result)
    )
    available = False
    if credential is not None:
        sealed = await load_sealed(
            session,
            credential.secret_ref,
            organization_id=org,
            owner_kind="provider_api_key",
            owner_id=credential.id,
        )
        available = secrets.available(credential.secret_ref, sealed)
    decision = provider_decision(
        await records.org_egress(session, org),
        config.record,
        deployment=declared.deployment,
        training_opt_out=declared.training_opt_out,
    )
    return AiFacts(
        **{
            **base.__dict__,
            "outbound_permitted": outbound_permitted,
            "credential_required": declared.requires_credential,
            "credential_present": credential is not None,
            "credential_available": available,
            "last_test_result": current_test,
            "allows_default": config.record.explicitly_approved
            and may_send(default_sensitivity, decision.level),
        }
    )


async def gather(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    factory: GenerationFactory,
    secrets: SecretStore,
) -> SetupFacts:
    status: str = (
        await session.execute(text("SELECT status FROM organizations WHERE id = :o"), {"o": org})
    ).scalar_one()
    people = (await session.execute(_PEOPLE_SQL, {"o": org})).one()
    identity = await identity_facts(session, org)
    sources = (await session.execute(_SOURCES_SQL, {"o": org})).one()
    answered, evidence_returned = (await session.execute(_ANSWERED_SQL, {"o": org})).one()
    egress = await records.org_egress(session, org)
    return SetupFacts(
        organization_status=status,
        active_administrators=people.active_administrators,
        active_members=people.active_members,
        **identity.__dict__,
        default_question_sensitivity=egress.default_query_sensitivity,
        ai=await ai_facts(
            session,
            org,
            egress.default_query_sensitivity,
            factory=factory,
            secrets=secrets,
        ),
        connected_sources=sources.connected_sources,
        synced_sources=sources.synced_sources,
        failed_sources=sources.failed_sources,
        syncing_sources=sources.syncing_sources,
        awaiting_import_sources=sources.awaiting_import_sources,
        rejected_import_sources=sources.rejected_import_sources,
        invalid_import_sources=sources.invalid_import_sources,
        account_sources=sources.account_sources,
        unmapped_references=sources.unmapped_references,
        references_awaiting_sync=sources.references_awaiting_sync,
        answered=bool(answered),
        evidence_returned=bool(evidence_returned),
    )
