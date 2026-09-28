"""Ingestion: sources, sync runs, record failures, canonical documents, chunks, and the
egress settings the embedding step needs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27

Phase 4 (docs/implementation-plan.md, docs/ingestion-design.md, docs/data-model.md
§3-7). Everything is tenant-owned, has forced row-level security with the tenant
policy, and references other tenant rows only through composite
``(organization_id, ...)`` foreign keys.

* ``sources``: one configured source per (organization, key). Configuration is
  validated by the connector's config model; it never holds secrets.
* ``source_sync_runs`` / ``sync_record_failures``: operational history. Failures carry
  a fixed error code and templated parameters only -- there is no free-text column, so
  record content cannot leak through them (SEC-14).
* ``documents`` gains its source identity (the idempotency key ``(organization_id,
  source_id, external_id)``), provenance, content and version columns. Phase 2 rows
  cannot be upgraded: no document can exist without the source it came from, and no
  ingestion has run before this revision. The upgrade refuses, with a clear message,
  if any document exists.
* ``access_groups.source_id`` (deferred from Phase 2): source-synced groups now
  reference their source, and are unique per (source, external_ref).
* ``document_grants``: source-origin and admin-origin grants are separate permission
  origins (decision D-12). The one-grant-per-principal uniqueness now includes
  ``origin``, so a source sync that replaces its own grants never collides with, and
  never touches, an administrator's grant on the same principal. Duplicate grants are
  harmless to the policy (any matching grant reads).
* ``chunks``: display text with exact offsets into ``documents.body``, provenance,
  the lexical vector and the embedding. The embedding dimension (1024) is fixed per
  deployment (ADR-008); ``embedding_status`` says why a chunk has no embedding.
* ``org_settings`` / ``model_providers``: the external AI egress level and the
  providers' approved levels (security-model §6). No secrets are stored.

The runtime role (lattice_app) gets SELECT only. Ingestion is an operator tool that
runs as lattice_owner, through row-level security, like the seed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 1024

NEW_TABLES = (
    "sources",
    "source_sync_runs",
    "sync_record_failures",
    "chunks",
    "org_settings",
    "model_providers",
)

_LEVELS = "('internal', 'confidential', 'restricted')"
_AUTHORITY = "('system_of_record', 'working', 'conversational')"
_SLUG = "'^[a-z0-9][a-z0-9-]{0,62}$'"


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (organization_id = lattice_current_org_id()) "
        "WITH CHECK (organization_id = lattice_current_org_id())"
    )


def upgrade() -> None:
    # No Phase 2/3 document can be carried forward: it would have no source.
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
          found boolean := false;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            IF EXISTS (SELECT 1 FROM documents) THEN
              found := true;
            END IF;
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
          IF found THEN
            RAISE EXCEPTION 'documents exist without a source; revision 0005 needs an '
              'empty documents table (documents are created only by ingestion)';
          END IF;
        END
        $$
        """
    )

    # --- sources ---------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE sources (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          key citext NOT NULL,
          connector_type text NOT NULL,
          name text NOT NULL,
          config jsonb NOT NULL DEFAULT '{{}}',
          default_sensitivity text NOT NULL,
          account_scoped boolean NOT NULL DEFAULT false,
          authority text NOT NULL,
          status text NOT NULL DEFAULT 'active',
          -- Keys the opaque references shown for records an admin cannot read
          -- (security-model §10). Never displayed.
          opaque_ref_key bytea NOT NULL
            DEFAULT uuid_send(gen_random_uuid()) || uuid_send(gen_random_uuid()),
          last_synced_at timestamptz,
          last_succeeded_at timestamptz,
          acl_synced_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_sources PRIMARY KEY (id),
          CONSTRAINT fk_sources_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT uq_sources_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT uq_sources_organization_id_key UNIQUE (organization_id, key),
          CONSTRAINT ck_sources_key_format CHECK (key::text ~ {_SLUG}),
          CONSTRAINT ck_sources_connector_type
            CHECK (connector_type IN ('document_repository', 'ticketing', 'chat_export')),
          CONSTRAINT ck_sources_default_sensitivity CHECK (default_sensitivity IN {_LEVELS}),
          CONSTRAINT ck_sources_authority CHECK (authority IN {_AUTHORITY}),
          CONSTRAINT ck_sources_status CHECK (status IN ('active', 'paused', 'deleting')),
          CONSTRAINT ck_sources_config_is_object CHECK (jsonb_typeof(config) = 'object'),
          CONSTRAINT ck_sources_opaque_ref_key_length CHECK (octet_length(opaque_ref_key) = 32)
        )
        """
    )

    # --- sync runs and record failures ---------------------------------------------------
    op.execute(
        """
        CREATE TABLE source_sync_runs (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          source_id uuid NOT NULL,
          mode text NOT NULL,
          status text NOT NULL DEFAULT 'running',
          started_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz,
          records_seen integer NOT NULL DEFAULT 0,
          created integer NOT NULL DEFAULT 0,
          updated integer NOT NULL DEFAULT 0,
          access_updated integer NOT NULL DEFAULT 0,
          unchanged integer NOT NULL DEFAULT 0,
          deleted integer NOT NULL DEFAULT 0,
          failed integer NOT NULL DEFAULT 0,
          chunks_embedded integer NOT NULL DEFAULT 0,
          embeddings_reused integer NOT NULL DEFAULT 0,
          chunks_policy_excluded integer NOT NULL DEFAULT 0,
          embedding_failures integer NOT NULL DEFAULT 0,
          deletion_guard_tripped boolean NOT NULL DEFAULT false,
          error_code text,
          CONSTRAINT pk_source_sync_runs PRIMARY KEY (id),
          CONSTRAINT fk_source_sync_runs_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT fk_source_sync_runs_organization_id_source_id_sources
            FOREIGN KEY (organization_id, source_id)
            REFERENCES sources (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT uq_source_sync_runs_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT ck_source_sync_runs_mode CHECK (mode IN ('full', 'acl_only', 'reindex')),
          CONSTRAINT ck_source_sync_runs_status
            CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'aborted')),
          -- Codes only (e.g. 'source_unavailable'); never free text.
          CONSTRAINT ck_source_sync_runs_error_code
            CHECK (error_code IS NULL OR error_code ~ '^[a-z_]+(:[a-z_]+)?$')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_source_sync_runs_organization_id_source_id_started_at "
        "ON source_sync_runs (organization_id, source_id, started_at)"
    )
    op.execute(
        """
        CREATE TABLE sync_record_failures (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          sync_run_id uuid NOT NULL,
          -- Raw identifiers, for infrastructure operators with database access only.
          external_id text,
          location text,
          -- Keyed hash shown to admins who cannot read the record (security-model §10).
          opaque_ref text NOT NULL,
          stage text NOT NULL,
          error_code text NOT NULL,
          message_params jsonb NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_sync_record_failures PRIMARY KEY (id),
          CONSTRAINT fk_sync_record_failures_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT fk_sync_record_failures_organization_id_sync_run_id_source_sync_runs
            FOREIGN KEY (organization_id, sync_run_id)
            REFERENCES source_sync_runs (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT ck_sync_record_failures_stage CHECK (stage IN
            ('read', 'parse', 'validate', 'normalize', 'map_access', 'chunk', 'embed',
             'persist')),
          CONSTRAINT ck_sync_record_failures_error_code
            CHECK (error_code ~ '^[a-z_]+(:[a-z_]+)?$'),
          CONSTRAINT ck_sync_record_failures_opaque_ref CHECK (opaque_ref ~ '^rec_[0-9a-f]{16}$'),
          CONSTRAINT ck_sync_record_failures_params_is_object
            CHECK (jsonb_typeof(message_params) = 'object')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_sync_record_failures_organization_id_sync_run_id "
        "ON sync_record_failures (organization_id, sync_run_id)"
    )

    # --- access_groups.source_id (deferred from Phase 2) -----------------------------
    op.execute("ALTER TABLE access_groups ADD COLUMN source_id uuid")
    op.execute(
        "ALTER TABLE access_groups ADD CONSTRAINT "
        "fk_access_groups_organization_id_source_id_sources "
        "FOREIGN KEY (organization_id, source_id) REFERENCES sources (organization_id, id) "
        "ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE access_groups ADD CONSTRAINT ck_access_groups_source_id_iff_source "
        "CHECK ((origin = 'source') = (source_id IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE access_groups ADD CONSTRAINT "
        "uq_access_groups_organization_id_source_id_external_ref "
        "UNIQUE (organization_id, source_id, external_ref)"
    )

    # --- documents: source identity, provenance, content, versioning -------------------
    op.execute(
        """
        ALTER TABLE documents
          ADD COLUMN source_id uuid NOT NULL,
          ADD COLUMN external_id text NOT NULL,
          ADD COLUMN document_kind text NOT NULL,
          ADD COLUMN title text NOT NULL,
          ADD COLUMN display_ref text,
          ADD COLUMN uri text,
          ADD COLUMN author_display text,
          ADD COLUMN authority text NOT NULL,
          ADD COLUMN source_created_at timestamptz,
          ADD COLUMN source_updated_at timestamptz NOT NULL,
          ADD COLUMN ingested_at timestamptz NOT NULL DEFAULT now(),
          ADD COLUMN acl_updated_at timestamptz NOT NULL DEFAULT now(),
          ADD COLUMN content_hash bytea NOT NULL,
          ADD COLUMN access_hash bytea NOT NULL,
          ADD COLUMN version integer NOT NULL DEFAULT 1,
          ADD COLUMN body text NOT NULL,
          ADD COLUMN supersedes_external_id text,
          ADD COLUMN supersedes_document_id uuid,
          ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'
        """
    )
    op.execute(
        f"""
        ALTER TABLE documents
          ADD CONSTRAINT fk_documents_organization_id_source_id_sources
            FOREIGN KEY (organization_id, source_id)
            REFERENCES sources (organization_id, id) ON DELETE CASCADE,
          -- The idempotency key (ingestion-design §2).
          ADD CONSTRAINT uq_documents_organization_id_source_id_external_id
            UNIQUE (organization_id, source_id, external_id),
          -- Resolved within the same source by ingestion; the link is cleared by
          -- ingestion before a superseding or superseded document is deleted.
          ADD CONSTRAINT fk_documents_organization_id_supersedes_document_id_documents
            FOREIGN KEY (organization_id, supersedes_document_id)
            REFERENCES documents (organization_id, id),
          ADD CONSTRAINT ck_documents_document_kind
            CHECK (document_kind IN ('doc', 'ticket', 'chat_thread', 'chat_channel_day')),
          ADD CONSTRAINT ck_documents_authority CHECK (authority IN {_AUTHORITY}),
          ADD CONSTRAINT ck_documents_external_id_length
            CHECK (char_length(external_id) BETWEEN 1 AND 256),
          ADD CONSTRAINT ck_documents_uri_https CHECK (uri IS NULL OR uri LIKE 'https://%'),
          ADD CONSTRAINT ck_documents_content_hash_length
            CHECK (octet_length(content_hash) = 32),
          ADD CONSTRAINT ck_documents_access_hash_length CHECK (octet_length(access_hash) = 32),
          ADD CONSTRAINT ck_documents_version_positive CHECK (version >= 1),
          ADD CONSTRAINT ck_documents_not_self_superseding
            CHECK (supersedes_document_id IS DISTINCT FROM id),
          ADD CONSTRAINT ck_documents_metadata_is_object CHECK (jsonb_typeof(metadata) = 'object')
        """
    )
    # A document whose access metadata can no longer be mapped is quarantined: kept,
    # but unreadable (the policy reads only 'active'), never left on stale access.
    op.execute("ALTER TABLE documents DROP CONSTRAINT ck_documents_status")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT ck_documents_status "
        "CHECK (status IN ('active', 'deleted', 'quarantined'))"
    )
    op.execute(
        "CREATE INDEX ix_documents_organization_id_status_sensitivity "
        "ON documents (organization_id, status, sensitivity)"
    )
    op.execute(
        "CREATE INDEX ix_documents_organization_id_source_updated_at "
        "ON documents (organization_id, source_updated_at)"
    )
    # Exact identifier lookup (HAL-4821) for Phase 5's identifier path.
    op.execute(
        "CREATE INDEX ix_documents_organization_id_display_ref "
        "ON documents (organization_id, display_ref)"
    )

    # --- document_grants: separate permission origins (D-12) ------------------------
    for column in ("membership_id", "department_id", "group_id"):
        op.execute(
            f"ALTER TABLE document_grants DROP CONSTRAINT uq_document_grants_document_id_{column}"
        )
        op.execute(
            f"ALTER TABLE document_grants ADD CONSTRAINT "
            f"uq_document_grants_document_id_origin_{column} UNIQUE (document_id, origin, {column})"
        )

    # --- chunks ---------------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE chunks (
          id uuid NOT NULL,
          organization_id uuid NOT NULL,
          document_id uuid NOT NULL,
          ordinal integer NOT NULL,
          text text NOT NULL,
          char_start integer NOT NULL,
          char_end integer NOT NULL,
          heading_path text[] NOT NULL DEFAULT '{{}}',
          source_anchors text[] NOT NULL DEFAULT '{{}}',
          occurred_at timestamptz,
          author_display text,
          content_hash bytea NOT NULL,
          -- Hash of the exact text an embedding provider receives for this chunk
          -- (versioned template + text); an embedding is reused only when it matches.
          embedding_input_hash bytea NOT NULL,
          token_count integer NOT NULL,
          tsv tsvector NOT NULL,
          embedding vector({EMBEDDING_DIMENSIONS}),
          embedding_model text,
          embedding_status text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_chunks PRIMARY KEY (id),
          CONSTRAINT fk_chunks_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT fk_chunks_organization_id_document_id_documents
            FOREIGN KEY (organization_id, document_id)
            REFERENCES documents (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT uq_chunks_organization_id_id UNIQUE (organization_id, id),
          -- Deferred: replacing a document's chunks renumbers kept chunks in place.
          CONSTRAINT uq_chunks_document_id_ordinal UNIQUE (document_id, ordinal)
            DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT ck_chunks_offsets CHECK (char_start >= 0 AND char_end > char_start),
          CONSTRAINT ck_chunks_ordinal CHECK (ordinal >= 0),
          CONSTRAINT ck_chunks_content_hash_length CHECK (octet_length(content_hash) = 32),
          CONSTRAINT ck_chunks_embedding_input_hash_length
            CHECK (octet_length(embedding_input_hash) = 32),
          CONSTRAINT ck_chunks_token_count CHECK (token_count >= 0),
          CONSTRAINT ck_chunks_embedding_status CHECK
            (embedding_status IN ('embedded', 'policy_excluded', 'pending', 'failed')),
          -- A vector exists exactly when the status says so, and always names its model.
          CONSTRAINT ck_chunks_embedding_matches_status CHECK (
            (embedding_status = 'embedded') = (embedding IS NOT NULL)
            AND (embedding IS NULL) = (embedding_model IS NULL)
          )
        )
        """
    )
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")

    # --- AI egress settings (security-model §6) -------------------------------------
    op.execute(
        f"""
        CREATE TABLE org_settings (
          organization_id uuid NOT NULL,
          external_egress_level text NOT NULL DEFAULT 'internal',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_org_settings PRIMARY KEY (organization_id),
          CONSTRAINT fk_org_settings_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT ck_org_settings_external_egress_level
            CHECK (external_egress_level IN {_LEVELS})
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE model_providers (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          purpose text NOT NULL,
          provider text NOT NULL,
          model text NOT NULL,
          deployment text NOT NULL,
          approved_for_level text NOT NULL DEFAULT 'internal',
          retention_mode text NOT NULL,
          training_opt_out boolean NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_model_providers PRIMARY KEY (id),
          CONSTRAINT fk_model_providers_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT uq_model_providers_organization_id_purpose_provider_model
            UNIQUE (organization_id, purpose, provider, model),
          CONSTRAINT ck_model_providers_purpose CHECK (purpose IN ('embedding', 'generation')),
          CONSTRAINT ck_model_providers_deployment
            CHECK (deployment IN ('external_api', 'private')),
          CONSTRAINT ck_model_providers_approved_for_level
            CHECK (approved_for_level IN {_LEVELS})
        )
        """
    )

    for table in ("sources", "org_settings", "model_providers"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION lattice_set_updated_at()"
        )
    for table in NEW_TABLES:
        _tenant_rls(table)
    op.execute(f"GRANT SELECT ON {', '.join(NEW_TABLES)} TO lattice_app")


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS model_providers, org_settings, chunks, sync_record_failures, "
        "source_sync_runs CASCADE"
    )
    # Documents cannot exist without their source columns; ingested content goes.
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            DELETE FROM documents;
            DELETE FROM access_groups WHERE origin = 'source';
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )
    for column in ("membership_id", "department_id", "group_id"):
        op.execute(
            "ALTER TABLE document_grants DROP CONSTRAINT "
            f"uq_document_grants_document_id_origin_{column}"
        )
        op.execute(
            f"ALTER TABLE document_grants ADD CONSTRAINT uq_document_grants_document_id_{column} "
            f"UNIQUE (document_id, {column})"
        )
    op.execute("ALTER TABLE documents DROP CONSTRAINT ck_documents_status")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT ck_documents_status "
        "CHECK (status IN ('active', 'deleted'))"
    )
    op.execute("DROP INDEX ix_documents_organization_id_display_ref")
    op.execute("DROP INDEX ix_documents_organization_id_source_updated_at")
    op.execute("DROP INDEX ix_documents_organization_id_status_sensitivity")
    op.execute(
        """
        ALTER TABLE documents
          DROP CONSTRAINT fk_documents_organization_id_supersedes_document_id_documents,
          DROP CONSTRAINT fk_documents_organization_id_source_id_sources,
          DROP COLUMN source_id, DROP COLUMN external_id, DROP COLUMN document_kind,
          DROP COLUMN title, DROP COLUMN display_ref, DROP COLUMN uri,
          DROP COLUMN author_display, DROP COLUMN authority, DROP COLUMN source_created_at,
          DROP COLUMN source_updated_at, DROP COLUMN ingested_at, DROP COLUMN acl_updated_at,
          DROP COLUMN content_hash, DROP COLUMN access_hash, DROP COLUMN version,
          DROP COLUMN body, DROP COLUMN supersedes_external_id,
          DROP COLUMN supersedes_document_id, DROP COLUMN metadata
        """
    )
    op.execute(
        "ALTER TABLE access_groups "
        "DROP CONSTRAINT uq_access_groups_organization_id_source_id_external_ref, "
        "DROP CONSTRAINT ck_access_groups_source_id_iff_source, "
        "DROP CONSTRAINT fk_access_groups_organization_id_source_id_sources, "
        "DROP COLUMN source_id"
    )
    op.execute("DROP TABLE IF EXISTS sources")
