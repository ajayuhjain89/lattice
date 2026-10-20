"""The organization's setup record and settings (Phase 12, P12.1; phase-12-plan C12,
§8). Administrators only; the capability is checked before anything is read.

* ``GET /v1/admin/setup`` -- the organization and one entry per setup category, each
  derived by the backend (``lattice.setup.derive``): state, reason, fixed message, safe
  facts and the page that owns it. Not audited (a read).
* ``PATCH /v1/admin/organization`` -- rename, with a reason and the current version
  (409 otherwise); audited (``organization.updated``) in the same transaction, enforced
  by the database.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from lattice.api.deps import CurrentPrincipal, Limits
from lattice.db.engine import Database
from lattice.errors import ErrorCode, ErrorResponse, LatticeError
from lattice.identity.sessions import Principal
from lattice.setup.derive import CategoryKey, FactValue, State, derive
from lattice.setup.facts import gather
from lattice.setup.organization import (
    OrganizationRecord,
    organization_record,
    rename_organization,
)

router = APIRouter(tags=["admin"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
}


class SetupAction(BaseModel):
    kind: Literal["link"]
    href: str
    label: str


class SetupCategory(BaseModel):
    key: CategoryKey
    state: State
    reason: str
    message: str
    facts: dict[str, FactValue]
    action: SetupAction | None


class SetupOrganization(BaseModel):
    slug: str
    name: str
    status: str
    data_origin: str
    version: datetime


class SetupStatus(BaseModel):
    organization: SetupOrganization
    categories: list[SetupCategory]


class OrganizationChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    version: datetime
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ord(c) < 0x20 or ord(c) == 0x7F for c in cleaned):
            raise ValueError("must have text and no control characters")
        return cleaned

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise LatticeError(ErrorCode.PERMISSION_DENIED)


def _organization(record: OrganizationRecord) -> SetupOrganization:
    return SetupOrganization(
        slug=record.slug,
        name=record.name,
        status=record.status,
        data_origin=record.data_origin,
        version=record.version,
    )


@router.get(
    "/v1/admin/setup",
    response_model=SetupStatus,
    responses=_ERRORS,
    summary="The organization's setup record, derived by the backend (administrators)",
)
async def get_setup(request: Request, principal: CurrentPrincipal) -> SetupStatus:
    _require_admin(principal)
    deps = request.app.state.ask_dependencies
    org = principal.organization_id
    async with _db(request).tenant_snapshot(org) as session:
        facts = await gather(session, org, factory=deps.generation, secrets=deps.secrets)
    record = await organization_record(_db(request), org)
    return SetupStatus(
        organization=_organization(record),
        categories=[
            SetupCategory(
                key=c.key,
                state=c.state,
                reason=c.reason,
                message=c.message,
                facts=c.facts,
                action=None
                if c.action is None
                else SetupAction(kind=c.action.kind, href=c.action.href, label=c.action.label),
            )
            for c in derive(facts)
        ],
    )


@router.patch(
    "/v1/admin/organization",
    response_model=SetupOrganization,
    responses=_WRITE_ERRORS,
    summary="Rename the organization, with a reason (administrators; audited)",
    description="`version` must be the organization's current version (409 otherwise). "
    "Requires the X-Lattice-Request: 1 header.",
)
async def patch_organization(
    body: OrganizationChange,
    request: Request,
    principal: CurrentPrincipal,
    limits: Limits,
) -> SetupOrganization:
    _require_admin(principal)
    await limits.limiter.hit(
        principal.organization_id, principal.membership_id, limits.access_write
    )
    record = await rename_organization(
        _db(request),
        organization_id=principal.organization_id,
        actor_membership_id=principal.membership_id,
        name=body.name,
        version=body.version,
        reason=body.reason,
    )
    return _organization(record)
