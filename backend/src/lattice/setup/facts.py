"""Facts for the setup record, read as the administrator's API role through row-level
security bound to their organization (phase-12-plan C12). Counts, flags and times only:
no titles, names of people, channels or hidden groups, no content, no secret -- not even
a credential reference (availability is checked by the secret store and only the
boolean leaves this module)."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
_CONNECTION_SQL = text(
    "SELECT status, last_success_at, last_failure_at FROM oidc_connections "
    "WHERE organization_id = :o ORDER BY (status = 'active') DESC, id LIMIT 1"
)
# SCIM tokens: live and ever issued; whether a live one has been used (the identity
# provider changed a member with it: an audit event by that token's actor); members
# the identity provider created that are still present.
_SCIM_SQL = text(
    """
    SELECT
      count(*) FILTER (WHERE t.revoked_at IS NULL) AS live_tokens,
      count(*) AS tokens_ever,
      EXISTS (
        SELECT 1 FROM audit_events e JOIN scim_tokens l
          ON l.organization_id = e.organization_id AND l.revoked_at IS NULL
         AND e.actor = 'scim:' || l.id::text
        WHERE e.organization_id = :o
      ) AS token_used,
      (SELECT count(DISTINCT m.id) FROM memberships m
        JOIN audit_events e ON e.organization_id = m.organization_id
         AND e.action = 'access.member_changed' AND e.actor LIKE 'scim:%'
         AND e.before IS NULL AND e.target ->> 'membership_id' = m.id::text
        WHERE m.organization_id = :o AND m.status <> 'removed') AS provisioned_members
    FROM scim_tokens t WHERE t.organization_id = :o
    """
)
_SOURCES_SQL = text(
    """
    WITH connected AS (
      SELECT id, account_scoped, config FROM sources
      WHERE organization_id = :o AND status IN ('active', 'paused')
    ), latest AS (
      SELECT DISTINCT ON (r.source_id) r.source_id, r.id, r.status
      FROM source_sync_runs r JOIN connected c ON c.id = r.source_id
      WHERE r.organization_id = :o
      ORDER BY r.source_id, r.started_at DESC, r.id
    )
    SELECT
      (SELECT count(*) FROM connected) AS connected_sources,
      (SELECT count(*) FROM latest WHERE status IN ('succeeded', 'partial')) AS synced_sources,
      (SELECT count(*) FROM latest WHERE status = 'failed') AS failed_sources,
      (SELECT count(DISTINCT j.source_id) FROM jobs j JOIN connected c ON c.id = j.source_id
        WHERE j.organization_id = :o AND j.status IN ('queued', 'running')) AS syncing_sources,
      (SELECT count(*) FROM connected
        WHERE account_scoped OR coalesce(config -> 'account_refs', '{}'::jsonb) <> '{}'::jsonb
           OR jsonb_path_exists(config, '$.channels.*.account')) AS account_sources,
      (SELECT count(DISTINCT f.message_params ->> 'external_account_ref')
         FROM sync_record_failures f JOIN latest l ON l.id = f.sync_run_id
        WHERE f.organization_id = :o AND f.error_code = 'access_unmappable:account')
        AS unmapped_references
    """
)
_ANSWERED_SQL = text(
    "SELECT EXISTS (SELECT 1 FROM query_traces WHERE organization_id = :o "
    "AND status IN ('answered', 'partial'))"
)


async def _ai(
    session: AsyncSession,
    org: uuid.UUID,
    default_sensitivity: str,
    *,
    factory: GenerationFactory,
    secrets: SecretStore,
) -> AiFacts:
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
    )
    try:
        declared = factory.declare(config, credential)
    except GenerationUnavailable:
        return AiFacts(**{**base.__dict__, "unusable": True})
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
        configured=True,
        enabled=True,
        provider=config.record.provider,
        model=config.record.model,
        credential_required=declared.requires_credential,
        credential_available=available,
        allows_default=may_send(default_sensitivity, decision.level),
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
    connection = (await session.execute(_CONNECTION_SQL, {"o": org})).one_or_none()
    scim = (await session.execute(_SCIM_SQL, {"o": org})).one()
    sources = (await session.execute(_SOURCES_SQL, {"o": org})).one()
    answered: bool = (await session.execute(_ANSWERED_SQL, {"o": org})).scalar_one()
    egress = await records.org_egress(session, org)
    return SetupFacts(
        organization_status=status,
        active_administrators=people.active_administrators,
        active_members=people.active_members,
        connection="none" if connection is None else connection.status,
        last_success_at=None if connection is None else connection.last_success_at,
        last_failure_at=None if connection is None else connection.last_failure_at,
        live_tokens=scim.live_tokens,
        tokens_ever=scim.tokens_ever,
        token_used=bool(scim.token_used),
        provisioned_members=scim.provisioned_members,
        default_question_sensitivity=egress.default_query_sensitivity,
        ai=await _ai(
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
        account_sources=sources.account_sources,
        unmapped_references=sources.unmapped_references,
        answered=answered,
    )
