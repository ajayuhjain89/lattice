"""Customer-operable export sources, imports and first sync (Phase 12, P12.6; ADR-017;
phase-12-plan C7, C8, C9, C11, C12).

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-18
Reversible: no (the downgrade drops every import and the sources' link to them)
Locks: new table; brief ACCESS EXCLUSIVE on sources, jobs, source_sync_runs and
  audit_events (columns, checks, policies, triggers); no row is rewritten

No broad write is granted. ``lattice_app`` still has ``SELECT`` only on ``sources``; it
creates, edits, pauses, resumes and disconnects sources, and registers imports, only
through fenced functions (``SECURITY DEFINER``, owned by ``lattice_owner``, search path
pinned, ``EXECUTE`` for ``lattice_app`` only) that re-check an active administrator of the
bound organization and refuse with SQLSTATE ``LA001`` and a fixed reason:

* ``lattice_create_source(actor, key, type, name, sensitivity, departments, authority,
  account_scoped)`` -- an export source **managed by the organization**: the function
  builds its configuration from validated values (default departments by key, no channel
  settings) and never accepts a path. Creating a source grants nobody anything.
* ``lattice_update_source(actor, source, version, change)`` -- name, default sensitivity,
  default departments, authority, account scoping, channel sensitivities (chat, by channel
  ID). Never the connector type, the key, a path or an import. Operator-managed sources
  (``lattice sync --sources``) stay the operator file's (``operator_managed``).
* ``lattice_set_source_paused(actor, source, version, paused)`` -- ``active`` <-> ``paused``
  (C15: the status existed since 0005 and nothing set it). A paused source refuses
  requested syncs; its documents stay searchable.
* ``lattice_disconnect_source(actor, source, version)`` -- the existing disconnect
  (``ingestion.lifecycle``): refused while a sync runs (``sync_running``); queued jobs end
  ``source_disconnected``; the source becomes ``deleting`` and every document ``deleted``
  in this transaction; every import is marked ``deleted`` (its files are removed after
  commit); returns the counts. ``lattice_purge_source(actor, source)`` then deletes the
  row (cascade: documents, chunks, grants, synced groups, runs, failures, mappings, jobs);
  only a ``deleting`` source can be purged, and re-running it resumes an interrupted purge.
* ``lattice_begin_import(actor, source, format)`` / ``lattice_import_received(actor,
  import, size, sha256)`` / ``lattice_finish_import(actor, import, entries, ignored,
  expanded)`` / ``lattice_reject_import(actor, import, code, size, sha256)`` /
  ``lattice_mark_import_deleted(actor, import)`` -- the C8.2 lifecycle ``receiving ->
  validating -> ready | rejected``, ``ready -> superseded``, ``-> deleted``. One upload per
  source at a time (a receiving row older than an hour is abandoned). Finishing makes the
  import the source's **current import** (``sources.import_id``) under the source's row
  lock, supersedes the previous one and queues a check (an ``import_check`` job, the
  dry-run pipeline) when no job of the source is active. A rejected upload leaves the
  current import untouched.

``source_imports`` holds IDs, sizes, a digest, counts, fixed codes and times -- never a
path, a file name or content. Storage locations are derived from the organization and
import UUIDs by the import store (``lattice.imports``). An import belongs to exactly one
source for life: ``sources.import_id`` and every job's ``import_id`` must name an import of
the same source (check triggers, every writer); after a purge an import keeps no source
(``SET NULL``) and can never be attached again.

Jobs: ``kind`` gains ``import_check``; ``import_id`` names the import a job reads -- set
when the job is requested (a sync reads the import that was current then, never a later
one), checked by trigger: an organization-managed source's sync must name its current
import, an operator-managed source's none. Sync runs record the import they read.

The worker gains ``SELECT`` on ``source_imports`` and two fenced functions (``EXECUTE`` for
``lattice_worker`` only): ``lattice_record_import_check(import, result, error)`` -- a
check's counts and codes, validated key by key (no free text) -- and
``lattice_expire_imports(limit)`` -- marks deleted every rejected import, every superseded
import no queued or running job reads, every upload abandoned for an hour and every
import left without a source, and returns them so the worker removes their files.

Audit coupling (deferred constraint triggers, ``lattice_app`` only): source inserts need
``source.created``; changes ``source.updated``; ``active -> paused`` ``source.paused``;
``paused -> active`` ``source.resumed``; ``-> deleting`` ``source.disconnected``; a new
current import ``source.import_accepted`` naming it; an import rejected
``source.import_rejected`` naming it; a requested sync ``source.sync_requested``. A check
(``import_check``) is not audited (C11). The audit insert policy admits the ``source.*``
family from ``member:`` actors. 0018's mapping coupling no longer asks for a per-mapping
event when a disconnected source's purge removes its mappings by cascade (the
``source.disconnected`` event records how many).

``sources.version`` is raised by every change of an administrator-visible setting (never
by the worker's sync timestamps) so edits are optimistic (409). ``sources.managed_by``:
``operator`` (the sources file; every existing source) or ``organization`` (the product);
an organization-managed source's configuration never holds a ``path`` (CHECK).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SSO_ACTIONS = (
    "'sso.connection_created', 'sso.connection_updated', 'sso.connection_disabled', "
    "'sso.connection_enabled', 'sso.connection_tested', 'sso.connection_identity_changed'"
)
_SCIM_ACTIONS = "'scim.token_created', 'scim.token_rotated', 'scim.token_revoked'"
_PROVIDER_ACTIONS = (
    "'provider.configured', 'provider.enabled', 'provider.disabled', 'provider.approval_changed'"
)
_CREDENTIAL_ACTIONS = "'credential.set', 'credential.rotated', 'credential.revoked'"
_EGRESS_ACTIONS = "'egress.levels_changed', 'egress.step_up_completed', 'egress.escalation_refused'"
_SOURCE_ACTIONS = (
    "'source.created', 'source.updated', 'source.paused', 'source.resumed', "
    "'source.disconnected', 'source.import_accepted', 'source.import_rejected', "
    "'source.sync_requested'"
)
_AUDIT_0021 = (
    "(action LIKE 'access.%' AND (actor LIKE 'member:%' OR actor LIKE 'scim:%')) "
    "OR (action IN ('organization.updated', 'account.created', 'account.updated', "
    "'account.archived', 'account.reactivated', 'account.mapping_changed', "
    f"{_SSO_ACTIONS}, {_SCIM_ACTIONS}, {_PROVIDER_ACTIONS}, {_CREDENTIAL_ACTIONS}, "
    f"{_EGRESS_ACTIONS}) AND actor LIKE 'member:%') "
    "OR (action = 'scim.request_refused' AND actor LIKE 'scim:%')"
)
_AUDIT_0022 = (
    "(action LIKE 'access.%' AND (actor LIKE 'member:%' OR actor LIKE 'scim:%')) "
    "OR (action IN ('organization.updated', 'account.created', 'account.updated', "
    "'account.archived', 'account.reactivated', 'account.mapping_changed', "
    f"{_SSO_ACTIONS}, {_SCIM_ACTIONS}, {_PROVIDER_ACTIONS}, {_CREDENTIAL_ACTIONS}, "
    f"{_EGRESS_ACTIONS}, {_SOURCE_ACTIONS}) AND actor LIKE 'member:%') "
    "OR (action = 'scim.request_refused' AND actor LIKE 'scim:%')"
)

_APP_FUNCTIONS = (
    "lattice_create_source(uuid, text, text, text, text, text[], text, boolean)",
    "lattice_update_source(uuid, uuid, bigint, jsonb)",
    "lattice_set_source_paused(uuid, uuid, bigint, boolean)",
    "lattice_disconnect_source(uuid, uuid, bigint)",
    "lattice_purge_source(uuid, uuid)",
    "lattice_begin_import(uuid, uuid, text)",
    "lattice_import_received(uuid, uuid, bigint, bytea)",
    "lattice_finish_import(uuid, uuid, integer, integer, bigint)",
    "lattice_reject_import(uuid, uuid, text, bigint, bytea)",
    "lattice_mark_import_deleted(uuid, uuid)",
)
_WORKER_FUNCTIONS = (
    "lattice_record_import_check(uuid, jsonb, text)",
    "lattice_expire_imports(integer)",
)
# Owner-only helpers (no EXECUTE for any runtime role).
_HELPERS = (
    "lattice_source_config(text[], jsonb)",
    "lattice_source_departments_valid(uuid, text[])",
    "lattice_import_check_valid(jsonb)",
)
_COUPLINGS = (
    ("trg_sources_setup_audit", "sources", "lattice_require_source_audit"),
    ("trg_source_imports_setup_audit", "source_imports", "lattice_require_import_audit"),
    ("trg_jobs_setup_audit", "jobs", "lattice_require_sync_request_audit"),
)

_REQUIRE_ORG = """
  IF v_org IS NULL THEN
    RAISE EXCEPTION 'no organization is bound' USING ERRCODE = 'insufficient_privilege';
  END IF;
"""
# Refuse unless p_actor is an active administrator of an active v_org (as 0017/0021).
_REQUIRE_ACTOR = """
  IF NOT EXISTS (
    SELECT 1 FROM public.memberships m JOIN public.users u ON u.id = m.user_id
      JOIN public.organizations o ON o.id = m.organization_id
    WHERE m.organization_id = v_org AND m.id = p_actor AND m.is_admin
      AND m.status = 'active' AND u.status = 'active' AND o.status = 'active'
  ) THEN
    RAISE EXCEPTION 'not_administrator' USING ERRCODE = 'LA001';
  END IF;
"""
_CODE = "^[a-z_]+(:[a-z_]+)?$"
_NAME_OK = (
    "{0} IS NOT NULL AND {0} = btrim({0}) AND length({0}) BETWEEN 1 AND 200 "
    "AND {0} !~ '[[:cntrl:]]'"
)
# Lock the source row and check the version the administrator reviewed.
_LOCK_SOURCE = """
  SELECT s.managed_by, s.status, s.version, s.connector_type
    INTO v_managed, v_status, v_version, v_connector
  FROM public.sources s WHERE s.organization_id = v_org AND s.id = p_source FOR UPDATE;
  IF NOT FOUND OR v_status = 'deleting' THEN
    RAISE EXCEPTION 'source_not_found' USING ERRCODE = 'LA001';
  END IF;
  IF p_version IS NULL OR v_version <> p_version THEN
    RAISE EXCEPTION 'source_changed' USING ERRCODE = 'LA001';
  END IF;
"""
_SOURCE_VARS = """
  v_managed text;
  v_status text;
  v_version bigint;
  v_connector text;
"""

_MAPPING_AUDIT = """
        CREATE OR REPLACE FUNCTION lattice_require_mapping_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_org uuid;
          v_source uuid;
          v_kind text;
          v_ref text;
        BEGIN
          IF current_user <> 'lattice_app' THEN
            RETURN NULL;
          END IF;
          IF TG_OP = 'DELETE' THEN
            v_org := OLD.organization_id; v_source := OLD.source_id;
            v_kind := OLD.ref_kind; v_ref := OLD.external_ref;
            {purge}
          ELSE
            v_org := NEW.organization_id; v_source := NEW.source_id;
            v_kind := NEW.ref_kind; v_ref := NEW.external_ref;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id() AND e.organization_id = v_org
              AND e.action = 'account.mapping_changed'
              AND e.target ->> 'source_id' = v_source::text
              AND e.target ->> 'ref_kind' = v_kind
              AND e.target ->> 'external_ref' = v_ref
          ) THEN
            RAISE EXCEPTION 'account mapping change without an audit event'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
"""
# A source's purge (it was disconnected, audited as source.disconnected with the number of
# mappings) removes its mappings by cascade: no per-mapping event. Nothing else deletes a
# mapping from the API role (an unmap keeps the row, 0019).
_PURGE_EXEMPTION = """
            IF NOT EXISTS (
              SELECT 1 FROM public.sources s
              WHERE s.organization_id = v_org AND s.id = v_source AND s.status <> 'deleting'
            ) THEN
              RETURN NULL;
            END IF;"""


def _function(signature: str, returns: str, declare: str, body: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION {signature} RETURNS {returns}
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_org uuid := public.lattice_current_org_id();
          {declare}
        BEGIN
          {_REQUIRE_ORG}
          {body}
        END
        $$
        """
    )


def upgrade() -> None:
    # --- sources: ownership, version, current import ----------------------------------
    op.execute(
        "ALTER TABLE sources "
        "ADD COLUMN managed_by text NOT NULL DEFAULT 'operator', "
        "ADD COLUMN version bigint NOT NULL DEFAULT 1, "
        "ADD COLUMN import_id uuid, "
        "ADD CONSTRAINT ck_sources_managed_by CHECK (managed_by IN ('operator', 'organization')), "
        "ADD CONSTRAINT ck_sources_version CHECK (version >= 1), "
        # An organization-managed source never syncs a host path; an operator's never an import.
        "ADD CONSTRAINT ck_sources_import_or_path CHECK ("
        "(managed_by = 'organization' AND NOT config ? 'path') "
        "OR (managed_by = 'operator' AND import_id IS NULL))"
    )
    # Raised by any change an administrator may have reviewed; never by sync timestamps.
    op.execute(
        """
        CREATE FUNCTION lattice_bump_source_version() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
          IF (NEW.name, NEW.config, NEW.default_sensitivity, NEW.account_scoped,
              NEW.authority, NEW.status, NEW.import_id, NEW.managed_by)
             IS DISTINCT FROM
             (OLD.name, OLD.config, OLD.default_sensitivity, OLD.account_scoped,
              OLD.authority, OLD.status, OLD.import_id, OLD.managed_by) THEN
            NEW.version := OLD.version + 1;
          ELSE
            NEW.version := OLD.version;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_bump_source_version() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_sources_version BEFORE UPDATE ON sources "
        "FOR EACH ROW EXECUTE FUNCTION lattice_bump_source_version()"
    )

    # --- source_imports -------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE source_imports (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          source_id uuid,
          format text NOT NULL,
          status text NOT NULL DEFAULT 'receiving',
          size_bytes bigint,
          sha256 bytea,
          entries integer,
          ignored_entries integer,
          expanded_bytes bigint,
          rejected_code text,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          received_at timestamptz,
          ready_at timestamptz,
          superseded_at timestamptz,
          deleted_at timestamptz,
          checked_at timestamptz,
          check_result jsonb,
          check_error_code text,
          CONSTRAINT pk_source_imports PRIMARY KEY (id),
          CONSTRAINT uq_source_imports_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT fk_source_imports_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT fk_source_imports_organization_id_source_id_sources
            FOREIGN KEY (organization_id, source_id)
            REFERENCES sources (organization_id, id) ON DELETE SET NULL (source_id),
          CONSTRAINT ck_source_imports_format CHECK (format IN ('zip', 'jsonl')),
          CONSTRAINT ck_source_imports_status CHECK (status IN (
            'receiving', 'validating', 'ready', 'rejected', 'superseded', 'deleted')),
          CONSTRAINT ck_source_imports_counts CHECK (
            coalesce(size_bytes, 0) >= 0 AND coalesce(entries, 0) >= 0
            AND coalesce(ignored_entries, 0) >= 0 AND coalesce(expanded_bytes, 0) >= 0),
          CONSTRAINT ck_source_imports_sha256 CHECK (sha256 IS NULL OR octet_length(sha256) = 32),
          CONSTRAINT ck_source_imports_rejected_code CHECK (
            rejected_code IS NULL OR rejected_code ~ '^[a-z_]{{1,40}}$'),
          CONSTRAINT ck_source_imports_rejected CHECK (
            (status = 'rejected') <= (rejected_code IS NOT NULL)
            AND (rejected_code IS NULL OR status IN ('rejected', 'deleted'))),
          CONSTRAINT ck_source_imports_ready CHECK (
            status NOT IN ('ready', 'superseded') OR (size_bytes IS NOT NULL
              AND sha256 IS NOT NULL AND entries IS NOT NULL AND expanded_bytes IS NOT NULL
              AND ready_at IS NOT NULL)),
          CONSTRAINT ck_source_imports_received CHECK (
            status <> 'validating' OR (size_bytes IS NOT NULL AND sha256 IS NOT NULL)),
          CONSTRAINT ck_source_imports_deleted
            CHECK ((status = 'deleted') = (deleted_at IS NOT NULL)),
          CONSTRAINT ck_source_imports_orphan CHECK (source_id IS NOT NULL OR status = 'deleted'),
          CONSTRAINT ck_source_imports_created_by CHECK (
            created_by ~ '^member:[0-9a-f-]{{36}}$'),
          CONSTRAINT ck_source_imports_check_result CHECK (
            check_result IS NULL OR jsonb_typeof(check_result) = 'object'),
          CONSTRAINT ck_source_imports_check_error_code CHECK (
            check_error_code IS NULL OR check_error_code ~ '{_CODE}')
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_source_imports_one_upload_per_source "
        "ON source_imports (organization_id, source_id) "
        "WHERE status IN ('receiving', 'validating')"
    )
    op.execute(
        "CREATE INDEX ix_source_imports_organization_id_source_id_created_at "
        "ON source_imports (organization_id, source_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_source_imports_due ON source_imports (status) "
        "WHERE status IN ('receiving', 'validating', 'rejected', 'superseded')"
    )
    op.execute("ALTER TABLE source_imports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE source_imports FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON source_imports "
        "USING (organization_id = lattice_current_org_id()) "
        "WITH CHECK (organization_id = lattice_current_org_id())"
    )
    op.execute("GRANT SELECT ON source_imports TO lattice_app, lattice_worker")
    op.execute(
        "ALTER TABLE sources ADD CONSTRAINT fk_sources_organization_id_import_id_source_imports "
        "FOREIGN KEY (organization_id, import_id) REFERENCES source_imports (organization_id, id)"
    )
    # Every writer: the current import is a ready import of this very source.
    op.execute(
        """
        CREATE FUNCTION lattice_check_source_import() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
          IF NEW.import_id IS NOT NULL
             AND (TG_OP = 'INSERT' OR NEW.import_id IS DISTINCT FROM OLD.import_id)
             AND NOT EXISTS (
               SELECT 1 FROM public.source_imports i
               WHERE i.organization_id = NEW.organization_id AND i.id = NEW.import_id
                 AND i.source_id = NEW.id AND i.status = 'ready'
             ) THEN
            RAISE EXCEPTION 'import_not_attachable' USING ERRCODE = 'LA001';
          END IF;
          IF TG_OP = 'UPDATE' AND (NEW.connector_type, NEW.key, NEW.organization_id)
             IS DISTINCT FROM (OLD.connector_type, OLD.key, OLD.organization_id) THEN
            RAISE EXCEPTION 'source_identity_immutable' USING ERRCODE = 'LA001';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_check_source_import() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_sources_import_check BEFORE INSERT OR UPDATE ON sources "
        "FOR EACH ROW EXECUTE FUNCTION lattice_check_source_import()"
    )

    # --- jobs and runs name the import they read ---------------------------------------------
    op.execute("ALTER TABLE jobs DROP CONSTRAINT ck_jobs_kind")
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind "
        "CHECK (kind IN ('source_sync', 'import_check'))"
    )
    op.execute(
        "ALTER TABLE jobs ADD COLUMN import_id uuid, "
        "ADD CONSTRAINT fk_jobs_organization_id_import_id_source_imports "
        "FOREIGN KEY (organization_id, import_id) REFERENCES source_imports (organization_id, id) "
        "ON DELETE SET NULL (import_id)"
    )
    op.execute("CREATE INDEX ix_jobs_import_id ON jobs (import_id) WHERE import_id IS NOT NULL")
    op.execute(
        "GRANT INSERT (organization_id, kind, source_id, mode, requested_by, import_id) "
        "ON jobs TO lattice_app"
    )
    # Every writer: a job reads an import of its own source -- the current one when it is
    # requested -- and an operator-managed source's job reads none.
    op.execute(
        """
        CREATE FUNCTION lattice_check_job_import() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_managed text;
          v_current uuid;
        BEGIN
          -- A share lock held to commit: a new import cannot supersede this job's import
          -- before the job is visible to the import retention (which spares imports that
          -- queued or running jobs read).
          SELECT s.managed_by, s.import_id INTO v_managed, v_current FROM public.sources s
          WHERE s.organization_id = NEW.organization_id AND s.id = NEW.source_id FOR SHARE;
          IF NOT FOUND THEN
            RETURN NEW;  -- the foreign key refuses it
          END IF;
          IF v_managed = 'operator' THEN
            IF NEW.import_id IS NOT NULL OR NEW.kind <> 'source_sync' THEN
              RAISE EXCEPTION 'job_import_mismatch' USING ERRCODE = 'LA001';
            END IF;
          ELSIF NEW.import_id IS NULL OR NEW.import_id IS DISTINCT FROM v_current THEN
            RAISE EXCEPTION 'job_import_mismatch' USING ERRCODE = 'LA001';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_check_job_import() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_jobs_import_check BEFORE INSERT ON jobs "
        "FOR EACH ROW EXECUTE FUNCTION lattice_check_job_import()"
    )
    op.execute(
        "ALTER TABLE source_sync_runs ADD COLUMN import_id uuid, "
        "ADD CONSTRAINT fk_source_sync_runs_organization_id_import_id_source_imports "
        "FOREIGN KEY (organization_id, import_id) REFERENCES source_imports (organization_id, id) "
        "ON DELETE SET NULL (import_id)"
    )

    # --- helpers (owner only) -------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION lattice_source_departments_valid(p_org uuid, p_departments text[])
        RETURNS boolean
        LANGUAGE sql STABLE
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          SELECT p_departments IS NOT NULL
            AND cardinality(p_departments) <= 50
            AND array_position(p_departments, NULL) IS NULL
            AND (SELECT count(DISTINCT d) FROM unnest(p_departments) d) = cardinality(p_departments)
            AND NOT EXISTS (
              SELECT 1 FROM unnest(p_departments) d
              WHERE NOT EXISTS (
                SELECT 1 FROM public.departments x
                WHERE x.organization_id = p_org AND x.key = d))
        $$
        """
    )
    # The configuration an organization-managed source records: default departments and
    # channel sensitivities, nothing else (no path, no mapping -- mappings are a table).
    op.execute(
        """
        CREATE FUNCTION lattice_source_config(p_departments text[], p_channels jsonb)
        RETURNS jsonb
        LANGUAGE sql IMMUTABLE
        SET search_path = pg_catalog, pg_temp
        AS $$
          SELECT jsonb_build_object(
            'default_departments', to_jsonb(coalesce(
              (SELECT array_agg(d ORDER BY d) FROM unnest(p_departments) d), '{}'::text[])),
            'channels', coalesce((
              SELECT jsonb_object_agg(e.key, jsonb_build_object('sensitivity', e.value #>> '{}'))
              FROM jsonb_each(coalesce(p_channels, '{}'::jsonb)) e), '{}'::jsonb))
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION lattice_import_check_valid(p_result jsonb) RETURNS boolean
        LANGUAGE plpgsql IMMUTABLE
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          k text;
          v jsonb;
        BEGIN
          IF p_result IS NULL OR jsonb_typeof(p_result) <> 'object' THEN
            RETURN false;
          END IF;
          FOR k, v IN SELECT * FROM jsonb_each(p_result) LOOP
            IF k IN ('records_seen', 'would_create', 'would_update', 'would_update_access',
                     'unchanged', 'invalid_records', 'would_quarantine', 'would_delete',
                     'unresolved_total') THEN
              IF jsonb_typeof(v) <> 'number' OR (v #>> '{{}}')::numeric < 0
                 OR (v #>> '{{}}')::numeric <> trunc((v #>> '{{}}')::numeric) THEN
                RETURN false;
              END IF;
            ELSIF k = 'deletion_guard' THEN
              IF NOT (jsonb_typeof(v) = 'null'
                      OR (jsonb_typeof(v) = 'string' AND (v #>> '{{}}') ~ '{_CODE}')) THEN
                RETURN false;
              END IF;
            ELSIF k = 'failure_codes' THEN
              IF jsonb_typeof(v) <> 'object' OR (SELECT count(*) FROM jsonb_object_keys(v)) > 100
                 OR EXISTS (SELECT 1 FROM jsonb_each(v) f
                            WHERE f.key !~ '{_CODE}' OR jsonb_typeof(f.value) <> 'number'
                               OR (f.value #>> '{{}}')::numeric < 0) THEN
                RETURN false;
              END IF;
            ELSIF k = 'unresolved_references' THEN
              IF jsonb_typeof(v) <> 'array' OR jsonb_array_length(v) > 200
                 OR EXISTS (
                   SELECT 1 FROM jsonb_array_elements(v) r
                   WHERE jsonb_typeof(r) <> 'object'
                      OR (SELECT count(*) FROM jsonb_object_keys(r)) <> 2
                      OR jsonb_typeof(r -> 'ref') <> 'string'
                      OR jsonb_typeof(r -> 'records') <> 'number'
                      OR length(r ->> 'ref') NOT BETWEEN 1 AND 256
                      OR (r ->> 'ref') ~ '[[:cntrl:]]'
                      OR (r ->> 'ref') <> btrim(r ->> 'ref')) THEN
                RETURN false;
              END IF;
            ELSE
              RETURN false;
            END IF;
          END LOOP;
          RETURN true;
        END
        $$
        """  # noqa: S608 - fixed SQL fragments, no input
    )
    for helper in _HELPERS:
        op.execute(f"REVOKE ALL ON FUNCTION {helper} FROM PUBLIC")

    # --- source lifecycle (lattice_app) ---------------------------------------------------
    _function(
        "lattice_create_source(p_actor uuid, p_key text, p_type text, p_name text, "
        "p_sensitivity text, p_departments text[], p_authority text, p_account_scoped boolean)",
        "uuid",
        "v_id uuid;",
        f"""
          {_REQUIRE_ACTOR}
          IF p_key IS NULL OR p_key !~ '^[a-z0-9][a-z0-9-]{{0,62}}$' THEN
            RAISE EXCEPTION 'key_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_type IS NULL
             OR p_type NOT IN ('document_repository', 'ticketing', 'chat_export') THEN
            RAISE EXCEPTION 'connector_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF NOT ({_NAME_OK.format("p_name")}) THEN
            RAISE EXCEPTION 'name_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_sensitivity IS NULL OR p_sensitivity NOT IN ('internal', 'confidential') THEN
            RAISE EXCEPTION 'sensitivity_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_authority IS NULL
             OR p_authority NOT IN ('system_of_record', 'working', 'conversational') THEN
            RAISE EXCEPTION 'authority_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_account_scoped IS NULL THEN
            RAISE EXCEPTION 'change_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF NOT public.lattice_source_departments_valid(v_org, p_departments) THEN
            RAISE EXCEPTION 'unknown_department' USING ERRCODE = 'LA001';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.sources s WHERE s.organization_id = v_org AND s.key = p_key
          ) THEN
            RAISE EXCEPTION 'key_taken' USING ERRCODE = 'LA001';
          END IF;
          INSERT INTO public.sources (
            organization_id, key, connector_type, name, config, default_sensitivity,
            account_scoped, authority, managed_by
          ) VALUES (
            v_org, p_key, p_type, p_name, public.lattice_source_config(p_departments, NULL),
            p_sensitivity, p_account_scoped, p_authority, 'organization'
          ) RETURNING id INTO v_id;
          RETURN v_id;
        """,  # noqa: S608 - fixed SQL fragments, no input
    )
    _function(
        "lattice_update_source(p_actor uuid, p_source uuid, p_version bigint, p_change jsonb)",
        "bigint",
        f"""{_SOURCE_VARS}
          v_row public.sources%ROWTYPE;
          v_name text;
          v_sensitivity text;
          v_authority text;
          v_scoped boolean;
          v_departments text[];
          v_channels jsonb;
          v_config jsonb;
          v_new bigint;
        """,
        f"""
          {_REQUIRE_ACTOR}
          IF p_change IS NULL OR jsonb_typeof(p_change) <> 'object' OR EXISTS (
            SELECT 1 FROM jsonb_object_keys(p_change) k
            WHERE k NOT IN ('name', 'default_sensitivity', 'default_departments', 'authority',
                            'account_scoped', 'channel_sensitivities')
          ) THEN
            RAISE EXCEPTION 'change_invalid' USING ERRCODE = 'LA001';
          END IF;
          {_LOCK_SOURCE}
          IF v_managed <> 'organization' THEN
            RAISE EXCEPTION 'operator_managed' USING ERRCODE = 'LA001';
          END IF;
          SELECT * INTO v_row FROM public.sources WHERE organization_id = v_org AND id = p_source;
          v_name := coalesce(p_change ->> 'name', v_row.name);
          IF NOT ({_NAME_OK.format("v_name")}) THEN
            RAISE EXCEPTION 'name_invalid' USING ERRCODE = 'LA001';
          END IF;
          v_sensitivity := coalesce(p_change ->> 'default_sensitivity', v_row.default_sensitivity);
          IF v_sensitivity NOT IN ('internal', 'confidential') THEN
            RAISE EXCEPTION 'sensitivity_invalid' USING ERRCODE = 'LA001';
          END IF;
          v_authority := coalesce(p_change ->> 'authority', v_row.authority);
          IF v_authority NOT IN ('system_of_record', 'working', 'conversational') THEN
            RAISE EXCEPTION 'authority_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_change ? 'account_scoped' THEN
            IF jsonb_typeof(p_change -> 'account_scoped') <> 'boolean' THEN
              RAISE EXCEPTION 'change_invalid' USING ERRCODE = 'LA001';
            END IF;
            v_scoped := (p_change ->> 'account_scoped')::boolean;
          ELSE
            v_scoped := v_row.account_scoped;
          END IF;
          IF p_change ? 'default_departments' THEN
            IF jsonb_typeof(p_change -> 'default_departments') <> 'array' OR EXISTS (
              SELECT 1 FROM jsonb_array_elements(p_change -> 'default_departments') d
              WHERE jsonb_typeof(d) <> 'string') THEN
              RAISE EXCEPTION 'unknown_department' USING ERRCODE = 'LA001';
            END IF;
            v_departments := ARRAY(
              SELECT jsonb_array_elements_text(p_change -> 'default_departments'));
          ELSE
            v_departments := ARRAY(
              SELECT jsonb_array_elements_text(
                coalesce(v_row.config -> 'default_departments', '[]'::jsonb)));
          END IF;
          IF NOT public.lattice_source_departments_valid(v_org, v_departments) THEN
            RAISE EXCEPTION 'unknown_department' USING ERRCODE = 'LA001';
          END IF;
          IF p_change ? 'channel_sensitivities' THEN
            v_channels := p_change -> 'channel_sensitivities';
            IF v_connector <> 'chat_export' OR jsonb_typeof(v_channels) <> 'object'
               OR (SELECT count(*) FROM jsonb_object_keys(v_channels)) > 500
               OR EXISTS (
                 SELECT 1 FROM jsonb_each(v_channels) e
                 WHERE e.key !~ '^[A-Z0-9]{{2,32}}$' OR jsonb_typeof(e.value) <> 'string'
                    OR (e.value #>> '{{}}') NOT IN ('internal', 'confidential')) THEN
              RAISE EXCEPTION 'channels_invalid' USING ERRCODE = 'LA001';
            END IF;
          ELSE
            SELECT jsonb_object_agg(e.key, e.value -> 'sensitivity') INTO v_channels
            FROM jsonb_each(coalesce(v_row.config -> 'channels', '{{}}'::jsonb)) e
            WHERE e.value ? 'sensitivity';
          END IF;
          v_config := public.lattice_source_config(v_departments, v_channels);
          IF (v_name, v_sensitivity, v_authority, v_scoped, v_config)
             IS NOT DISTINCT FROM
             (v_row.name, v_row.default_sensitivity, v_row.authority, v_row.account_scoped,
              v_row.config) THEN
            RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
          END IF;
          UPDATE public.sources SET name = v_name, default_sensitivity = v_sensitivity,
            authority = v_authority, account_scoped = v_scoped, config = v_config
          WHERE organization_id = v_org AND id = p_source
          RETURNING version INTO v_new;
          RETURN v_new;
        """,  # noqa: S608 - fixed SQL fragments, no input
    )
    _function(
        "lattice_set_source_paused(p_actor uuid, p_source uuid, p_version bigint, "
        "p_paused boolean)",
        "bigint",
        f"""{_SOURCE_VARS}
          v_new bigint;
        """,
        f"""
          {_REQUIRE_ACTOR}
          IF p_paused IS NULL THEN
            RAISE EXCEPTION 'change_invalid' USING ERRCODE = 'LA001';
          END IF;
          {_LOCK_SOURCE}
          IF (v_status = 'paused') = p_paused THEN
            RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
          END IF;
          UPDATE public.sources SET status = CASE WHEN p_paused THEN 'paused' ELSE 'active' END
          WHERE organization_id = v_org AND id = p_source
          RETURNING version INTO v_new;
          RETURN v_new;
        """,  # noqa: S608 - fixed SQL fragments, no input
    )
    _function(
        "lattice_disconnect_source(p_actor uuid, p_source uuid, p_version bigint)",
        "jsonb",
        f"""{_SOURCE_VARS}
          v_cancelled integer;
          v_documents integer;
          v_chunks integer;
          v_mappings integer;
          v_imports integer;
        """,
        f"""
          {_REQUIRE_ACTOR}
          {_LOCK_SOURCE}
          -- Queued jobs end first (row locks: a worker claiming one skips it, or this
          -- statement waits and then sees it running); then any running job refuses.
          UPDATE public.jobs SET status = 'failed', error_code = 'source_disconnected',
            finished_at = now(), lease_expires_at = NULL
          WHERE organization_id = v_org AND source_id = p_source AND status = 'queued';
          GET DIAGNOSTICS v_cancelled = ROW_COUNT;
          IF EXISTS (
            SELECT 1 FROM public.jobs j
            WHERE j.organization_id = v_org AND j.source_id = p_source AND j.status = 'running'
          ) THEN
            RAISE EXCEPTION 'sync_running' USING ERRCODE = 'LA001';
          END IF;
          SELECT count(*) INTO v_documents FROM public.documents
          WHERE organization_id = v_org AND source_id = p_source;
          SELECT count(*) INTO v_chunks FROM public.chunks c JOIN public.documents d
            ON d.id = c.document_id
          WHERE d.organization_id = v_org AND d.source_id = p_source;
          SELECT count(*) INTO v_mappings FROM public.account_external_refs
          WHERE organization_id = v_org AND source_id = p_source;
          UPDATE public.sources SET status = 'deleting', import_id = NULL
          WHERE organization_id = v_org AND id = p_source;
          UPDATE public.documents SET status = 'deleted'
          WHERE organization_id = v_org AND source_id = p_source AND status <> 'deleted';
          UPDATE public.source_imports SET status = 'deleted', deleted_at = now()
          WHERE organization_id = v_org AND source_id = p_source AND status <> 'deleted';
          GET DIAGNOSTICS v_imports = ROW_COUNT;
          RETURN jsonb_build_object(
            'documents', v_documents, 'chunks', v_chunks, 'account_mappings', v_mappings,
            'imports', v_imports, 'cancelled_jobs', v_cancelled);
        """,  # noqa: S608 - fixed SQL fragments, no input
    )
    _function(
        "lattice_purge_source(p_actor uuid, p_source uuid)",
        "boolean",
        "v_status text;",
        f"""
          {_REQUIRE_ACTOR}
          SELECT s.status INTO v_status FROM public.sources s
          WHERE s.organization_id = v_org AND s.id = p_source FOR UPDATE;
          IF NOT FOUND THEN
            RETURN false;
          END IF;
          IF v_status <> 'deleting' THEN
            RAISE EXCEPTION 'source_not_disconnected' USING ERRCODE = 'LA001';
          END IF;
          -- Losing a synced group's membership is an access change for those members.
          UPDATE public.memberships SET authz_updated_at = now()
          WHERE organization_id = v_org AND id IN (
            SELECT m.membership_id FROM public.access_group_members m
            JOIN public.access_groups g ON g.id = m.group_id
            WHERE g.organization_id = v_org AND g.source_id = p_source);
          DELETE FROM public.sources WHERE organization_id = v_org AND id = p_source;
          RETURN true;
        """,  # noqa: S608 - fixed SQL fragments, no input
    )

    # --- imports (lattice_app) ---------------------------------------------------------------
    _function(
        "lattice_begin_import(p_actor uuid, p_source uuid, p_format text)",
        "uuid",
        """
          v_managed text;
          v_status text;
          v_connector text;
          v_id uuid;
        """,
        f"""
          {_REQUIRE_ACTOR}
          SELECT s.managed_by, s.status, s.connector_type INTO v_managed, v_status, v_connector
          FROM public.sources s WHERE s.organization_id = v_org AND s.id = p_source FOR UPDATE;
          IF NOT FOUND OR v_status = 'deleting' THEN
            RAISE EXCEPTION 'source_not_found' USING ERRCODE = 'LA001';
          END IF;
          IF v_managed <> 'organization' THEN
            RAISE EXCEPTION 'operator_managed' USING ERRCODE = 'LA001';
          END IF;
          IF p_format IS NULL OR p_format NOT IN ('zip', 'jsonl')
             OR (p_format = 'jsonl' AND v_connector <> 'ticketing') THEN
            RAISE EXCEPTION 'format_invalid' USING ERRCODE = 'LA001';
          END IF;
          -- An upload whose request died more than an hour ago is abandoned (its files are
          -- removed by the worker's import retention).
          UPDATE public.source_imports SET status = 'rejected', rejected_code = 'upload_abandoned'
          WHERE organization_id = v_org AND source_id = p_source
            AND status IN ('receiving', 'validating') AND created_at < now() - interval '1 hour';
          IF EXISTS (
            SELECT 1 FROM public.source_imports i WHERE i.organization_id = v_org
              AND i.source_id = p_source AND i.status IN ('receiving', 'validating')
          ) THEN
            RAISE EXCEPTION 'upload_in_progress' USING ERRCODE = 'LA001';
          END IF;
          INSERT INTO public.source_imports (organization_id, source_id, format, created_by)
          VALUES (v_org, p_source, p_format, 'member:' || p_actor)
          RETURNING id INTO v_id;
          RETURN v_id;
        """,  # noqa: S608 - fixed SQL fragments, no input
    )
    _function(
        "lattice_import_received(p_actor uuid, p_import uuid, p_size bigint, p_sha256 bytea)",
        "void",
        "",
        f"""
          {_REQUIRE_ACTOR}
          UPDATE public.source_imports SET status = 'validating', size_bytes = p_size,
            sha256 = p_sha256, received_at = now()
          WHERE organization_id = v_org AND id = p_import AND status = 'receiving'
            AND source_id IS NOT NULL;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'import_state' USING ERRCODE = 'LA001';
          END IF;
        """,  # noqa: S608 - fixed SQL fragments, no input
    )
    _function(
        "lattice_finish_import(p_actor uuid, p_import uuid, p_entries integer, "
        "p_ignored integer, p_expanded bigint)",
        "jsonb",
        """
          v_source uuid;
          v_previous uuid;
          v_status text;
          v_managed text;
          v_queued integer := 0;
        """,
        f"""
          {_REQUIRE_ACTOR}
          SELECT i.source_id INTO v_source FROM public.source_imports i
          WHERE i.organization_id = v_org AND i.id = p_import AND i.status = 'validating';
          IF NOT FOUND OR v_source IS NULL THEN
            RAISE EXCEPTION 'import_state' USING ERRCODE = 'LA001';
          END IF;
          -- The source's row lock orders this with sync requests (FOR SHARE) and disconnects.
          SELECT s.import_id, s.status, s.managed_by INTO v_previous, v_status, v_managed
          FROM public.sources s WHERE s.organization_id = v_org AND s.id = v_source FOR UPDATE;
          IF NOT FOUND OR v_status = 'deleting' OR v_managed <> 'organization' THEN
            RAISE EXCEPTION 'source_not_found' USING ERRCODE = 'LA001';
          END IF;
          UPDATE public.source_imports SET status = 'ready', entries = p_entries,
            ignored_entries = p_ignored, expanded_bytes = p_expanded, ready_at = now()
          WHERE organization_id = v_org AND id = p_import AND status = 'validating';
          IF NOT FOUND THEN
            RAISE EXCEPTION 'import_state' USING ERRCODE = 'LA001';
          END IF;
          IF v_previous IS NOT NULL THEN
            UPDATE public.source_imports SET status = 'superseded', superseded_at = now()
            WHERE organization_id = v_org AND id = v_previous AND status = 'ready';
          END IF;
          UPDATE public.sources SET import_id = p_import
          WHERE organization_id = v_org AND id = v_source;
          -- The deterministic check (the dry-run pipeline) of the new import, unless a job
          -- of this source is already queued or running (the administrator checks later).
          INSERT INTO public.jobs (organization_id, kind, source_id, mode, requested_by, import_id)
          VALUES (v_org, 'import_check', v_source, 'full', p_actor, p_import)
          ON CONFLICT (organization_id, source_id) WHERE status IN ('queued', 'running')
          DO NOTHING;
          GET DIAGNOSTICS v_queued = ROW_COUNT;
          RETURN jsonb_build_object('source_id', v_source, 'superseded', v_previous,
                                    'check_queued', v_queued > 0);
        """,  # noqa: S608 - fixed SQL fragments, no input
    )
    _function(
        "lattice_reject_import(p_actor uuid, p_import uuid, p_code text, p_size bigint, "
        "p_sha256 bytea)",
        "void",
        "",
        f"""
          {_REQUIRE_ACTOR}
          IF p_code IS NULL OR p_code !~ '^[a-z_]{{1,40}}$' THEN
            RAISE EXCEPTION 'change_invalid' USING ERRCODE = 'LA001';
          END IF;
          UPDATE public.source_imports SET status = 'rejected', rejected_code = p_code,
            size_bytes = coalesce(p_size, size_bytes), sha256 = coalesce(p_sha256, sha256),
            received_at = coalesce(received_at, CASE WHEN p_size IS NOT NULL THEN now() END)
          WHERE organization_id = v_org AND id = p_import
            AND status IN ('receiving', 'validating');
          IF NOT FOUND THEN
            RAISE EXCEPTION 'import_state' USING ERRCODE = 'LA001';
          END IF;
        """,  # noqa: S608 - fixed SQL fragments, no input
    )
    _function(
        "lattice_mark_import_deleted(p_actor uuid, p_import uuid)",
        "void",
        "",
        f"""
          {_REQUIRE_ACTOR}
          UPDATE public.source_imports SET status = 'deleted', deleted_at = now()
          WHERE organization_id = v_org AND id = p_import AND status = 'rejected';
          IF NOT FOUND THEN
            RAISE EXCEPTION 'import_state' USING ERRCODE = 'LA001';
          END IF;
        """,  # noqa: S608 - fixed SQL fragments, no input
    )
    for function in _APP_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO lattice_app")

    # --- the worker's writes (lattice_worker) ------------------------------------------
    op.execute(
        f"""
        CREATE FUNCTION lattice_record_import_check(p_import uuid, p_result jsonb, p_error text)
        RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_org uuid := public.lattice_current_org_id();
        BEGIN
          {_REQUIRE_ORG}
          IF (p_result IS NULL) = (p_error IS NULL) THEN
            RAISE EXCEPTION 'check_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_result IS NOT NULL AND NOT public.lattice_import_check_valid(p_result) THEN
            RAISE EXCEPTION 'check_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_error IS NOT NULL AND p_error !~ '{_CODE}' THEN
            RAISE EXCEPTION 'check_invalid' USING ERRCODE = 'LA001';
          END IF;
          UPDATE public.source_imports SET checked_at = now(), check_result = p_result,
            check_error_code = p_error
          WHERE organization_id = v_org AND id = p_import AND status IN ('ready', 'superseded');
          IF NOT FOUND THEN
            RAISE EXCEPTION 'import_state' USING ERRCODE = 'LA001';
          END IF;
        END
        $$
        """  # noqa: S608 - fixed SQL fragments, no input
    )
    # Across organizations, one bound at a time (forced RLS binds the owner too).
    op.execute(
        """
        CREATE FUNCTION lattice_expire_imports(p_limit integer)
        RETURNS TABLE (organization_id uuid, import_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        #variable_conflict use_column
        DECLARE
          v_org uuid;
          v_previous text := current_setting('app.org_id', true);
          v_limit integer := greatest(coalesce(p_limit, 0), 0);
          v_done integer := 0;
          r record;
        BEGIN
          FOR v_org IN SELECT o.id FROM public.organizations o ORDER BY o.id LOOP
            EXIT WHEN v_done >= v_limit;
            PERFORM set_config('app.org_id', v_org::text, true);
            UPDATE public.source_imports i SET status = 'rejected',
              rejected_code = 'upload_abandoned'
            WHERE i.organization_id = v_org AND i.status IN ('receiving', 'validating')
              AND i.created_at < now() - interval '1 hour';
            FOR r IN
              WITH due AS (
                SELECT i.id FROM public.source_imports i
                WHERE i.organization_id = v_org AND (
                  i.status = 'rejected'
                  OR (i.status <> 'deleted' AND i.source_id IS NULL)
                  OR (i.status = 'superseded' AND NOT EXISTS (
                    SELECT 1 FROM public.jobs j
                    WHERE j.organization_id = v_org AND j.import_id = i.id
                      AND j.status IN ('queued', 'running'))))
                ORDER BY i.created_at
                LIMIT v_limit - v_done
                FOR UPDATE SKIP LOCKED
              )
              UPDATE public.source_imports i SET status = 'deleted', deleted_at = now()
              FROM due WHERE i.id = due.id
              RETURNING i.organization_id AS org, i.id AS id
            LOOP
              organization_id := r.org;
              import_id := r.id;
              v_done := v_done + 1;
              RETURN NEXT;
            END LOOP;
          END LOOP;
          PERFORM set_config('app.org_id', coalesce(v_previous, ''), true);
        END
        $$
        """
    )
    for function in _WORKER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO lattice_worker")

    # --- audit coupling ----------------------------------------------------------------
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        f"TO lattice_app WITH CHECK ({_AUDIT_0022})"
    )
    op.execute(
        """
        CREATE FUNCTION lattice_require_source_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_action text;
          v_import boolean := false;
        BEGIN
          IF current_user <> 'lattice_app' THEN
            RETURN NULL;
          END IF;
          IF TG_OP = 'INSERT' THEN
            v_action := 'source.created';
          ELSIF NEW.status = 'deleting' AND OLD.status <> 'deleting' THEN
            v_action := 'source.disconnected';
          ELSIF OLD.status = 'active' AND NEW.status = 'paused' THEN
            v_action := 'source.paused';
          ELSIF OLD.status = 'paused' AND NEW.status = 'active' THEN
            v_action := 'source.resumed';
          ELSIF NEW.import_id IS DISTINCT FROM OLD.import_id THEN
            v_action := 'source.import_accepted';
            v_import := true;
          ELSIF (NEW.name, NEW.config, NEW.default_sensitivity, NEW.account_scoped, NEW.authority)
                IS DISTINCT FROM
                (OLD.name, OLD.config, OLD.default_sensitivity, OLD.account_scoped,
                 OLD.authority) THEN
            v_action := 'source.updated';
          ELSE
            RETURN NULL;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id() AND e.organization_id = NEW.organization_id
              AND e.action = v_action AND e.target ->> 'source_id' = NEW.id::text
              AND (NOT v_import OR e.target ->> 'import_id' = NEW.import_id::text)
          ) THEN
            RAISE EXCEPTION 'source change without an audit event'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION lattice_require_import_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
          -- An upload abandoned by a request that died is not a decision anybody made.
          IF current_user <> 'lattice_app' OR NEW.status <> 'rejected'
             OR OLD.status = 'rejected' OR NEW.rejected_code = 'upload_abandoned' THEN
            RETURN NULL;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id() AND e.organization_id = NEW.organization_id
              AND e.action = 'source.import_rejected' AND e.target ->> 'import_id' = NEW.id::text
          ) THEN
            RAISE EXCEPTION 'import rejection without an audit event'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION lattice_require_sync_request_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
          IF current_user <> 'lattice_app' OR NEW.kind <> 'source_sync' THEN
            RETURN NULL;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id() AND e.organization_id = NEW.organization_id
              AND e.action = 'source.sync_requested'
              AND e.target ->> 'source_id' = NEW.source_id::text
          ) THEN
            RAISE EXCEPTION 'sync request without an audit event'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_sources_setup_audit AFTER INSERT OR UPDATE OF "
        "name, config, default_sensitivity, account_scoped, authority, status, import_id "
        "ON sources DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_source_audit()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_source_imports_setup_audit AFTER UPDATE OF status "
        "ON source_imports DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_import_audit()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_jobs_setup_audit AFTER INSERT ON jobs "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_sync_request_audit()"
    )
    for _, _, function in _COUPLINGS:
        op.execute(f"REVOKE ALL ON FUNCTION {function}() FROM PUBLIC")
    op.execute(_MAPPING_AUDIT.replace("{purge}", _PURGE_EXEMPTION))


def downgrade() -> None:
    op.execute(_MAPPING_AUDIT.replace("{purge}", ""))
    for trigger, table, function in reversed(_COUPLINGS):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
        op.execute(f"DROP FUNCTION {function}()")
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        f"TO lattice_app WITH CHECK ({_AUDIT_0021})"
    )
    for function in (*reversed(_WORKER_FUNCTIONS), *reversed(_APP_FUNCTIONS), *_HELPERS):
        op.execute(f"DROP FUNCTION {function}")
    # 0021 knows no import-checks and no imports: they go; organization-managed sources
    # stay (with no path, a requested sync of one fails source_config:path_unknown).
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            DELETE FROM jobs WHERE organization_id = org AND kind = 'import_check';
            UPDATE sources SET import_id = NULL WHERE organization_id = org;
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )
    op.execute("ALTER TABLE source_sync_runs DROP COLUMN import_id")
    op.execute("DROP TRIGGER trg_jobs_import_check ON jobs")
    op.execute("DROP FUNCTION lattice_check_job_import()")
    op.execute(
        "REVOKE INSERT (organization_id, kind, source_id, mode, requested_by, import_id) "
        "ON jobs FROM lattice_app"
    )
    op.execute(
        "GRANT INSERT (organization_id, kind, source_id, mode, requested_by) ON jobs TO lattice_app"
    )
    op.execute("DROP INDEX ix_jobs_import_id")
    op.execute("ALTER TABLE jobs DROP COLUMN import_id")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT ck_jobs_kind")
    op.execute("ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN ('source_sync'))")
    op.execute("DROP TRIGGER trg_sources_import_check ON sources")
    op.execute("DROP FUNCTION lattice_check_source_import()")
    op.execute(
        "ALTER TABLE sources DROP CONSTRAINT fk_sources_organization_id_import_id_source_imports"
    )
    op.execute("DROP TABLE source_imports")
    op.execute("DROP TRIGGER trg_sources_version ON sources")
    op.execute("DROP FUNCTION lattice_bump_source_version()")
    op.execute(
        "ALTER TABLE sources DROP CONSTRAINT ck_sources_import_or_path, "
        "DROP CONSTRAINT ck_sources_version, DROP CONSTRAINT ck_sources_managed_by, "
        "DROP COLUMN import_id, DROP COLUMN version, DROP COLUMN managed_by"
    )
