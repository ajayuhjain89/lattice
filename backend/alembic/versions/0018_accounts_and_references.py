"""Accounts and external-reference mapping in the product (Phase 12, P12.3; ADR-017;
phase-12-plan C10, C11, C12).

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-12
Reversible: no (the downgrade drops the archived lifecycle; mappings return to config)
Locks: new table; brief ACCESS EXCLUSIVE on accounts (column with no default, checks,
  policies, triggers), sources (the configuration rewrite updates every source row once)
  and audit_events (policy); the accounts checks scan the table once

* ``accounts.archived_at`` -- the setup lifecycle, separate from the business ``status``
  (``active`` / ``churned`` / ``prospect``, display labels with no effect). An archived
  account keeps its documents, assignments, traces and existing mappings; it only
  refuses *new* mappings. Archive and reactivate keep the same row (no new identity).
  Accounts are never deleted by the product. Checks for every writer: the key shape the
  seed and ``sources.yaml`` already use (immutable for the API: no column grant), a
  name of 1-200 characters without control characters, at most 20 distinct aliases of
  1-100 characters, trimmed, without control characters.
* ``lattice_app`` may ``INSERT (organization_id, key, name, aliases, status)`` and
  ``UPDATE (name, aliases, status)`` of its organization's accounts; never ``archived_at``
  directly and never an archived account (restrictive policy: archived records are
  read-only until reactivated). ``lattice_set_account_archived(actor, account,
  archived)`` -- fenced (SECURITY DEFINER, owned by ``lattice_owner``, search path
  pinned, ``EXECUTE`` for ``lattice_app`` only) -- archives or reactivates under the
  account's row lock, after checking the actor is an active administrator.
* ``account_external_refs`` (C10): one source-provided reference -> at most one account
  (unique per organization, source, kind and value); one account -> many references.
  ``ref_kind`` is the connector's own: ``ticketing_organization`` (a ticket's
  organization ID, canonical decimal) for ``ticketing`` sources and ``chat_channel`` (a
  channel ID) for ``chat_export`` sources. Composite foreign keys keep source and account
  in the row's organization; deleting the source deletes its mappings, an account with
  mappings cannot be deleted. A check trigger, for every writer: the kind fits the
  source's connector, the source is not being deleted, the key columns never change, and
  a new or changed mapping never targets an archived account (read under a share lock,
  so it serialises with archiving). ``lattice_app`` and ``lattice_worker`` read it;
  ``lattice_app`` writes only through ``lattice_set_account_ref(actor, source, kind,
  value, expected, account)``: map, remap or unmap, refused unless the current mapping is
  ``expected`` (a concurrent change answers ``mapping_changed``).
* Audit coupling (the 0016 mechanism): deferred constraint triggers refuse to commit an
  API-role write of ``accounts`` without the matching ``account.created`` /
  ``account.updated`` / ``account.archived`` / ``account.reactivated`` event, and of
  ``account_external_refs`` without ``account.mapping_changed`` for the same reference,
  in the same transaction. The audit insert policy admits those five actions from
  ``member:`` actors.
* Data: ``sources.config.account_refs`` (ticketing) and ``channels.*.account`` (chat)
  move into the table (actor ``operator:migration``) and leave the configuration;
  channel sensitivity stays there. A configured key naming no account stops the
  migration (it would have failed every sync of that source).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# pg_advisory_xact_lock(class, key): one reference of one source (lattice.accounts).
REF_LOCK = (
    "pg_advisory_xact_lock(1280460102, "
    "hashtext({0}::text || '/' || {1}::text || '/' || {2} || '/' || {3}))"
)

_AUDIT_INSERT = (
    "(action LIKE 'access.%' AND (actor LIKE 'member:%' OR actor LIKE 'scim:%')) "
    "OR (action IN ('organization.updated', 'account.created', 'account.updated', "
    "'account.archived', 'account.reactivated', 'account.mapping_changed') "
    "AND actor LIKE 'member:%')"
)
_AUDIT_INSERT_0016 = (
    "(action LIKE 'access.%' AND (actor LIKE 'member:%' OR actor LIKE 'scim:%')) "
    "OR (action = 'organization.updated' AND actor LIKE 'member:%')"
)
_FUNCTIONS = (
    "lattice_set_account_archived(uuid, uuid, boolean)",
    "lattice_set_account_ref(uuid, uuid, text, text, uuid, uuid)",
)
_REQUIRE_ORG = """
  IF v_org IS NULL THEN
    RAISE EXCEPTION 'no organization is bound' USING ERRCODE = 'insufficient_privilege';
  END IF;
"""
# Refuse unless p_actor is an active administrator of v_org (as 0017).
_REQUIRE_ACTOR = """
  IF NOT EXISTS (
    SELECT 1 FROM public.memberships m JOIN public.users u ON u.id = m.user_id
    WHERE m.organization_id = v_org AND m.id = p_actor AND m.is_admin
      AND m.status = 'active' AND u.status = 'active'
  ) THEN
    RAISE EXCEPTION 'not_administrator' USING ERRCODE = 'LA001';
  END IF;
"""
_TICKETING_REF = "^(0|[1-9][0-9]{0,18})$"


def upgrade() -> None:
    # --- accounts: lifecycle and checks for every writer -------------------------------
    op.execute(
        """
        CREATE FUNCTION lattice_account_aliases_valid(p_aliases text[]) RETURNS boolean
        LANGUAGE sql IMMUTABLE
        SET search_path = pg_catalog, pg_temp
        AS $$
          SELECT p_aliases IS NOT NULL
            AND cardinality(p_aliases) <= 20
            AND array_position(p_aliases, NULL) IS NULL
            AND NOT EXISTS (
              SELECT 1 FROM unnest(p_aliases) a
              WHERE a <> btrim(a) OR length(a) NOT BETWEEN 1 AND 100 OR a ~ '[[:cntrl:]]'
            )
            AND (SELECT count(DISTINCT lower(a)) FROM unnest(p_aliases) a)
                = cardinality(p_aliases)
        $$
        """
    )
    op.execute("ALTER TABLE accounts ADD COLUMN archived_at timestamptz")
    op.execute(
        "ALTER TABLE accounts "
        "ADD CONSTRAINT ck_accounts_key CHECK (key ~ '^[a-z0-9][a-z0-9-]{0,62}$'), "
        "ADD CONSTRAINT ck_accounts_name "
        "CHECK (name = btrim(name) AND length(name) BETWEEN 1 AND 200 "
        "AND name !~ '[[:cntrl:]]'), "
        "ADD CONSTRAINT ck_accounts_aliases CHECK (lattice_account_aliases_valid(aliases)), "
        "ADD CONSTRAINT ck_accounts_archived_at CHECK (archived_at IS NULL "
        "OR archived_at >= created_at)"
    )
    op.execute(
        "GRANT INSERT (organization_id, key, name, aliases, status), "
        "UPDATE (name, aliases, status) ON accounts TO lattice_app"
    )
    op.execute(
        "CREATE POLICY app_account_insert ON accounts AS RESTRICTIVE FOR INSERT "
        "TO lattice_app WITH CHECK (archived_at IS NULL)"
    )
    # An archived account is a read-only record until it is reactivated.
    op.execute(
        "CREATE POLICY app_account_update ON accounts AS RESTRICTIVE FOR UPDATE "
        "TO lattice_app USING (archived_at IS NULL) WITH CHECK (archived_at IS NULL)"
    )
    op.execute(
        f"""
        CREATE FUNCTION lattice_set_account_archived(
          p_actor uuid, p_account uuid, p_archived boolean
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_org uuid := public.lattice_current_org_id();
          v_archived_at timestamptz;
        BEGIN
          {_REQUIRE_ORG}
          {_REQUIRE_ACTOR}
          IF p_archived IS NULL THEN
            RAISE EXCEPTION 'change_invalid' USING ERRCODE = 'LA001';
          END IF;
          SELECT a.archived_at INTO v_archived_at FROM public.accounts a
          WHERE a.organization_id = v_org AND a.id = p_account
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'account_not_found' USING ERRCODE = 'LA001';
          END IF;
          IF (v_archived_at IS NOT NULL) = p_archived THEN
            RETURN false;
          END IF;
          UPDATE public.accounts
          SET archived_at = CASE WHEN p_archived THEN clock_timestamp() END
          WHERE organization_id = v_org AND id = p_account;
          RETURN true;
        END
        $$
        """  # noqa: S608 - fixed SQL fragments, no input
    )

    # --- account_external_refs -------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE account_external_refs (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          source_id uuid NOT NULL,
          ref_kind text NOT NULL,
          external_ref text NOT NULL,
          account_id uuid NOT NULL,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_account_external_refs PRIMARY KEY (id),
          CONSTRAINT fk_account_external_refs_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT fk_account_external_refs_organization_id_source_id_sources
            FOREIGN KEY (organization_id, source_id)
            REFERENCES sources (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_account_external_refs_organization_id_account_id_accounts
            FOREIGN KEY (organization_id, account_id)
            REFERENCES accounts (organization_id, id) ON DELETE RESTRICT,
          CONSTRAINT uq_account_external_refs_reference
            UNIQUE (organization_id, source_id, ref_kind, external_ref),
          CONSTRAINT ck_account_external_refs_ref_kind
            CHECK (ref_kind IN ('ticketing_organization', 'chat_channel')),
          CONSTRAINT ck_account_external_refs_external_ref CHECK (
            external_ref = btrim(external_ref) AND length(external_ref) BETWEEN 1 AND 256
            AND external_ref IS NFC NORMALIZED AND external_ref !~ '[[:cntrl:]]'
            AND (ref_kind <> 'ticketing_organization' OR external_ref ~ '{_TICKETING_REF}')),
          CONSTRAINT ck_account_external_refs_created_by
            CHECK (created_by ~ '^(member|operator):[A-Za-z0-9_.:-]{{1,64}}$')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_account_external_refs_organization_id_account_id "
        "ON account_external_refs (organization_id, account_id)"
    )
    op.execute("ALTER TABLE account_external_refs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE account_external_refs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON account_external_refs "
        "USING (organization_id = lattice_current_org_id()) "
        "WITH CHECK (organization_id = lattice_current_org_id())"
    )
    op.execute(
        "CREATE TRIGGER trg_account_external_refs_updated_at BEFORE UPDATE "
        "ON account_external_refs FOR EACH ROW EXECUTE FUNCTION lattice_set_updated_at()"
    )
    # Every writer: the kind fits the connector, the source is not being deleted, the
    # reference never changes identity, and an archived account receives nothing new.
    op.execute(
        """
        CREATE FUNCTION lattice_check_account_ref() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_connector text;
          v_status text;
          v_archived_at timestamptz;
        BEGIN
          IF TG_OP = 'UPDATE' AND (
            NEW.organization_id, NEW.source_id, NEW.ref_kind, NEW.external_ref
          ) IS DISTINCT FROM (
            OLD.organization_id, OLD.source_id, OLD.ref_kind, OLD.external_ref
          ) THEN
            RAISE EXCEPTION 'reference_immutable' USING ERRCODE = 'LA001';
          END IF;
          SELECT s.connector_type, s.status INTO v_connector, v_status
          FROM public.sources s
          WHERE s.organization_id = NEW.organization_id AND s.id = NEW.source_id;
          IF NOT FOUND OR v_status = 'deleting' THEN
            RAISE EXCEPTION 'source_not_found' USING ERRCODE = 'LA001';
          END IF;
          IF NOT (
            (v_connector = 'ticketing' AND NEW.ref_kind = 'ticketing_organization')
            OR (v_connector = 'chat_export' AND NEW.ref_kind = 'chat_channel')
          ) THEN
            RAISE EXCEPTION 'ref_kind_mismatch' USING ERRCODE = 'LA001';
          END IF;
          IF TG_OP = 'INSERT' OR NEW.account_id IS DISTINCT FROM OLD.account_id THEN
            -- A share lock: archiving (FOR UPDATE) waits for this transaction, or this
            -- one sees the archive it waited for.
            SELECT a.archived_at INTO v_archived_at FROM public.accounts a
            WHERE a.organization_id = NEW.organization_id AND a.id = NEW.account_id
            FOR SHARE;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'account_not_found' USING ERRCODE = 'LA001';
            END IF;
            IF v_archived_at IS NOT NULL THEN
              RAISE EXCEPTION 'account_archived' USING ERRCODE = 'LA001';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_check_account_ref() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_account_external_refs_check BEFORE INSERT OR UPDATE "
        "ON account_external_refs FOR EACH ROW EXECUTE FUNCTION lattice_check_account_ref()"
    )
    op.execute("GRANT SELECT ON account_external_refs TO lattice_app, lattice_worker")

    op.execute(
        f"""
        CREATE FUNCTION lattice_set_account_ref(
          p_actor uuid, p_source uuid, p_ref_kind text, p_external_ref text,
          p_expected uuid, p_account uuid
        ) RETURNS text
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_org uuid := public.lattice_current_org_id();
          v_current uuid;
        BEGIN
          {_REQUIRE_ORG}
          {_REQUIRE_ACTOR}
          IF p_ref_kind IS NULL OR p_external_ref IS NULL OR p_source IS NULL THEN
            RAISE EXCEPTION 'reference_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_external_ref <> btrim(p_external_ref) OR NOT p_external_ref IS NFC NORMALIZED
             OR length(p_external_ref) NOT BETWEEN 1 AND 256 OR p_external_ref ~ '[[:cntrl:]]'
             OR (p_ref_kind = 'ticketing_organization'
                 AND p_external_ref !~ '{_TICKETING_REF}') THEN
            RAISE EXCEPTION 'reference_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.sources s
            WHERE s.organization_id = v_org AND s.id = p_source AND s.status <> 'deleting'
          ) THEN
            RAISE EXCEPTION 'source_not_found' USING ERRCODE = 'LA001';
          END IF;
          -- One change of this reference at a time: the read below is then exact.
          PERFORM {REF_LOCK.format("v_org", "p_source", "p_ref_kind", "p_external_ref")};
          SELECT r.account_id INTO v_current FROM public.account_external_refs r
          WHERE r.organization_id = v_org AND r.source_id = p_source
            AND r.ref_kind = p_ref_kind AND r.external_ref = p_external_ref
          FOR UPDATE;
          IF v_current IS DISTINCT FROM p_expected THEN
            RAISE EXCEPTION 'mapping_changed' USING ERRCODE = 'LA001';
          END IF;
          IF p_account IS NULL THEN
            IF v_current IS NULL THEN
              RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
            END IF;
            DELETE FROM public.account_external_refs
            WHERE organization_id = v_org AND source_id = p_source
              AND ref_kind = p_ref_kind AND external_ref = p_external_ref;
            RETURN 'removed';
          END IF;
          IF v_current = p_account THEN
            RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
          END IF;
          IF v_current IS NULL THEN
            INSERT INTO public.account_external_refs (
              organization_id, source_id, ref_kind, external_ref, account_id, created_by
            ) VALUES (
              v_org, p_source, p_ref_kind, p_external_ref, p_account, 'member:' || p_actor
            );
            RETURN 'created';
          END IF;
          UPDATE public.account_external_refs SET account_id = p_account
          WHERE organization_id = v_org AND source_id = p_source
            AND ref_kind = p_ref_kind AND external_ref = p_external_ref;
          RETURN 'changed';
        END
        $$
        """  # noqa: S608 - fixed SQL fragments, no input
    )
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO lattice_app")

    # --- audit coupling ----------------------------------------------------------------------
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        f"TO lattice_app WITH CHECK ({_AUDIT_INSERT})"
    )
    op.execute(
        """
        CREATE FUNCTION lattice_require_account_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_action text;
        BEGIN
          IF current_user <> 'lattice_app' THEN
            RETURN NULL;
          END IF;
          IF TG_OP = 'INSERT' THEN
            v_action := 'account.created';
          ELSIF OLD.archived_at IS NULL AND NEW.archived_at IS NOT NULL THEN
            v_action := 'account.archived';
          ELSIF OLD.archived_at IS NOT NULL AND NEW.archived_at IS NULL THEN
            v_action := 'account.reactivated';
          ELSE
            v_action := 'account.updated';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id() AND e.organization_id = NEW.organization_id
              AND e.action = v_action AND e.target ->> 'account_id' = NEW.id::text
          ) THEN
            RAISE EXCEPTION 'account change without an audit event'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_require_account_audit() FROM PUBLIC")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_accounts_setup_audit AFTER INSERT OR UPDATE ON accounts "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_account_audit()"
    )
    op.execute(
        """
        CREATE FUNCTION lattice_require_mapping_audit() RETURNS trigger
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
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_require_mapping_audit() FROM PUBLIC")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_account_external_refs_setup_audit "
        "AFTER INSERT OR UPDATE OR DELETE ON account_external_refs "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_mapping_audit()"
    )

    # --- data: mappings leave sources.config --------------------------------------------
    # Tenant tables are visible to the owner only with an organization bound: bind each.
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
          src record;
          ref record;
          acct uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            FOR src IN
              SELECT id, connector_type, config, created_at FROM sources
              WHERE organization_id = org
            LOOP
              FOR ref IN
                SELECT 'ticketing_organization' AS kind, e.key AS value,
                       e.value #>> '{}' AS account_key
                FROM jsonb_each(coalesce(src.config -> 'account_refs', '{}'::jsonb)) e
                WHERE src.connector_type = 'ticketing'
                UNION ALL
                SELECT 'chat_channel', e.key, e.value ->> 'account'
                FROM jsonb_each(coalesce(src.config -> 'channels', '{}'::jsonb)) e
                WHERE src.connector_type = 'chat_export' AND e.value ? 'account'
              LOOP
                SELECT a.id INTO acct FROM accounts a
                WHERE a.organization_id = org AND a.key = ref.account_key;
                IF acct IS NULL THEN
                  RAISE EXCEPTION 'source % maps % to an account key that does not exist',
                    src.id, ref.value;
                END IF;
                -- In effect for every run so far: dated with the source, so the setup
                -- record does not ask for a sync the configuration never needed.
                INSERT INTO account_external_refs (
                  organization_id, source_id, ref_kind, external_ref, account_id, created_by,
                  created_at, updated_at
                ) VALUES (
                  org, src.id, ref.kind, ref.value, acct, 'operator:migration',
                  src.created_at, src.created_at
                );
              END LOOP;
              UPDATE sources SET config = CASE
                WHEN src.config ? 'channels' THEN jsonb_set(
                  src.config - 'account_refs', '{channels}',
                  coalesce((
                    SELECT jsonb_object_agg(e.key, e.value - 'account')
                    FROM jsonb_each(src.config -> 'channels') e
                    WHERE e.value - 'account' <> '{}'::jsonb
                  ), '{}'::jsonb))
                ELSE src.config - 'account_refs'
              END
              WHERE organization_id = org AND id = src.id;
            END LOOP;
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )


def downgrade() -> None:
    # Mappings back into the configuration 0017's ingestion reads.
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            UPDATE sources s SET config = s.config || jsonb_build_object(
              'account_refs', coalesce((
                SELECT jsonb_object_agg(r.external_ref, a.key::text)
                FROM account_external_refs r JOIN accounts a
                  ON a.organization_id = r.organization_id AND a.id = r.account_id
                WHERE r.organization_id = org AND r.source_id = s.id
                  AND r.ref_kind = 'ticketing_organization'
              ), '{}'::jsonb))
            WHERE s.organization_id = org;
            UPDATE sources s SET config = s.config || jsonb_build_object(
              'channels', coalesce(s.config -> 'channels', '{}'::jsonb) || coalesce((
                SELECT jsonb_object_agg(
                  r.external_ref,
                  coalesce(s.config -> 'channels' -> r.external_ref, '{}'::jsonb)
                    || jsonb_build_object('account', a.key::text))
                FROM account_external_refs r JOIN accounts a
                  ON a.organization_id = r.organization_id AND a.id = r.account_id
                WHERE r.organization_id = org AND r.source_id = s.id
                  AND r.ref_kind = 'chat_channel'
              ), '{}'::jsonb))
            WHERE s.organization_id = org AND s.connector_type = 'chat_export';
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )
    op.execute("DROP TRIGGER trg_account_external_refs_setup_audit ON account_external_refs")
    op.execute("DROP FUNCTION lattice_require_mapping_audit()")
    op.execute("DROP TRIGGER trg_accounts_setup_audit ON accounts")
    op.execute("DROP FUNCTION lattice_require_account_audit()")
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        f"TO lattice_app WITH CHECK ({_AUDIT_INSERT_0016})"
    )
    for function in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION {function}")
    op.execute("DROP TABLE account_external_refs")
    op.execute("DROP FUNCTION lattice_check_account_ref()")
    op.execute("DROP POLICY app_account_update ON accounts")
    op.execute("DROP POLICY app_account_insert ON accounts")
    op.execute(
        "REVOKE INSERT (organization_id, key, name, aliases, status), "
        "UPDATE (name, aliases, status) ON accounts FROM lattice_app"
    )
    op.execute(
        "ALTER TABLE accounts DROP CONSTRAINT ck_accounts_archived_at, "
        "DROP CONSTRAINT ck_accounts_aliases, DROP CONSTRAINT ck_accounts_name, "
        "DROP CONSTRAINT ck_accounts_key, DROP COLUMN archived_at"
    )
    op.execute("DROP FUNCTION lattice_account_aliases_valid(text[])")
