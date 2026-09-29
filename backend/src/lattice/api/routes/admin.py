"""Administrator read model of the organization's provider configuration (D-16).

Masked: whether each provider is configured, approved, enabled and has a resolvable
credential -- never a secret or its reference. Changes are administrative actions with
a reason and an audit event (``lattice provider ...`` today; the Phase 7 settings UI)."""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from lattice.api.deps import CurrentPrincipal
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.providers.configuration import provider_overview
from lattice.providers.egress import OrgEgress
from lattice.providers.records import org_egress
from lattice.providers.secrets import SecretStore

router = APIRouter(tags=["admin"])


class CredentialOut(BaseModel):
    state: Literal["none", "active"]
    source: str | None
    resolvable: bool
    data_use: str | None


class ProviderOut(BaseModel):
    purpose: str
    provider: str
    model: str
    deployment: str
    approved_for_level: str
    explicitly_approved: bool
    enabled: bool
    endpoint_configured: bool
    retention_mode: str
    training_opt_out: bool
    credential: CredentialOut


class EgressOut(BaseModel):
    external_egress_level: str
    organization_controlled_level: str | None
    default_query_sensitivity: str
    data_origin: str


class ProvidersResponse(BaseModel):
    egress: EgressOut
    providers: list[ProviderOut]


@router.get(
    "/v1/admin/providers",
    response_model=ProvidersResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="The organization's AI egress settings and provider configuration (masked)",
)
async def get_providers(request: Request, principal: CurrentPrincipal) -> ProvidersResponse:
    if not principal.is_admin:
        raise LatticeError(ErrorCode.PERMISSION_DENIED)
    db: Database = request.app.state.db
    secrets: SecretStore = request.app.state.ask_dependencies.secrets
    async with db.tenant_snapshot(principal.organization_id) as session:
        egress: OrgEgress = await org_egress(session, principal.organization_id)
        views = await provider_overview(session, principal.organization_id, secrets)
    return ProvidersResponse.model_validate(
        {
            "egress": {
                "external_egress_level": egress.external_egress_level,
                "organization_controlled_level": egress.organization_controlled_level,
                "default_query_sensitivity": egress.default_query_sensitivity,
                "data_origin": egress.data_origin,
            },
            "providers": [asdict(v) for v in views],
        }
    )
