"""Shared limits and retention (Phase 11, P11.4; D-24, plan §10, §12).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-05
Reversible: no (the downgrade drops ask_slots and pre_auth_rate_limits)
Locks: new tables; SHARE on query_traces while its index builds; brief ACCESS EXCLUSIVE
  on the tables that gain policies

* ``ask_slots`` -- the in-flight Ask limit shared by every API process (D-24): one row
  per (organization, membership, slot) holding a lease until ``expires_at`` (the
  generation deadline plus a retrieval margin). A request takes a free or expired slot
  with a conditional upsert and releases it by its lease ID; a crashed process's slot
  frees itself at expiry. Tenant row-level security as everywhere.
* ``pre_auth_rate_limits`` -- the pre-authentication limit (development sign-in, OIDC
  start and callback, failed SCIM authentication) shared by every API process (D-24
  amended). Not a tenant table (no organization is known yet): no login role has any
  privilege on it; ``lattice_app`` counts through ``lattice_pre_auth_hit`` and the worker
  deletes old windows through ``lattice_pre_auth_retention``, both ``SECURITY DEFINER``
  functions owned by the NOLOGIN ``lattice_auth`` (the pre-auth pattern of 0001/0013).
  ``client_key`` is an HMAC-SHA-256 of the client address under
  ``LATTICE_CLIENT_KEY_SECRET``: neither the table nor a backup holds an address.
* Retention (plan §12) with **database-enforced floors**: ``lattice_worker`` may delete
  only rows older than each class's floor -- permissive policies let it find and delete
  them across organizations, restrictive policies hold the floor even with an
  organization bound -- and reads only the identifying and timestamp columns it needs
  (no content). Floors:

  =========================  ===================================================
  query_traces               created more than 90 days ago (the retention itself:
                             a shorter one needs a migration and a product decision)
  source_sync_runs (+ their  started more than 180 days ago **and** a newer run of
  sync_record_failures)      the same source exists (the latest run is kept)
  jobs (finished)            finished more than 30 days ago (0011's rule, now also
                             a floor)
  sessions                   expired or revoked more than 30 days ago
  oidc_login_attempts        expired more than 1 day ago
  rate_limit_counters        window started more than 2 days ago
  pre_auth_rate_limits       window started more than 2 days ago (in the function)
  ask_slots                  lease expired more than 1 hour ago
  =========================  ===================================================

  ``audit_events``, directory identities, removed memberships, SCIM tokens, connections
  and provider history are never deleted by retention (no privilege, no policy).
* ``query_traces (created_at)`` index for the retention scan.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table -> (floor condition, columns the worker may read to find eligible rows)
RETENTION = {
    "query_traces": (
        "created_at < now() - interval '90 days'",
        "id, organization_id, created_at",
    ),
    "sessions": (
        "(expires_at < now() - interval '30 days' OR revoked_at < now() - interval '30 days')",
        "id, organization_id, expires_at, revoked_at",
    ),
    "oidc_login_attempts": (
        "expires_at < now() - interval '1 day'",
        "id, organization_id, expires_at",
    ),
    "rate_limit_counters": (
        "window_start < now() - interval '2 days'",
        "organization_id, bucket, subject, window_start",
    ),
    "ask_slots": (
        "expires_at < now() - interval '1 hour'",
        "organization_id, membership_id, slot, expires_at",
    ),
}
_JOBS_FLOOR = "status IN ('succeeded', 'failed') AND finished_at < now() - interval '30 days'"
_RUN_AGE = "started_at < now() - interval '180 days'"
_RUN_SUPERSEDED = (
    "EXISTS (SELECT 1 FROM source_sync_runs newer "
    "WHERE newer.organization_id = source_sync_runs.organization_id "
    "AND newer.source_id = source_sync_runs.source_id "
    "AND newer.started_at > source_sync_runs.started_at)"
)
_FUNCTIONS = {
    "lattice_pre_auth_hit(text, bytea, integer, integer, boolean)": "lattice_app",
    "lattice_pre_auth_retention(integer)": "lattice_worker",
}


def upgrade() -> None:
    # --- ask_slots ---------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE ask_slots (
          organization_id uuid NOT NULL,
          membership_id uuid NOT NULL,
          slot smallint NOT NULL,
          lease_id uuid NOT NULL,
          expires_at timestamptz NOT NULL,
          CONSTRAINT pk_ask_slots PRIMARY KEY (organization_id, membership_id, slot),
          CONSTRAINT fk_ask_slots_membership FOREIGN KEY (organization_id, membership_id)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT ck_ask_slots_slot CHECK (slot >= 0 AND slot < 100)
        )
        """
    )
    op.execute("ALTER TABLE ask_slots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ask_slots FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON ask_slots "
        "USING (organization_id = lattice_current_org_id()) "
        "WITH CHECK (organization_id = lattice_current_org_id())"
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON ask_slots TO lattice_app")
    op.execute("GRANT UPDATE (lease_id, expires_at) ON ask_slots TO lattice_app")

    # --- pre_auth_rate_limits (no login role has a privilege) ----------------------------
    op.execute(
        """
        CREATE TABLE pre_auth_rate_limits (
          bucket text NOT NULL,
          client_key bytea NOT NULL,
          window_start timestamptz NOT NULL,
          count integer NOT NULL,
          CONSTRAINT pk_pre_auth_rate_limits PRIMARY KEY (bucket, client_key),
          CONSTRAINT ck_pre_auth_rate_limits_bucket CHECK (bucket ~ '^[a-z_]{1,32}$'),
          CONSTRAINT ck_pre_auth_rate_limits_client_key CHECK (octet_length(client_key) = 32),
          CONSTRAINT ck_pre_auth_rate_limits_count CHECK (count >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_pre_auth_rate_limits_window_start ON pre_auth_rate_limits (window_start)"
    )
    op.execute("ALTER TABLE pre_auth_rate_limits ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pre_auth_rate_limits FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY auth_counters ON pre_auth_rate_limits TO lattice_auth "
        "USING (true) WITH CHECK (true)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON pre_auth_rate_limits TO lattice_auth")
    op.execute(
        """
        CREATE FUNCTION lattice_pre_auth_hit(
          p_bucket text, p_client_key bytea, p_limit integer, p_window_seconds integer,
          p_count boolean
        ) RETURNS integer
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_window timestamptz;
          v_start timestamptz;
          v_count integer;
          v_counted integer;
        BEGIN
          IF p_bucket IS NULL OR p_bucket !~ '^[a-z_]{1,32}$'
             OR p_client_key IS NULL OR octet_length(p_client_key) <> 32
             OR p_limit IS NULL OR p_limit < 1
             OR p_window_seconds IS NULL OR p_window_seconds < 1 OR p_window_seconds > 86400
             OR p_count IS NULL THEN
            RAISE EXCEPTION 'invalid pre-authentication limit'
              USING ERRCODE = 'invalid_parameter_value';
          END IF;
          v_window := to_timestamp(
            floor(extract(epoch FROM now()) / p_window_seconds) * p_window_seconds);
          IF p_count THEN
            INSERT INTO public.pre_auth_rate_limits AS c (bucket, client_key, window_start, count)
            VALUES (p_bucket, p_client_key, v_window, 1)
            ON CONFLICT (bucket, client_key) DO UPDATE SET
              count = CASE WHEN c.window_start >= EXCLUDED.window_start
                           THEN c.count + 1 ELSE 1 END,
              window_start = GREATEST(c.window_start, EXCLUDED.window_start)
            WHERE c.window_start < EXCLUDED.window_start OR c.count < p_limit
            RETURNING c.count INTO v_counted;
            IF v_counted IS NOT NULL THEN
              RETURN 0;
            END IF;
          END IF;
          SELECT window_start, count INTO v_start, v_count
          FROM public.pre_auth_rate_limits
          WHERE bucket = p_bucket AND client_key = p_client_key;
          IF v_start IS NULL OR v_start < v_window OR v_count < p_limit THEN
            RETURN 0;
          END IF;
          RETURN greatest(1, ceil(extract(epoch FROM
            v_start + make_interval(secs => p_window_seconds) - now()))::integer);
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION lattice_pre_auth_retention(p_batch integer)
        RETURNS integer
        LANGUAGE sql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          WITH gone AS (
            DELETE FROM public.pre_auth_rate_limits
            WHERE (bucket, client_key) IN (
              SELECT bucket, client_key FROM public.pre_auth_rate_limits
              WHERE window_start < now() - interval '2 days'
              ORDER BY window_start
              LIMIT least(greatest(p_batch, 1), 10000))
              AND window_start < now() - interval '2 days'
            RETURNING 1)
          SELECT count(*)::integer FROM gone
        $$
        """
    )
    # As in 0001/0013: set the ACL while lattice_owner owns the functions, then transfer
    # them with CREATE held only inside this transaction.
    for function, grantee in _FUNCTIONS.items():
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {grantee}")
    op.execute("GRANT CREATE ON SCHEMA public TO lattice_auth")
    for function in _FUNCTIONS:
        op.execute(f"ALTER FUNCTION {function} OWNER TO lattice_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM lattice_auth")

    # --- retention: the worker deletes only rows past each floor -------------------------
    for table, (floor, columns) in RETENTION.items():
        op.execute(f"GRANT SELECT ({columns}), DELETE ON {table} TO lattice_worker")
        op.execute(
            f"CREATE POLICY worker_retention_find ON {table} FOR SELECT TO lattice_worker "
            f"USING ({floor})"
        )
        op.execute(
            f"CREATE POLICY worker_retention ON {table} FOR DELETE TO lattice_worker "
            f"USING ({floor})"
        )
        op.execute(
            f"CREATE POLICY worker_retention_floor ON {table} AS RESTRICTIVE FOR DELETE "
            f"TO lattice_worker USING ({floor})"
        )
    op.execute(
        "CREATE POLICY worker_retention_floor ON jobs AS RESTRICTIVE FOR DELETE "
        f"TO lattice_worker USING ({_JOBS_FLOOR})"
    )
    # Sync runs: found across organizations by age, deleted with the organization bound
    # (the tenant policy shows the newer run that makes an old one deletable).
    op.execute("GRANT DELETE ON source_sync_runs TO lattice_worker")
    op.execute(
        "CREATE POLICY worker_retention_find ON source_sync_runs FOR SELECT TO lattice_worker "
        f"USING ({_RUN_AGE})"
    )
    op.execute(
        "CREATE POLICY worker_retention_floor ON source_sync_runs AS RESTRICTIVE FOR DELETE "
        f"TO lattice_worker USING ({_RUN_AGE} AND {_RUN_SUPERSEDED})"
    )
    op.execute("CREATE INDEX ix_query_traces_created_at ON query_traces (created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_query_traces_created_at")
    op.execute("DROP POLICY worker_retention_floor ON source_sync_runs")
    op.execute("DROP POLICY worker_retention_find ON source_sync_runs")
    op.execute("REVOKE DELETE ON source_sync_runs FROM lattice_worker")
    op.execute("DROP POLICY worker_retention_floor ON jobs")
    for table, (_, columns) in RETENTION.items():
        if table == "ask_slots":
            continue
        for policy in ("worker_retention_floor", "worker_retention", "worker_retention_find"):
            op.execute(f"DROP POLICY {policy} ON {table}")
        op.execute(f"REVOKE SELECT ({columns}), DELETE ON {table} FROM lattice_worker")  # noqa: S608
    for function in _FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")
    op.execute("DROP TABLE pre_auth_rate_limits")
    op.execute("DROP TABLE ask_slots")
