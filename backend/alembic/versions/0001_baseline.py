"""Baseline: tenancy, identity and sessions with forced row-level security.

Revision ID: 0001
Revises:
Create Date: 2026-09-27

Creates the Phase 1 tables (docs/data-model.md §2), their row-level-security policies,
the privileges of the runtime role, and the two SECURITY DEFINER pre-authentication
lookup functions.

Prerequisites (created by infra/postgres/bootstrap.sql, as superuser): the roles
lattice_owner, lattice_app and lattice_auth, and the citext and vector extensions.
This migration runs as lattice_owner and only verifies them.

Row-level security model:

* Tenant-owned tables (memberships, departments, membership_departments, accounts,
  account_assignments, sessions): one policy, organization_id = the bound org. It
  applies to every role, including the table owner (FORCE).
* organizations: visible only as the bound org itself.
* users / external_identities (global identities): visible only if the user has a
  membership in the bound org.
* lattice_owner gets provisioning policies on the three global/root tables only, so
  operator tools (seed) can create tenants and identities. Tenant-owned tables have
  no owner exception.
* lattice_auth gets read policies on the tables the pre-authentication lookups need.
  It is NOLOGIN; only the two SECURITY DEFINER functions it owns run as it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "memberships",
    "departments",
    "membership_departments",
    "accounts",
    "account_assignments",
    "sessions",
)
GLOBAL_TABLES = ("organizations", "users", "external_identities")
TABLES_WITH_UPDATED_AT = ("organizations", "users", "memberships", "departments", "accounts")


def _status_check(table: str, values: tuple[str, ...], column: str = "status") -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"CONSTRAINT ck_{table}_{column} CHECK ({column} IN ({allowed}))"


def upgrade() -> None:
    # --- Prerequisites ------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'citext')
             OR NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
            RAISE EXCEPTION 'Extensions citext and vector are required. '
              'Run infra/postgres/bootstrap.sql as a superuser first.';
          END IF;
          IF (SELECT count(*) FROM pg_roles
              WHERE rolname IN ('lattice_owner', 'lattice_app', 'lattice_auth')) <> 3 THEN
            RAISE EXCEPTION 'Roles lattice_owner, lattice_app and lattice_auth are required. '
              'Run infra/postgres/bootstrap.sql as a superuser first.';
          END IF;
        END
        $$;
        """
    )

    # --- Helpers --------------------------------------------------------------------
    # NULLIF matters: once app.org_id has been set on a pooled connection,
    # current_setting() returns '' (not NULL) after the transaction ends, and ''::uuid
    # would raise instead of matching nothing.
    op.execute(
        """
        CREATE FUNCTION lattice_current_org_id() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION lattice_set_updated_at() RETURNS trigger
        LANGUAGE plpgsql
        AS $$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$
        """
    )

    # --- Tables ---------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE organizations (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          slug citext NOT NULL,
          name text NOT NULL,
          status text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_organizations PRIMARY KEY (id),
          CONSTRAINT uq_organizations_slug UNIQUE (slug),
          CONSTRAINT ck_organizations_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9-]{{0,62}}$'),
          {_status_check("organizations", ("active", "suspended", "deleting"))}
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE users (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          email citext NOT NULL,
          display_name text NOT NULL,
          status text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_users PRIMARY KEY (id),
          CONSTRAINT uq_users_email UNIQUE (email),
          {_status_check("users", ("active", "disabled", "deleted"))}
        )
        """
    )
    op.execute(
        """
        CREATE TABLE external_identities (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          issuer text NOT NULL,
          subject text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_external_identities PRIMARY KEY (id),
          CONSTRAINT fk_external_identities_user_id_users
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
          CONSTRAINT uq_external_identities_issuer_subject UNIQUE (issuer, subject)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_external_identities_user_id ON external_identities (user_id)
        """
    )
    op.execute(
        f"""
        CREATE TABLE memberships (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          user_id uuid NOT NULL,
          status text NOT NULL DEFAULT 'active',
          is_admin boolean NOT NULL DEFAULT false,
          clearance text NOT NULL DEFAULT 'internal',
          account_scope text NOT NULL DEFAULT 'assigned',
          role_label text,
          authz_updated_at timestamptz NOT NULL DEFAULT now(),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_memberships PRIMARY KEY (id),
          CONSTRAINT fk_memberships_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT fk_memberships_user_id_users
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
          CONSTRAINT uq_memberships_organization_id_user_id UNIQUE (organization_id, user_id),
          CONSTRAINT uq_memberships_organization_id_id UNIQUE (organization_id, id),
          {_status_check("memberships", ("active", "suspended", "removed"))},
          {_status_check("memberships", ("internal", "confidential"), "clearance")},
          {_status_check("memberships", ("all", "assigned"), "account_scope")}
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_memberships_user_id ON memberships (user_id)
        """
    )
    op.execute(
        """
        CREATE TABLE departments (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          key citext NOT NULL,
          name text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_departments PRIMARY KEY (id),
          CONSTRAINT fk_departments_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT uq_departments_organization_id_key UNIQUE (organization_id, key),
          CONSTRAINT uq_departments_organization_id_id UNIQUE (organization_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE membership_departments (
          organization_id uuid NOT NULL,
          membership_id uuid NOT NULL,
          department_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_membership_departments PRIMARY KEY (membership_id, department_id),
          CONSTRAINT fk_membership_departments_organization_id_membership_id_memberships
            FOREIGN KEY (organization_id, membership_id)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_membership_departments_organization_id_department_id_departments
            FOREIGN KEY (organization_id, department_id)
            REFERENCES departments (organization_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE accounts (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          key citext NOT NULL,
          name text NOT NULL,
          aliases text[] NOT NULL DEFAULT '{{}}',
          status text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_accounts PRIMARY KEY (id),
          CONSTRAINT fk_accounts_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT uq_accounts_organization_id_key UNIQUE (organization_id, key),
          CONSTRAINT uq_accounts_organization_id_id UNIQUE (organization_id, id),
          {_status_check("accounts", ("active", "churned", "prospect"))}
        )
        """
    )
    op.execute(
        """
        CREATE TABLE account_assignments (
          organization_id uuid NOT NULL,
          membership_id uuid NOT NULL,
          account_id uuid NOT NULL,
          assigned_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_account_assignments PRIMARY KEY (membership_id, account_id),
          CONSTRAINT fk_account_assignments_organization_id_membership_id_memberships
            FOREIGN KEY (organization_id, membership_id)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_account_assignments_organization_id_account_id_accounts
            FOREIGN KEY (organization_id, account_id)
            REFERENCES accounts (organization_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE sessions (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          token_hash bytea NOT NULL,
          user_id uuid NOT NULL,
          organization_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          last_seen_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          CONSTRAINT pk_sessions PRIMARY KEY (id),
          CONSTRAINT uq_sessions_token_hash UNIQUE (token_hash),
          -- A session can exist only for a real membership in its organization.
          CONSTRAINT fk_sessions_organization_id_user_id_memberships
            FOREIGN KEY (organization_id, user_id)
            REFERENCES memberships (organization_id, user_id) ON DELETE CASCADE,
          CONSTRAINT ck_sessions_token_hash_sha256 CHECK (octet_length(token_hash) = 32),
          CONSTRAINT ck_sessions_expiry_after_creation CHECK (expires_at > created_at)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_sessions_organization_id ON sessions (organization_id)
        """
    )

    for table in TABLES_WITH_UPDATED_AT:
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION lattice_set_updated_at()"
        )

    # --- Row-level security -----------------------------------------------------------
    for table in (*GLOBAL_TABLES, *TENANT_TABLES):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    for table in TENANT_TABLES:
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organization_id = lattice_current_org_id()) "
            "WITH CHECK (organization_id = lattice_current_org_id())"
        )

    op.execute(
        "CREATE POLICY tenant_self ON organizations "
        "USING (id = lattice_current_org_id()) WITH CHECK (id = lattice_current_org_id())"
    )
    # Subqueries in policies are themselves filtered by RLS, so "memberships" here means
    # memberships of the bound organization only.
    op.execute(
        "CREATE POLICY member_visible ON users FOR SELECT "
        "USING (EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = users.id))"
    )
    op.execute(
        "CREATE POLICY member_visible ON external_identities FOR SELECT "
        "USING (EXISTS (SELECT 1 FROM users u WHERE u.id = external_identities.user_id))"
    )

    for table in GLOBAL_TABLES:
        op.execute(
            f"CREATE POLICY owner_provisioning ON {table} TO lattice_owner "
            "USING (true) WITH CHECK (true)"
        )
    for table in ("organizations", "users", "external_identities", "memberships", "sessions"):
        op.execute(f"CREATE POLICY auth_lookup ON {table} FOR SELECT TO lattice_auth USING (true)")

    # --- Privileges -------------------------------------------------------------------
    # Least privilege for the runtime role in Phase 1: read tenancy/identity data,
    # create sessions, and update only the two mutable session columns. No DELETE.
    op.execute(
        "GRANT SELECT ON organizations, users, external_identities, memberships, departments, "
        "membership_departments, accounts, account_assignments TO lattice_app"
    )
    op.execute("GRANT SELECT, INSERT ON sessions TO lattice_app")
    op.execute("GRANT UPDATE (last_seen_at, revoked_at) ON sessions TO lattice_app")
    op.execute(
        "GRANT SELECT ON organizations, users, external_identities, memberships, sessions "
        "TO lattice_auth"
    )

    # --- Pre-authentication lookups ---------------------------------------------------
    # Before a request's organization is known, these are the only way to read identity
    # data. Each answers one narrow question for a caller who already holds a secret
    # (the session token) or a verified identity (issuer + subject).
    op.execute(
        """
        CREATE FUNCTION lattice_resolve_session(p_token_hash bytea)
        RETURNS TABLE (
          session_id uuid, user_id uuid, organization_id uuid, created_at timestamptz,
          last_seen_at timestamptz, expires_at timestamptz, revoked_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          SELECT s.id, s.user_id, s.organization_id, s.created_at, s.last_seen_at,
                 s.expires_at, s.revoked_at
          FROM public.sessions s
          WHERE octet_length(p_token_hash) = 32 AND s.token_hash = p_token_hash
        $$
        """
    )
    # Active memberships (active user, active organization) for a verified identity.
    op.execute(
        """
        CREATE FUNCTION lattice_identity_memberships(p_issuer text, p_subject text)
        RETURNS TABLE (user_id uuid, organization_id uuid, organization_slug text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          SELECT u.id, o.id, o.slug::text
          FROM public.external_identities ei
          JOIN public.users u ON u.id = ei.user_id AND u.status = 'active'
          JOIN public.memberships m ON m.user_id = u.id AND m.status = 'active'
          JOIN public.organizations o ON o.id = m.organization_id AND o.status = 'active'
          WHERE ei.issuer = p_issuer AND ei.subject = p_subject
          ORDER BY o.slug
        $$
        """
    )
    # Set the ACL while lattice_owner still owns the functions: after the transfer it
    # can no longer change it (it does not inherit lattice_auth's privileges), and
    # PostgreSQL would reduce these statements to warnings. ALTER ... OWNER TO carries
    # the ACL over to the new owner.
    op.execute("REVOKE ALL ON FUNCTION lattice_resolve_session(bytea) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION lattice_identity_memberships(text, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION lattice_resolve_session(bytea) TO lattice_app")
    op.execute("GRANT EXECUTE ON FUNCTION lattice_identity_memberships(text, text) TO lattice_app")
    # A new function owner must hold CREATE on the schema at the moment of the ownership
    # transfer, and never afterwards. The grant and the revoke run in this migration's
    # transaction, so no other session ever observes lattice_auth holding CREATE.
    op.execute("GRANT CREATE ON SCHEMA public TO lattice_auth")
    op.execute("ALTER FUNCTION lattice_resolve_session(bytea) OWNER TO lattice_auth")
    op.execute("ALTER FUNCTION lattice_identity_memberships(text, text) OWNER TO lattice_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM lattice_auth")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS lattice_identity_memberships(text, text)")
    op.execute("DROP FUNCTION IF EXISTS lattice_resolve_session(bytea)")
    op.execute(
        "DROP TABLE IF EXISTS sessions, account_assignments, accounts, membership_departments, "
        "departments, memberships, external_identities, users, organizations"
    )
    op.execute("DROP FUNCTION IF EXISTS lattice_set_updated_at()")
    op.execute("DROP FUNCTION IF EXISTS lattice_current_org_id()")
