"""Read the organization's egress settings and provider records (``org_settings``,
``model_providers``) for the egress decision. Works in any tenant-bound session: the
runtime role (retrieval) has SELECT on both tables, the operator role (ingestion) owns
them. Row-level security scopes both to the bound organization."""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.providers.egress import OrgEgress, ProviderRecord


async def org_egress(session: AsyncSession, org: uuid.UUID) -> OrgEgress:
    row = (
        await session.execute(
            text(
                "SELECT external_egress_level, default_query_sensitivity, data_origin, "
                "organization_controlled_level FROM org_settings WHERE organization_id = :o"
            ),
            {"o": org},
        )
    ).one_or_none()
    if row is None:
        return OrgEgress()
    return OrgEgress(str(row[0]), str(row[1]), str(row[2]), None if row[3] is None else str(row[3]))


async def embedding_records(
    session: AsyncSession, org: uuid.UUID, model: str
) -> list[ProviderRecord]:
    """Every embedding provider recorded with this model name (normally one; more than
    one means the embedding space cannot be told apart by the model name)."""
    rows = await session.execute(
        text(
            "SELECT provider, model, deployment, approved_for_level, training_opt_out, "
            "approved_at IS NOT NULL FROM model_providers WHERE organization_id = :o "
            "AND purpose = 'embedding' AND model = :m ORDER BY provider"
        ),
        {"o": org, "m": model},
    )
    return [
        ProviderRecord(str(p), str(m), str(d), str(a), bool(t), bool(x))
        for p, m, d, a, t, x in rows
    ]


async def embedding_record(
    session: AsyncSession, org: uuid.UUID, provider: str, model: str
) -> ProviderRecord | None:
    return next(
        (r for r in await embedding_records(session, org, model) if r.provider == provider), None
    )


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """The organization's generation provider configuration (``model_providers``,
    purpose ``generation``): what the administrator recorded, approved and enabled."""

    record: ProviderRecord
    endpoint: str | None
    enabled: bool


@dataclass(frozen=True, slots=True)
class CredentialRef:
    """An organization-owned credential, by reference. Never a secret value."""

    id: uuid.UUID
    provider: str
    secret_ref: str
    data_use: str | None


async def generation_config(session: AsyncSession, org: uuid.UUID) -> GenerationConfig | None:
    """The enabled generation configuration (at most one, by a unique index), else the
    most recently updated disabled one (so the caller can say it is disabled), else
    None."""
    row = (
        await session.execute(
            text(
                "SELECT provider, model, deployment, approved_for_level, training_opt_out, "
                "approved_at IS NOT NULL, endpoint, enabled FROM model_providers "
                "WHERE organization_id = :o AND purpose = 'generation' "
                "ORDER BY enabled DESC, updated_at DESC, provider, model LIMIT 1"
            ),
            {"o": org},
        )
    ).one_or_none()
    if row is None:
        return None
    p, m, d, a, t, x, endpoint, enabled = row
    return GenerationConfig(
        ProviderRecord(str(p), str(m), str(d), str(a), bool(t), bool(x)),
        None if endpoint is None else str(endpoint),
        bool(enabled),
    )


async def active_credential(
    session: AsyncSession, org: uuid.UUID, provider: str
) -> CredentialRef | None:
    row = (
        await session.execute(
            text(
                "SELECT id, provider, secret_ref, data_use FROM provider_credentials "
                "WHERE organization_id = :o AND provider = :p AND status = 'active'"
            ),
            {"o": org, "p": provider},
        )
    ).one_or_none()
    if row is None:
        return None
    return CredentialRef(row[0], str(row[1]), str(row[2]), None if row[3] is None else str(row[3]))
