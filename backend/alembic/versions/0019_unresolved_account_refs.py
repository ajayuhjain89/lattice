"""Unmapping leaves a reference unresolved: a missing account mapping never widens access
(Phase 12, P12.3 correction; phase-12-plan C10).

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-13
Reversible: no (the downgrade drops unresolved references: 0018 would unscope their records)
Locks: brief ACCESS EXCLUSIVE on account_external_refs (DROP NOT NULL, no scan)

0018 deleted the row on unmap, so the next sync could no longer tell a reference that had
an account from one that never had one: a non-account-scoped source indexed its records
without the account, readable under the source's other rules. Now:

* ``account_external_refs.account_id`` may be NULL: an **unresolved** reference -- known
  to name an account, currently mapped to none. Only an unmap creates one (an INSERT
  must name an account, for every writer); mapping it again updates the same row. The
  composite foreign key does not apply to NULL; the check trigger skips the account
  checks for it.
* ``lattice_set_account_ref``: unmap sets ``account_id`` to NULL (``'unresolved'``)
  instead of deleting; map of an unresolved reference updates it (``'restored'``).
  ``expected`` is still the account reviewed (NULL for both "never mapped" and
  "unresolved"), so the 409 contract is unchanged. Audit coupling is unchanged (UPDATE).
* Ingestion (``lattice.ingestion.access``) fails a record whose reference is unresolved
  (``access_unmappable:account``) on every source; its indexed document is quarantined
  at that sync and restored by the first sync after a mapping exists again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# As 0018 (one reference of one source; lattice.accounts).
REF_LOCK = (
    "pg_advisory_xact_lock(1280460102, "
    "hashtext({0}::text || '/' || {1}::text || '/' || {2} || '/' || {3}))"
)
_REQUIRE_ORG = """
  IF v_org IS NULL THEN
    RAISE EXCEPTION 'no organization is bound' USING ERRCODE = 'insufficient_privilege';
  END IF;
"""
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

_CHECK = """
CREATE OR REPLACE FUNCTION lattice_check_account_ref() RETURNS trigger
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
  {unresolved}
  IF {account_condition} THEN
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
_CHANGED = "(TG_OP = 'INSERT' OR NEW.account_id IS DISTINCT FROM OLD.account_id)"
# 0019: a reference starts mapped; only an unmap makes one unresolved.
_INSERT_NAMES_AN_ACCOUNT = """
  IF TG_OP = 'INSERT' AND NEW.account_id IS NULL THEN
    RAISE EXCEPTION 'account_not_found' USING ERRCODE = 'LA001';
  END IF;
"""

_SET_REF = f"""
CREATE OR REPLACE FUNCTION lattice_set_account_ref(
  p_actor uuid, p_source uuid, p_ref_kind text, p_external_ref text,
  p_expected uuid, p_account uuid
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
  v_org uuid := public.lattice_current_org_id();
  v_current uuid;
  v_exists boolean;
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
  v_exists := FOUND;
  IF v_current IS DISTINCT FROM p_expected THEN
    RAISE EXCEPTION 'mapping_changed' USING ERRCODE = 'LA001';
  END IF;
  IF v_current IS NOT DISTINCT FROM p_account THEN
    RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
  END IF;
  IF NOT v_exists THEN
    IF p_account IS NULL THEN
      RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
    END IF;
    INSERT INTO public.account_external_refs (
      organization_id, source_id, ref_kind, external_ref, account_id, created_by
    ) VALUES (
      v_org, p_source, p_ref_kind, p_external_ref, p_account, 'member:' || p_actor
    );
    RETURN 'created';
  END IF;
  -- Unmap keeps the row with no account: the reference stays known, and its records
  -- fail closed at the next sync instead of losing their account.
  UPDATE public.account_external_refs SET account_id = p_account
  WHERE organization_id = v_org AND source_id = p_source
    AND ref_kind = p_ref_kind AND external_ref = p_external_ref;
  RETURN CASE
    WHEN p_account IS NULL THEN 'unresolved'
    WHEN v_current IS NULL THEN 'restored'
    ELSE 'changed'
  END;
END
$$
"""  # noqa: S608 - fixed SQL fragments, no input

_SET_REF_0018 = f"""
CREATE OR REPLACE FUNCTION lattice_set_account_ref(
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


def upgrade() -> None:
    # Migrated with 0018 in one transaction, its data move queued the deferred audit-coupling
    # checks (a no-op for the owner): run them first, or the table cannot be altered.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("ALTER TABLE account_external_refs ALTER COLUMN account_id DROP NOT NULL")
    op.execute(
        _CHECK.replace("{unresolved}", _INSERT_NAMES_AN_ACCOUNT).replace(
            "{account_condition}", f"NEW.account_id IS NOT NULL AND {_CHANGED}"
        )
    )
    # CREATE OR REPLACE keeps the owner, the SECURITY DEFINER grants and the EXECUTE grant.
    op.execute(_SET_REF)


def downgrade() -> None:
    op.execute(_SET_REF_0018)
    # Unresolved references cannot be represented in 0018: delete them (an operator path,
    # outside the API role's audit coupling).
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            DELETE FROM account_external_refs
            WHERE organization_id = org AND account_id IS NULL;
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )
    # The deletes queued the deferred audit-coupling checks (a no-op for the owner): run
    # them now, or the table cannot be altered in this transaction.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("ALTER TABLE account_external_refs ALTER COLUMN account_id SET NOT NULL")
    op.execute(_CHECK.replace("{unresolved}", "").replace("{account_condition}", _CHANGED))
