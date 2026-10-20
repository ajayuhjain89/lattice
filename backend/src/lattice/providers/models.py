"""Configured model providers and their approved AI egress level
(docs/security-model.md §6, docs/data-model.md §7; migration 0005).

Secrets are never stored here. ``provider_credentials`` holds only a *reference* to
each organization-owned credential (``lattice.providers.secrets``), resolved at
invocation time after the egress policy has allowed the call.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from lattice.db.base import Base


class ModelProvider(Base):
    __tablename__ = "model_providers"
    __table_args__ = (
        UniqueConstraint("organization_id", "purpose", "provider", "model"),
        # At most one enabled generation configuration per organization (migration 0008).
        Index(
            "uq_model_providers_one_enabled_generation",
            "organization_id",
            unique=True,
            postgresql_where=text("purpose = 'generation' AND enabled"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    deployment: Mapped[str] = mapped_column(Text)
    approved_for_level: Mapped[str] = mapped_column(Text, server_default="internal")
    # Set by an explicit administrator approval; required for organization_controlled.
    approved_at: Mapped[datetime | None]
    retention_mode: Mapped[str] = mapped_column(Text)
    training_opt_out: Mapped[bool]
    # Phase 6 (migration 0008): a local or organization-controlled server's URL (NULL
    # for vendor APIs), and whether the configuration may be used at all.
    endpoint: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # 0021: the latest administrator test (facts, not audited) and the credential it used.
    last_test_at: Mapped[datetime | None]
    last_test_result: Mapped[str | None] = mapped_column(Text)
    last_test_credential_id: Mapped[uuid.UUID | None]


class EgressPolicyVersion(Base):
    """A counter per organization raised by every write to its egress policy
    (``org_settings``, the policy columns of ``model_providers``) by any writer (0021): a
    preview and a step-up grant are bound to the exact policy they were computed on."""

    __tablename__ = "egress_policy_versions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    changed_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProviderCredential(Base):
    """An organization-owned provider credential, by reference (migration 0008)."""

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        Index(
            "uq_provider_credentials_one_active",
            "organization_id",
            "provider",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(Text)
    secret_ref: Mapped[str] = mapped_column(Text)
    data_use: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]
