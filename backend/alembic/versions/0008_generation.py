"""Answering: provider configuration for generation, credential references, query traces,
audit events.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-29
Reversible: no (the downgrade drops traces, audit events and credential references)
Locks: ACCESS EXCLUSIVE on model_providers; new tables

Phase 6 (docs/security-model.md §5, §6, §8; ADR-011, D-16; data-model §7).

* ``model_providers`` becomes the organization's provider configuration for both
  purposes: ``endpoint`` (a local or organization-controlled server; NULL for vendor
  APIs), ``enabled`` (disable without deleting history). At most one *enabled*
  generation configuration per organization (the one Ask uses).
* ``provider_credentials``: organization-owned credentials **by reference only**. A row
  names a provider and a secret reference (``env:NAME`` / ``memory:NAME``, resolved by
  ``lattice.providers.secrets``), the key's declared data-use tier, and its lifecycle
  (``active`` / ``revoked``). No column holds a secret value; the reference format is
  checked here too. At most one active credential per organization and provider:
  rotation revokes the old row and adds a new one; history is kept.
* ``query_traces``: one row per Ask -- identifiers, versions, codes, counts, timings,
  the validated answer and the asker's question (retention-limited, asker-only). Never
  the prompt, evidence text, rejected claim text or any secret.
* ``audit_events``: append-only record of administrative changes (provider approval,
  enablement, credentials, egress levels) with actor, before/after and a reason.

Runtime role (``lattice_app``): SELECT on ``provider_credentials``; SELECT + INSERT on
``query_traces`` (no UPDATE/DELETE); nothing on ``audit_events`` (written by the
operator CLI). All three tables force row-level security.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEVELS = "('internal', 'confidential', 'restricted')"


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (organization_id = lattice_current_org_id()) "
        "WITH CHECK (organization_id = lattice_current_org_id())"
    )


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE model_providers
          ADD COLUMN endpoint text,
          ADD COLUMN enabled boolean NOT NULL DEFAULT true,
          ADD CONSTRAINT ck_model_providers_endpoint
            CHECK (endpoint IS NULL OR endpoint ~ '^https?://[^\\s@?#]+$')
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_model_providers_one_enabled_generation "
        "ON model_providers (organization_id) WHERE purpose = 'generation' AND enabled"
    )

    op.execute(
        """
        CREATE TABLE provider_credentials (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          provider text NOT NULL,
          secret_ref text NOT NULL,
          data_use text,
          status text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          revoked_at timestamptz,
          CONSTRAINT pk_provider_credentials PRIMARY KEY (id),
          CONSTRAINT uq_provider_credentials_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT fk_provider_credentials_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT ck_provider_credentials_secret_ref
            CHECK (secret_ref ~ '^(env|memory):[A-Za-z0-9_.-]{1,96}$'),
          CONSTRAINT ck_provider_credentials_data_use
            CHECK (data_use IS NULL OR data_use IN ('unpaid', 'paid')),
          CONSTRAINT ck_provider_credentials_status CHECK (status IN ('active', 'revoked')),
          CONSTRAINT ck_provider_credentials_revoked
            CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_provider_credentials_one_active "
        "ON provider_credentials (organization_id, provider) WHERE status = 'active'"
    )

    op.execute(
        f"""
        CREATE TABLE query_traces (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          membership_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          question_text text NOT NULL,
          question_sensitivity text NOT NULL,
          scope jsonb NOT NULL DEFAULT '{{}}',
          authz_snapshot jsonb NOT NULL,
          retrieval jsonb NOT NULL,
          evidence_chunk_ids uuid[] NOT NULL DEFAULT '{{}}',
          prompt_chunk_ids uuid[] NOT NULL DEFAULT '{{}}',
          cited_chunk_ids uuid[] NOT NULL DEFAULT '{{}}',
          egress jsonb NOT NULL,
          validation jsonb NOT NULL,
          answer jsonb,
          status text NOT NULL,
          status_reason text,
          error_code text,
          timings_ms jsonb NOT NULL,
          usage jsonb NOT NULL DEFAULT '{{}}',
          CONSTRAINT pk_query_traces PRIMARY KEY (id),
          CONSTRAINT fk_query_traces_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT fk_query_traces_membership
            FOREIGN KEY (organization_id, membership_id)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT ck_query_traces_question_sensitivity
            CHECK (question_sensitivity IN {_LEVELS}),
          CONSTRAINT ck_query_traces_status CHECK (status IN ('answered', 'partial',
            'insufficient_evidence', 'evidence_only', 'ambiguous', 'unsupported_request',
            'failed'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_query_traces_organization_id_membership_id_created_at "
        "ON query_traces (organization_id, membership_id, created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE audit_events (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          -- clock_timestamp(): events of one transaction keep their order.
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          actor text NOT NULL,
          action text NOT NULL,
          target jsonb NOT NULL,
          before jsonb,
          after jsonb,
          reason text NOT NULL,
          CONSTRAINT pk_audit_events PRIMARY KEY (id),
          CONSTRAINT fk_audit_events_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT ck_audit_events_reason CHECK (length(btrim(reason)) BETWEEN 1 AND 500)
        )
        """
    )
    # Append-only: an event is never rewritten (deletion only with its organization).
    op.execute(
        """
        CREATE FUNCTION lattice_audit_events_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_events is append-only';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_audit_events_immutable BEFORE UPDATE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION lattice_audit_events_immutable()"
    )

    for table in ("provider_credentials", "query_traces", "audit_events"):
        _tenant_rls(table)
    op.execute("GRANT SELECT ON provider_credentials TO lattice_app")
    op.execute("GRANT SELECT, INSERT ON query_traces TO lattice_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_events, query_traces, provider_credentials CASCADE")
    op.execute("DROP FUNCTION IF EXISTS lattice_audit_events_immutable()")
    op.execute("DROP INDEX IF EXISTS uq_model_providers_one_enabled_generation")
    op.execute(
        """
        ALTER TABLE model_providers
          DROP CONSTRAINT ck_model_providers_endpoint,
          DROP COLUMN endpoint,
          DROP COLUMN enabled
        """
    )
