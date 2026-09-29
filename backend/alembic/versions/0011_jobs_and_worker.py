"""Background jobs and the worker's runtime role.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29

Phase 8 (implementation-plan §4, architecture §1; ADR-013).

* ``jobs``: the Postgres queue for syncs requested from the web app. One row per
  request: organization, kind (``source_sync``), source, mode, the requesting
  membership, status (``queued`` → ``running`` → ``succeeded`` | ``failed``), attempts
  and a lease, the sync run it produced, a fixed error code. No free text, no content.
  At most one queued or running job per source (a repeated request returns the same
  job). Forced row-level security: the API sees and inserts its own organization's
  rows only.
* ``lattice_worker`` (created by the bootstrap, like the other login roles) is the
  worker's runtime role. It is not an owner and has no BYPASSRLS. It may *see* and
  *claim* every organization's jobs (``worker_queue``, ``worker_claim``: the queue holds
  only IDs and codes), delete finished jobs after 30 days (``worker_retention``), and
  write the ingestion tables **through the tenant policies**, one organization per job:
  documents, chunks, sync runs and failures, and only the sync timestamps of sources
  (never their configuration or export path); source-origin grants and
  groups only (restrictive policies: an administrator's grant or group is never touched,
  D-12); the ``authz_updated_at`` column of memberships; new embedding provider records
  approved for ``internal`` only. It has nothing on sessions, identities, traces, audit
  events, provider credentials or organization settings.
* ``lattice_app`` keeps its Phase 6 privileges plus SELECT and INSERT on ``jobs``.
* ``query_traces`` gets an ``(organization_id, created_at DESC)`` index for the
  administrator's operational activity list.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Read by a sync (directory, access state, configuration, egress settings).
WORKER_READS = (
    "organizations",
    "users",
    "memberships",
    "departments",
    "membership_departments",
    "accounts",
    "account_assignments",
    "access_groups",
    "access_group_members",
    "documents",
    "document_grants",
    "chunks",
    "sources",
    "source_sync_runs",
    "sync_record_failures",
    "org_settings",
    "model_providers",
)
# Written by a sync (INSERT, UPDATE, DELETE unless narrowed below).
WORKER_WRITES = {
    "documents": "INSERT, UPDATE, DELETE",
    "chunks": "INSERT, UPDATE, DELETE",
    "document_grants": "INSERT, DELETE",
    "access_groups": "INSERT, UPDATE, DELETE",
    "access_group_members": "INSERT, DELETE",
    "source_sync_runs": "INSERT, UPDATE",
    "sync_record_failures": "INSERT",
    "model_providers": "INSERT",
}
_SOURCE_GROUP = (
    "EXISTS (SELECT 1 FROM access_groups g WHERE g.id = group_id AND g.origin = 'source')"
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lattice_worker') THEN
            RAISE EXCEPTION 'Role lattice_worker is required (Phase 8). Set '
              'LATTICE_WORKER_PASSWORD in .env, then run make db-up and make db-bootstrap '
              '(docs/runbook.md section 1).';
          END IF;
        END
        $$
        """
    )
    op.execute(
        r"""
        CREATE TABLE jobs (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          kind text NOT NULL,
          source_id uuid NOT NULL,
          mode text NOT NULL DEFAULT 'full',
          requested_by uuid NOT NULL,
          status text NOT NULL DEFAULT 'queued',
          attempts integer NOT NULL DEFAULT 0,
          max_attempts integer NOT NULL DEFAULT 3,
          run_after timestamptz NOT NULL DEFAULT now(),
          lease_expires_at timestamptz,
          sync_run_id uuid,
          error_code text,
          created_at timestamptz NOT NULL DEFAULT now(),
          started_at timestamptz,
          finished_at timestamptz,
          CONSTRAINT pk_jobs PRIMARY KEY (id),
          CONSTRAINT uq_jobs_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT fk_jobs_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT fk_jobs_organization_id_source_id_sources
            FOREIGN KEY (organization_id, source_id)
            REFERENCES sources (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_jobs_organization_id_requested_by_memberships
            FOREIGN KEY (organization_id, requested_by)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_jobs_organization_id_sync_run_id_source_sync_runs
            FOREIGN KEY (organization_id, sync_run_id)
            REFERENCES source_sync_runs (organization_id, id) ON DELETE SET NULL (sync_run_id),
          CONSTRAINT ck_jobs_kind CHECK (kind IN ('source_sync')),
          CONSTRAINT ck_jobs_mode CHECK (mode IN ('full', 'acl_only')),
          CONSTRAINT ck_jobs_status CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
          CONSTRAINT ck_jobs_attempts CHECK (attempts >= 0 AND attempts <= max_attempts + 1),
          CONSTRAINT ck_jobs_max_attempts CHECK (max_attempts BETWEEN 1 AND 10),
          CONSTRAINT ck_jobs_lease CHECK ((status = 'running') = (lease_expires_at IS NOT NULL)),
          CONSTRAINT ck_jobs_finished
            CHECK ((status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)),
          CONSTRAINT ck_jobs_error_code
            CHECK (error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_:.-]{0,99}$')
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_jobs_one_active_per_source ON jobs (organization_id, source_id) "
        "WHERE status IN ('queued', 'running')"
    )
    op.execute("CREATE INDEX ix_jobs_due ON jobs (run_after) WHERE status = 'queued'")
    op.execute("CREATE INDEX ix_jobs_lease ON jobs (lease_expires_at) WHERE status = 'running'")
    op.execute(
        "CREATE INDEX ix_jobs_finished ON jobs (finished_at) "
        "WHERE status IN ('succeeded', 'failed')"
    )
    op.execute(
        "CREATE INDEX ix_jobs_organization_id_source_id ON jobs (organization_id, source_id)"
    )
    op.execute("ALTER TABLE jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON jobs "
        "USING (organization_id = lattice_current_org_id()) "
        "WITH CHECK (organization_id = lattice_current_org_id())"
    )
    op.execute("CREATE POLICY worker_queue ON jobs FOR SELECT TO lattice_worker USING (true)")
    op.execute(
        "CREATE POLICY worker_claim ON jobs FOR UPDATE TO lattice_worker "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY worker_retention ON jobs FOR DELETE TO lattice_worker "
        "USING (status IN ('succeeded', 'failed') AND finished_at < now() - interval '30 days')"
    )
    op.execute(
        "GRANT SELECT, INSERT (organization_id, kind, source_id, mode, requested_by) "
        "ON jobs TO lattice_app"
    )
    op.execute(
        "GRANT SELECT, DELETE, UPDATE (status, attempts, run_after, lease_expires_at, "
        "sync_run_id, error_code, started_at, finished_at) ON jobs TO lattice_worker"
    )

    op.execute(
        "CREATE INDEX ix_query_traces_organization_id_created_at "
        "ON query_traces (organization_id, created_at DESC)"
    )

    # --- the worker's ingestion privileges, through the tenant policies ----------------
    op.execute(f"GRANT SELECT ON {', '.join(WORKER_READS)} TO lattice_worker")
    op.execute("GRANT SELECT ON alembic_version TO lattice_worker")
    for table, privileges in WORKER_WRITES.items():
        op.execute(f"GRANT {privileges} ON {table} TO lattice_worker")
    op.execute("GRANT UPDATE (authz_updated_at) ON memberships TO lattice_worker")
    # A worker sync never writes a source's configuration or export path (ADR-013).
    op.execute(
        "GRANT UPDATE (last_synced_at, last_succeeded_at, acl_synced_at) ON sources "
        "TO lattice_worker"
    )
    # Restrictive: ANDed with the tenant policy. A sync writes its own origin only.
    for table, condition in (
        ("document_grants", "origin = 'source'"),
        ("access_groups", "origin = 'source'"),
        ("access_group_members", _SOURCE_GROUP),
    ):
        for command in ("INSERT", "UPDATE", "DELETE"):
            clause = (
                f"WITH CHECK ({condition})"
                if command == "INSERT"
                else f"USING ({condition})"
                + (f" WITH CHECK ({condition})" if command == "UPDATE" else "")
            )
            op.execute(
                f"CREATE POLICY worker_source_origin_{command.lower()} ON {table} "
                f"AS RESTRICTIVE FOR {command} TO lattice_worker {clause}"
            )
    op.execute(
        "CREATE POLICY worker_records_embedding_only ON model_providers AS RESTRICTIVE "
        "FOR INSERT TO lattice_worker WITH CHECK (purpose = 'embedding' "
        "AND approved_for_level = 'internal' AND approved_at IS NULL)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_query_traces_organization_id_created_at")
    op.execute("DROP POLICY worker_records_embedding_only ON model_providers")
    for table in ("document_grants", "access_groups", "access_group_members"):
        for command in ("insert", "update", "delete"):
            op.execute(f"DROP POLICY worker_source_origin_{command} ON {table}")
    op.execute("REVOKE ALL ON memberships FROM lattice_worker")
    for table in (*WORKER_READS, "alembic_version"):
        op.execute(f"REVOKE ALL ON {table} FROM lattice_worker")
    op.execute("DROP TABLE jobs")
