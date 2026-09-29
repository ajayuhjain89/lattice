"""HTTP request/response models. The OpenAPI schema generated from these is the
canonical API contract (docs/architecture.md §2)."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lattice.identity.sessions import Principal

Capability = Literal["admin"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]


class LiveResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready"]
    database: Literal["ok"]
    schema_revision: Literal["ok"]


class DevLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=3, max_length=320, description="Seeded persona email.")
    organization: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        description="Organization slug; required only when the persona has several.",
    )


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str


class OrganizationOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str


class MembershipOut(BaseModel):
    id: uuid.UUID
    role_label: str | None = Field(description="Display label only; not a permission.")


class MeResponse(BaseModel):
    user: UserOut
    organization: OrganizationOut
    membership: MembershipOut
    capabilities: list[Capability] = Field(
        description="Administrative capabilities. None of them grants access to content."
    )

    @classmethod
    def from_principal(cls, principal: Principal) -> "MeResponse":
        return cls(
            user=UserOut(
                id=principal.user_id, email=principal.email, display_name=principal.display_name
            ),
            organization=OrganizationOut(
                id=principal.organization_id,
                slug=principal.organization_slug,
                name=principal.organization_name,
            ),
            membership=MembershipOut(id=principal.membership_id, role_label=principal.role_label),
            capabilities=["admin"] if principal.is_admin else [],
        )
