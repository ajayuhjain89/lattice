"""Members and administrators in the product (Phase 12, P12.2; ADR-017; phase-12-plan
C2.1-C2.3, D-30, D-31, D-36).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-09
Reversible: no (without the marker, members added by hand would turn SCIM-managed)
Locks: brief ACCESS EXCLUSIVE on directory_identities (new column with a constant
  default: no rewrite) and memberships (policy, trigger); the backfill updates the
  directory identities that SCIM did not create (few: the operator's first
  administrators)

* ``directory_identities.managed_by`` -- who owns the person's lifecycle:
  ``identity_provider`` (SCIM created them; D-20, the default, and the only value the
  API role may insert directly) or ``organization`` (an administrator added them by
  hand, or the operator's ``org create`` made them the first administrator). Backfilled
  from the audit trail: an identity whose membership was created by a ``scim:`` actor
  stays ``identity_provider``; every other one becomes ``organization``. SCIM sees only
  ``identity_provider`` identities; their identifiers still count for its uniqueness.
* ``lattice_app`` may read ``oidc_connections.issuer`` and ``subject_claim``: the
  add-member form names the claim whose exact value identifies a person.
* ``app_member_status`` -- the API may set ``removed`` only on an identity-provider
  member (SCIM ``DELETE``); a member added by hand is suspended, never removed.
* Fenced functions (``SECURITY DEFINER``, owned by ``lattice_owner``, search path
  pinned, ``EXECUTE`` for ``lattice_app`` only). Each runs bound to the caller's
  organization (``lattice_current_org_id()``, row-level security as everywhere), checks
  that the acting membership is an active administrator of it, and refuses with SQLSTATE
  ``LA001`` and a fixed reason as the message. The API writes the
  ``access.member_changed`` event in the same transaction: the 0013 audit triggers are
  deferred and fire as ``lattice_app`` at commit, so a function's writes cannot commit
  without it.

  - ``lattice_create_member(actor, email, display_name, given_name, family_name,
    identifier)`` -- a new user owned by the organization, a membership with the D-20
    least-access defaults written by the function (active; not an administrator;
    clearance ``internal``; scope ``assigned``; nothing assigned) and the directory
    identity ``(active connection, identifier)``, ``managed_by = organization``. The
    identifier must arrive NFC-normalised and trimmed (case preserved); it is compared
    byte for byte, never as an e-mail. Refused: no active connection; the identifier on
    the connection, or the e-mail among the organization's people, already taken.
  - ``lattice_set_admin(actor, membership, administrator)`` -- for another member only,
    not a removed one; under the organization's administrator-set lock it re-checks
    the actor and refuses a change that would leave no active administrator.
  - ``lattice_reinstate_member(actor, membership)`` -- a removed member becomes active
    with least access (clearance, scope, accounts, departments, administrator-defined
    groups and administrator grants reset; never an administrator) on their original
    directory identity, which must be on the organization's active connection; refused
    if another member now has their e-mail. Sessions stay revoked.

* **Last administrator** (ADR-017): a deferred constraint trigger on ``memberships``
  re-checks, at commit, every API-role write that takes an active administrator out of
  the administrator set (capability revoked, suspended or removed): under the
  organization's administrator-set lock it counts the active administrators and refuses
  zero. The identity provider is exempt (a transaction whose event for that member is by
  one of the organization's SCIM tokens): a person who left must lose access even when
  they were the last administrator -- the organization then needs an operator. Operators
  are not the API role and are not bound either.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# pg_advisory_xact_lock(class, key): the organization's administrator set and its
# member creation. Classes are b"LADM" and b"LMEM" (lattice.access.lifecycle).
ADMIN_LOCK = "pg_advisory_xact_lock(1279345741, hashtext({0}::text))"
MEMBER_LOCK = "pg_advisory_xact_lock(1280132429, hashtext({0}::text))"

_FUNCTIONS = (
    "lattice_create_member(uuid, text, text, text, text, text)",
    "lattice_set_admin(uuid, uuid, boolean)",
    "lattice_reinstate_member(uuid, uuid)",
)
_IDP_IDENTITY = (
    "EXISTS (SELECT 1 FROM directory_identities d WHERE d.organization_id = "
    "memberships.organization_id AND d.membership_id = memberships.id "
    "AND d.managed_by = 'identity_provider')"
)
_ANY_IDENTITY = (
    "EXISTS (SELECT 1 FROM directory_identities d WHERE d.organization_id = "
    "memberships.organization_id AND d.membership_id = memberships.id)"
)
# The active administrators of the organization bound as v_org.
_ACTIVE_ADMINS = (
    "SELECT count(*) FROM public.memberships m JOIN public.users u ON u.id = m.user_id "
    "WHERE m.organization_id = {0} AND m.is_admin AND m.status = 'active' "
    "AND u.status = 'active'"
)
# Refuse unless p_actor is an active administrator of v_org.
_REQUIRE_ACTOR = """
  IF NOT EXISTS (
    SELECT 1 FROM public.memberships m JOIN public.users u ON u.id = m.user_id
    WHERE m.organization_id = v_org AND m.id = p_actor AND m.is_admin
      AND m.status = 'active' AND u.status = 'active'
  ) THEN
    RAISE EXCEPTION 'not_administrator' USING ERRCODE = 'LA001';
  END IF;
"""
_REQUIRE_ORG = """
  IF v_org IS NULL THEN
    RAISE EXCEPTION 'no organization is bound' USING ERRCODE = 'insufficient_privilege';
  END IF;
"""


def upgrade() -> None:
    # --- Who owns a person's lifecycle --------------------------------------------------
    op.execute(
        "ALTER TABLE directory_identities ADD COLUMN managed_by text NOT NULL "
        "DEFAULT 'identity_provider' CONSTRAINT ck_directory_identities_managed_by "
        "CHECK (managed_by IN ('identity_provider', 'organization'))"
    )
    # Tenant tables are visible to the owner only with an organization bound: bind each.
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            UPDATE directory_identities d SET managed_by = 'organization'
            WHERE d.organization_id = org AND NOT EXISTS (
              SELECT 1 FROM audit_events e
              WHERE e.organization_id = org AND e.action = 'access.member_changed'
                AND e.before IS NULL AND e.actor LIKE 'scim:%'
                AND e.target ->> 'membership_id' = d.membership_id::text
            );
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )
    # The add-member form names the claim whose exact value identifies a person.
    op.execute("GRANT SELECT (issuer, subject_claim) ON oidc_connections TO lattice_app")
    op.execute(
        "CREATE POLICY app_identity_provider_insert ON directory_identities AS RESTRICTIVE "
        "FOR INSERT TO lattice_app WITH CHECK (managed_by = 'identity_provider')"
    )
    op.execute("DROP POLICY app_member_status ON memberships")
    op.execute(
        "CREATE POLICY app_member_status ON memberships AS RESTRICTIVE FOR UPDATE "
        "TO lattice_app USING (status <> 'removed') "
        "WITH CHECK (status IN ('active', 'suspended') OR "
        f"(status = 'removed' AND {_IDP_IDENTITY}))"
    )

    # --- Fenced functions -----------------------------------------------------------------
    op.execute(
        f"""
        CREATE FUNCTION lattice_create_member(
          p_actor uuid, p_email text, p_display_name text, p_given_name text,
          p_family_name text, p_identifier text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_org uuid := public.lattice_current_org_id();
          v_connection uuid;
          v_user uuid;
          v_membership uuid;
        BEGIN
          {_REQUIRE_ORG}
          -- One creation at a time per organization: the uniqueness checks below are
          -- then exact (a concurrent SCIM create still meets the unique indexes).
          PERFORM {MEMBER_LOCK.format("v_org")};
          {_REQUIRE_ACTOR}
          IF p_identifier IS NULL OR p_identifier <> btrim(p_identifier)
             OR NOT p_identifier IS NFC NORMALIZED
             OR length(p_identifier) NOT BETWEEN 1 AND 255 OR p_identifier ~ '[[:cntrl:]]' THEN
            RAISE EXCEPTION 'identifier_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_email IS NULL OR p_email <> btrim(p_email)
             OR p_email !~ '^[^@[:space:][:cntrl:]]+@[^@[:space:][:cntrl:]]+$'
             OR length(p_email) > 255 THEN
            RAISE EXCEPTION 'email_invalid' USING ERRCODE = 'LA001';
          END IF;
          IF p_display_name IS NULL OR length(btrim(p_display_name)) NOT BETWEEN 1 AND 200
             OR p_display_name ~ '[[:cntrl:]]'
             OR coalesce(length(p_given_name), 0) > 200
             OR coalesce(length(p_family_name), 0) > 200
             OR coalesce(p_given_name, '') ~ '[[:cntrl:]]'
             OR coalesce(p_family_name, '') ~ '[[:cntrl:]]' THEN
            RAISE EXCEPTION 'name_invalid' USING ERRCODE = 'LA001';
          END IF;
          SELECT c.id INTO v_connection FROM public.oidc_connections c
          WHERE c.organization_id = v_org AND c.status = 'active';
          IF v_connection IS NULL THEN
            RAISE EXCEPTION 'no_active_connection' USING ERRCODE = 'LA001';
          END IF;
          -- Exact identifier on this connection: removed people keep theirs (re-hire is
          -- reinstatement, never a second record).
          IF EXISTS (
            SELECT 1 FROM public.directory_identities d
            WHERE d.organization_id = v_org AND d.connection_id = v_connection
              AND d.external_id = p_identifier
          ) THEN
            RAISE EXCEPTION 'identifier_taken' USING ERRCODE = 'LA001';
          END IF;
          -- The e-mail among this organization's people only; other organizations'
          -- users are never read.
          IF EXISTS (
            SELECT 1 FROM public.memberships m JOIN public.users u ON u.id = m.user_id
            WHERE m.organization_id = v_org AND lower(u.email) = lower(p_email)
          ) OR EXISTS (
            SELECT 1 FROM public.users u
            WHERE u.home_organization_id = v_org AND lower(u.email) = lower(p_email)
          ) OR EXISTS (
            SELECT 1 FROM public.directory_identities d
            WHERE d.organization_id = v_org AND d.connection_id = v_connection
              AND lower(d.user_name) = lower(p_email)
          ) THEN
            RAISE EXCEPTION 'email_taken' USING ERRCODE = 'LA001';
          END IF;

          INSERT INTO public.users (email, display_name, home_organization_id)
          VALUES (p_email, btrim(p_display_name), v_org) RETURNING id INTO v_user;
          -- D-20 least access, written here: the request cannot carry access.
          INSERT INTO public.memberships (
            organization_id, user_id, status, is_admin, clearance, account_scope
          ) VALUES (v_org, v_user, 'active', false, 'internal', 'assigned')
          RETURNING id INTO v_membership;
          INSERT INTO public.directory_identities (
            organization_id, connection_id, membership_id, external_id, user_name,
            given_name, family_name, managed_by
          ) VALUES (
            v_org, v_connection, v_membership, p_identifier, p_email,
            nullif(btrim(p_given_name), ''), nullif(btrim(p_family_name), ''), 'organization'
          );
          RETURN v_membership;
        END
        $$
        """  # noqa: S608 - fixed SQL fragments, no input
    )
    op.execute(
        f"""
        CREATE FUNCTION lattice_set_admin(p_actor uuid, p_membership uuid, p_admin boolean)
        RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_org uuid := public.lattice_current_org_id();
          v_status text;
          v_is_admin boolean;
          v_user_status text;
          v_remaining bigint;
        BEGIN
          {_REQUIRE_ORG}
          -- Lock, then count: under READ COMMITTED each statement below sees every
          -- transaction that committed before the lock was granted.
          PERFORM {ADMIN_LOCK.format("v_org")};
          {_REQUIRE_ACTOR}
          IF p_membership = p_actor THEN
            RAISE EXCEPTION 'own_access' USING ERRCODE = 'LA001';
          END IF;
          SELECT m.status, m.is_admin, u.status INTO v_status, v_is_admin, v_user_status
          FROM public.memberships m JOIN public.users u ON u.id = m.user_id
          WHERE m.organization_id = v_org AND m.id = p_membership
          FOR UPDATE OF m;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'member_not_found' USING ERRCODE = 'LA001';
          END IF;
          IF v_status = 'removed' THEN
            RAISE EXCEPTION 'member_removed' USING ERRCODE = 'LA001';
          END IF;
          IF v_is_admin = p_admin THEN
            RETURN false;
          END IF;
          IF NOT p_admin AND v_status = 'active' AND v_user_status = 'active' THEN
            {_ACTIVE_ADMINS.format("v_org")} AND m.id <> p_membership INTO v_remaining;
            IF v_remaining = 0 THEN
              RAISE EXCEPTION 'last_administrator' USING ERRCODE = 'LA001';
            END IF;
          END IF;
          UPDATE public.memberships
          SET is_admin = p_admin, authz_updated_at = clock_timestamp(), updated_at = now()
          WHERE organization_id = v_org AND id = p_membership;
          RETURN true;
        END
        $$
        """  # noqa: S608 - fixed SQL fragments, no input
    )
    op.execute(
        f"""
        CREATE FUNCTION lattice_reinstate_member(p_actor uuid, p_membership uuid)
        RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_org uuid := public.lattice_current_org_id();
          v_status text;
          v_user uuid;
          v_user_status text;
          v_email text;
          v_connection uuid;
        BEGIN
          {_REQUIRE_ORG}
          {_REQUIRE_ACTOR}
          SELECT m.status, m.user_id, u.status, u.email
            INTO v_status, v_user, v_user_status, v_email
          FROM public.memberships m JOIN public.users u ON u.id = m.user_id
          WHERE m.organization_id = v_org AND m.id = p_membership
          FOR UPDATE OF m;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'member_not_found' USING ERRCODE = 'LA001';
          END IF;
          IF v_status <> 'removed' THEN
            RAISE EXCEPTION 'not_removed' USING ERRCODE = 'LA001';
          END IF;
          -- The original directory identity, on the organization's active connection
          -- (a replaced identity namespace cannot be relinked here).
          SELECT d.connection_id INTO v_connection
          FROM public.directory_identities d
          JOIN public.oidc_connections c
            ON c.organization_id = d.organization_id AND c.id = d.connection_id
           AND c.status = 'active'
          WHERE d.organization_id = v_org AND d.membership_id = p_membership;
          IF v_connection IS NULL THEN
            RAISE EXCEPTION 'identity_not_current' USING ERRCODE = 'LA001';
          END IF;
          IF v_user_status <> 'active' THEN
            RAISE EXCEPTION 'user_inactive' USING ERRCODE = 'LA001';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.memberships m JOIN public.users u ON u.id = m.user_id
            WHERE m.organization_id = v_org AND m.id <> p_membership
              AND lower(u.email) = lower(v_email)
          ) THEN
            RAISE EXCEPTION 'email_taken' USING ERRCODE = 'LA001';
          END IF;

          -- Least access (D-36): nothing they had is restored.
          DELETE FROM public.account_assignments
          WHERE organization_id = v_org AND membership_id = p_membership;
          DELETE FROM public.membership_departments
          WHERE organization_id = v_org AND membership_id = p_membership;
          DELETE FROM public.access_group_members gm
          USING public.access_groups g
          WHERE gm.organization_id = v_org AND gm.membership_id = p_membership
            AND g.organization_id = gm.organization_id AND g.id = gm.group_id
            AND g.origin = 'admin';
          DELETE FROM public.document_grants
          WHERE organization_id = v_org AND membership_id = p_membership
            AND origin = 'admin' AND principal_type = 'membership';
          UPDATE public.memberships
          SET status = 'active', is_admin = false, clearance = 'internal',
              account_scope = 'assigned', authz_updated_at = clock_timestamp(),
              updated_at = now()
          WHERE organization_id = v_org AND id = p_membership;
        END
        $$
        """  # noqa: S608 - fixed SQL fragments, no input
    )
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO lattice_app")

    # --- Last administrator, re-checked at commit --------------------------------------
    op.execute(
        f"""
        CREATE FUNCTION lattice_require_an_administrator() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_remaining bigint;
        BEGIN
          IF current_user <> 'lattice_app' THEN
            RETURN NULL;
          END IF;
          IF NOT (OLD.is_admin AND OLD.status = 'active')
             OR (NEW.is_admin AND NEW.status = 'active') THEN
            RETURN NULL;
          END IF;
          -- The identity provider may take the last administrator away (C18).
          IF EXISTS (
            SELECT 1 FROM public.audit_events e
            JOIN public.scim_tokens t
              ON t.organization_id = e.organization_id AND e.actor = 'scim:' || t.id::text
            WHERE e.txid = pg_current_xact_id() AND e.organization_id = NEW.organization_id
              AND e.target ->> 'membership_id' = NEW.id::text
          ) THEN
            RETURN NULL;
          END IF;
          PERFORM {ADMIN_LOCK.format("NEW.organization_id")};
          {_ACTIVE_ADMINS.format("NEW.organization_id")} INTO v_remaining;
          IF v_remaining = 0 THEN
            RAISE EXCEPTION 'last_administrator' USING ERRCODE = 'LA001';
          END IF;
          RETURN NULL;
        END
        $$
        """  # noqa: S608 - fixed SQL fragments, no input
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_require_an_administrator() FROM PUBLIC")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_memberships_administrator_set "
        "AFTER UPDATE OF is_admin, status ON memberships "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_an_administrator()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_memberships_administrator_set ON memberships")
    op.execute("DROP FUNCTION lattice_require_an_administrator()")
    for function in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION {function}")
    op.execute("DROP POLICY app_member_status ON memberships")
    op.execute(
        "CREATE POLICY app_member_status ON memberships AS RESTRICTIVE FOR UPDATE "
        "TO lattice_app USING (status <> 'removed') "
        "WITH CHECK (status IN ('active', 'suspended') OR "
        f"(status = 'removed' AND {_ANY_IDENTITY}))"
    )
    op.execute("DROP POLICY app_identity_provider_insert ON directory_identities")
    op.execute("REVOKE SELECT (issuer, subject_claim) ON oidc_connections FROM lattice_app")
    op.execute("ALTER TABLE directory_identities DROP COLUMN managed_by")
