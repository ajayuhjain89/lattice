"""OIDC sign-in, SCIM provisioning, rate limits and database-enforced access audit.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-02
Reversible: no (the downgrade drops the identity tables and organization-owned users)
Locks: ACCESS EXCLUSIVE on users, memberships and audit_events; new tables

Phase 10 (implementation-plan §4 "Phase 10"; ADR-015; security-model §4, §8; threat
model T3, T19, T22-T24).

* ``oidc_connections``: one active OIDC connection per organization (issuer, client ID,
  an ``env:`` client-secret *reference*, the identifying claim, an opaque login key).
  Written only by the operator (``lattice sso``).
* ``oidc_login_attempts``: server-side state of an authorization-code flow (hashes of
  ``state`` and of the browser-binding cookie, the nonce and PKCE verifier), single use,
  short-lived. The API inserts, consumes and deletes expired or consumed attempts.
* ``directory_identities``: the identity provider's person, scoped to the organization
  and its connection: ``(connection, external_id)`` -> membership. Sign-in maps the
  configured claim to ``external_id`` here, never through e-mail or another organization.
* ``scim_tokens``: per-organization SCIM bearer tokens (SHA-256 only). Operator-written.
* ``rate_limit_counters``: one row per (organization, bucket, subject) with the current
  fixed window and its count, updated by an atomic upsert.
* ``users.home_organization_id``: a user created by an organization's provisioning is
  owned by it. E-mail stays unique among global users and becomes unique *per owning
  organization* for owned users, so provisioning never collides with -- and so never
  reveals -- a user of another organization. A trigger keeps an owned user out of any
  other organization's memberships.
* ``lattice_app`` gains exactly: INSERT of owned users and UPDATE of their e-mail and
  display name; INSERT of memberships through column privileges that leave
  ``is_admin``, clearance, account scope and role label at their least-access defaults,
  only for users owned by the organization; ``removed`` only for provisioned members;
  the directory identity, attempt and counter writes above.
* Pre-authentication lookups owned by ``lattice_auth`` (like 0001's): the active
  connection for a login key, and the organization and connection of a SCIM token.
* Access audit enforced by the database (ADR-014 residual risk): ``audit_events.txid``
  records the writing transaction, and deferred constraint triggers refuse to commit an
  access write by ``lattice_app`` unless that transaction recorded an ``access.*``
  event for the same membership. SCIM events (actor ``scim:<token>``) are allowed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TENANT_TABLES = (
    "oidc_connections",
    "oidc_login_attempts",
    "directory_identities",
    "scim_tokens",
    "rate_limit_counters",
)
# Tables whose writes by lattice_app must be audited, and the column naming the member.
AUDITED = (
    ("memberships", "id"),
    ("account_assignments", "membership_id"),
    ("membership_departments", "membership_id"),
    ("access_group_members", "membership_id"),
    ("document_grants", "membership_id"),
    ("directory_identities", "membership_id"),
)
_FUNCTIONS = (
    "lattice_oidc_connection(text)",
    "lattice_resolve_scim_token(bytea)",
)
_HASH = "octet_length({0}) = 32"
_PROVISIONED = (
    "EXISTS (SELECT 1 FROM directory_identities d WHERE d.organization_id = "
    "memberships.organization_id AND d.membership_id = memberships.id)"
)


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (organization_id = lattice_current_org_id()) "
        "WITH CHECK (organization_id = lattice_current_org_id())"
    )


def upgrade() -> None:
    # --- Tables ---------------------------------------------------------------------
    op.execute(
        r"""
        CREATE TABLE oidc_connections (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          login_key text NOT NULL,
          issuer text NOT NULL,
          client_id text NOT NULL,
          client_secret_ref text,
          subject_claim text NOT NULL DEFAULT 'sub',
          status text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_oidc_connections PRIMARY KEY (id),
          CONSTRAINT fk_oidc_connections_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT uq_oidc_connections_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT uq_oidc_connections_login_key UNIQUE (login_key),
          CONSTRAINT ck_oidc_connections_login_key CHECK (login_key ~ '^[A-Za-z0-9_-]{22,64}$'),
          CONSTRAINT ck_oidc_connections_issuer
            CHECK (issuer ~ '^https?://[^\s/?#@]+(/[^\s?#]*)?$' AND length(issuer) <= 500),
          CONSTRAINT ck_oidc_connections_client_id
            CHECK (length(client_id) BETWEEN 1 AND 255 AND client_id !~ '[[:cntrl:]]'),
          CONSTRAINT ck_oidc_connections_client_secret_ref
            CHECK (client_secret_ref ~ '^env:LATTICE_OIDC_SECRET_[A-Z0-9_]{1,64}$'),
          CONSTRAINT ck_oidc_connections_subject_claim
            CHECK (subject_claim ~ '^[A-Za-z_][A-Za-z0-9_]{0,63}$'),
          CONSTRAINT ck_oidc_connections_status CHECK (status IN ('active', 'disabled'))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_oidc_connections_one_active ON oidc_connections "
        "(organization_id) WHERE status = 'active'"
    )
    op.execute(
        "CREATE TRIGGER trg_oidc_connections_updated_at BEFORE UPDATE ON oidc_connections "
        "FOR EACH ROW EXECUTE FUNCTION lattice_set_updated_at()"
    )
    op.execute(
        f"""
        CREATE TABLE oidc_login_attempts (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          connection_id uuid NOT NULL,
          state_hash bytea NOT NULL,
          binding_hash bytea NOT NULL,
          nonce text NOT NULL,
          code_verifier text NOT NULL,
          return_to text NOT NULL DEFAULT '/',
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          consumed_at timestamptz,
          CONSTRAINT pk_oidc_login_attempts PRIMARY KEY (id),
          CONSTRAINT fk_oidc_login_attempts_connection
            FOREIGN KEY (organization_id, connection_id)
            REFERENCES oidc_connections (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT uq_oidc_login_attempts_state_hash UNIQUE (state_hash),
          CONSTRAINT ck_oidc_login_attempts_hashes
            CHECK ({_HASH.format("state_hash")} AND {_HASH.format("binding_hash")}),
          CONSTRAINT ck_oidc_login_attempts_secrets
            CHECK (length(nonce) BETWEEN 22 AND 128 AND length(code_verifier) BETWEEN 43 AND 128),
          CONSTRAINT ck_oidc_login_attempts_return_to
            CHECK (return_to ~ '^/([^/\\\\]|$)' AND length(return_to) <= 512),
          CONSTRAINT ck_oidc_login_attempts_expiry CHECK (expires_at > created_at)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_oidc_login_attempts_expiry ON oidc_login_attempts "
        "(organization_id, expires_at)"
    )
    op.execute(
        r"""
        CREATE TABLE directory_identities (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          connection_id uuid NOT NULL,
          membership_id uuid NOT NULL,
          external_id text NOT NULL,
          user_name text NOT NULL,
          given_name text,
          family_name text,
          version integer NOT NULL DEFAULT 1,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_directory_identities PRIMARY KEY (id),
          CONSTRAINT fk_directory_identities_connection
            FOREIGN KEY (organization_id, connection_id)
            REFERENCES oidc_connections (organization_id, id),
          CONSTRAINT fk_directory_identities_membership
            FOREIGN KEY (organization_id, membership_id)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT uq_directory_identities_connection_id_external_id
            UNIQUE (connection_id, external_id),
          CONSTRAINT uq_directory_identities_organization_id_membership_id
            UNIQUE (organization_id, membership_id),
          CONSTRAINT ck_directory_identities_external_id
            CHECK (length(external_id) BETWEEN 1 AND 255 AND external_id !~ '[[:cntrl:]]'),
          CONSTRAINT ck_directory_identities_user_name
            CHECK (length(user_name) BETWEEN 1 AND 255 AND user_name !~ '[[:cntrl:]]'),
          CONSTRAINT ck_directory_identities_names
            CHECK (coalesce(length(given_name), 0) <= 200
                   AND coalesce(length(family_name), 0) <= 200),
          CONSTRAINT ck_directory_identities_version CHECK (version >= 1)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_directory_identities_user_name ON directory_identities "
        "(connection_id, lower(user_name))"
    )
    op.execute(
        "CREATE TRIGGER trg_directory_identities_updated_at BEFORE UPDATE ON "
        "directory_identities FOR EACH ROW EXECUTE FUNCTION lattice_set_updated_at()"
    )
    op.execute(
        f"""
        CREATE TABLE scim_tokens (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          connection_id uuid NOT NULL,
          token_hash bytea NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          revoked_at timestamptz,
          CONSTRAINT pk_scim_tokens PRIMARY KEY (id),
          CONSTRAINT fk_scim_tokens_connection FOREIGN KEY (organization_id, connection_id)
            REFERENCES oidc_connections (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT uq_scim_tokens_token_hash UNIQUE (token_hash),
          CONSTRAINT ck_scim_tokens_token_hash CHECK ({_HASH.format("token_hash")})
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_limit_counters (
          organization_id uuid NOT NULL,
          bucket text NOT NULL,
          subject uuid NOT NULL,
          window_start timestamptz NOT NULL,
          count integer NOT NULL,
          CONSTRAINT pk_rate_limit_counters PRIMARY KEY (organization_id, bucket, subject),
          CONSTRAINT fk_rate_limit_counters_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT ck_rate_limit_counters_bucket CHECK (bucket ~ '^[a-z_]{1,32}$'),
          CONSTRAINT ck_rate_limit_counters_count CHECK (count >= 0)
        )
        """
    )
    for table in NEW_TENANT_TABLES:
        _tenant_rls(table)

    # --- Organization-owned users ------------------------------------------------------
    op.execute(
        "ALTER TABLE users ADD COLUMN home_organization_id uuid "
        "CONSTRAINT fk_users_home_organization_id_organizations "
        "REFERENCES organizations (id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE users DROP CONSTRAINT uq_users_email")
    op.execute(
        "CREATE UNIQUE INDEX uq_users_email ON users (email) WHERE home_organization_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_users_home_organization_email ON users "
        "(home_organization_id, email) WHERE home_organization_id IS NOT NULL"
    )
    # Owned users are visible to (and writable by) their organization, membership or not.
    op.execute(
        "CREATE POLICY home_visible ON users FOR SELECT TO lattice_app "
        "USING (home_organization_id = lattice_current_org_id())"
    )
    op.execute(
        "CREATE POLICY app_provision_users ON users FOR INSERT TO lattice_app "
        "WITH CHECK (home_organization_id = lattice_current_org_id())"
    )
    op.execute(
        "CREATE POLICY app_provisioned_users_update ON users FOR UPDATE TO lattice_app "
        "USING (home_organization_id = lattice_current_org_id()) "
        "WITH CHECK (home_organization_id = lattice_current_org_id())"
    )
    # An owned user belongs to its organization only, whoever writes the membership.
    op.execute(
        """
        CREATE FUNCTION lattice_membership_home_organization() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM public.users u
            WHERE u.id = NEW.user_id AND u.home_organization_id IS NOT NULL
              AND u.home_organization_id <> NEW.organization_id
          ) THEN
            RAISE EXCEPTION 'user belongs to another organization'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_membership_home_organization() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_memberships_home_organization BEFORE INSERT OR UPDATE OF "
        "user_id, organization_id ON memberships FOR EACH ROW "
        "EXECUTE FUNCTION lattice_membership_home_organization()"
    )

    # --- lattice_app: provisioning, sign-in state, counters ------------------------------
    op.execute("GRANT INSERT ON users TO lattice_app")
    op.execute("GRANT UPDATE (email, display_name, updated_at) ON users TO lattice_app")
    # Column privileges: is_admin, clearance, account_scope and role_label keep their
    # least-access defaults (false, internal, assigned, NULL) on every API insert.
    op.execute("GRANT INSERT (organization_id, user_id, status) ON memberships TO lattice_app")
    op.execute(
        "CREATE POLICY app_member_insert ON memberships AS RESTRICTIVE FOR INSERT "
        "TO lattice_app WITH CHECK (status IN ('active', 'suspended') AND EXISTS "
        "(SELECT 1 FROM users u WHERE u.id = memberships.user_id "
        "AND u.home_organization_id = memberships.organization_id))"
    )
    op.execute("DROP POLICY app_member_status ON memberships")
    op.execute(
        "CREATE POLICY app_member_status ON memberships AS RESTRICTIVE FOR UPDATE "
        "TO lattice_app USING (status <> 'removed') "
        f"WITH CHECK (status IN ('active', 'suspended') OR (status = 'removed' AND {_PROVISIONED}))"
    )
    op.execute("GRANT SELECT, INSERT ON directory_identities TO lattice_app")
    op.execute(
        "GRANT UPDATE (user_name, given_name, family_name, version, updated_at) "
        "ON directory_identities TO lattice_app"
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON oidc_login_attempts TO lattice_app")
    op.execute("GRANT UPDATE (consumed_at) ON oidc_login_attempts TO lattice_app")
    op.execute(
        "CREATE POLICY app_attempts_cleanup ON oidc_login_attempts AS RESTRICTIVE FOR DELETE "
        "TO lattice_app USING (consumed_at IS NOT NULL OR expires_at <= now())"
    )
    op.execute("GRANT SELECT, INSERT ON rate_limit_counters TO lattice_app")
    op.execute("GRANT UPDATE (window_start, count) ON rate_limit_counters TO lattice_app")
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        "TO lattice_app WITH CHECK (action LIKE 'access.%' "
        "AND (actor LIKE 'member:%' OR actor LIKE 'scim:%'))"
    )

    # --- Pre-authentication lookups (lattice_auth) ---------------------------------------
    op.execute("GRANT SELECT ON oidc_connections, scim_tokens TO lattice_auth")
    for table in ("oidc_connections", "scim_tokens"):
        op.execute(f"CREATE POLICY auth_lookup ON {table} FOR SELECT TO lattice_auth USING (true)")
    op.execute(
        """
        CREATE FUNCTION lattice_oidc_connection(p_login_key text)
        RETURNS TABLE (
          connection_id uuid, organization_id uuid, issuer text, client_id text,
          client_secret_ref text, subject_claim text
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          SELECT c.id, c.organization_id, c.issuer, c.client_id, c.client_secret_ref,
                 c.subject_claim
          FROM public.oidc_connections c
          JOIN public.organizations o ON o.id = c.organization_id AND o.status = 'active'
          WHERE c.login_key = p_login_key AND c.status = 'active'
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION lattice_resolve_scim_token(p_token_hash bytea)
        RETURNS TABLE (token_id uuid, organization_id uuid, connection_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          SELECT t.id, t.organization_id, t.connection_id
          FROM public.scim_tokens t
          JOIN public.oidc_connections c
            ON c.organization_id = t.organization_id AND c.id = t.connection_id
           AND c.status = 'active'
          JOIN public.organizations o ON o.id = t.organization_id AND o.status = 'active'
          WHERE octet_length(p_token_hash) = 32 AND t.token_hash = p_token_hash
            AND t.revoked_at IS NULL
        $$
        """
    )
    # As in 0001: set the ACL while lattice_owner owns the functions, then transfer them
    # with CREATE held only inside this transaction.
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO lattice_app")
    op.execute("GRANT CREATE ON SCHEMA public TO lattice_auth")
    for function in _FUNCTIONS:
        op.execute(f"ALTER FUNCTION {function} OWNER TO lattice_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM lattice_auth")

    # --- Access audit enforced by the database (ADR-014) ---------------------------------
    op.execute(
        "ALTER TABLE audit_events ADD COLUMN txid xid8 NOT NULL DEFAULT pg_current_xact_id()"
    )
    op.execute("CREATE INDEX ix_audit_events_txid ON audit_events (txid)")
    # Deferred to commit, so the event may be written after the change. Runs as the
    # writer (SECURITY INVOKER): the API role reads its own organization's events.
    op.execute(
        """
        CREATE FUNCTION lattice_require_access_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          changed jsonb;
          member text;
        BEGIN
          IF current_user <> 'lattice_app' THEN
            RETURN NULL;
          END IF;
          IF TG_OP = 'DELETE' THEN
            changed := to_jsonb(OLD);
          ELSE
            changed := to_jsonb(NEW);
          END IF;
          member := changed ->> TG_ARGV[0];
          IF member IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id()
              AND e.organization_id = (changed ->> 'organization_id')::uuid
              AND e.action LIKE 'access.%'
              AND e.target ->> 'membership_id' = member
          ) THEN
            RAISE EXCEPTION 'access change on % without an audit event', TG_TABLE_NAME
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    for table, column in AUDITED:
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table}_access_audit "
            f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION lattice_require_access_audit('{column}')"
        )


def downgrade() -> None:
    for table, _ in AUDITED:
        op.execute(f"DROP TRIGGER trg_{table}_access_audit ON {table}")
    op.execute("DROP FUNCTION lattice_require_access_audit()")
    op.execute("DROP INDEX ix_audit_events_txid")
    op.execute("ALTER TABLE audit_events DROP COLUMN txid")

    op.execute("DROP FUNCTION lattice_resolve_scim_token(bytea)")
    op.execute("DROP FUNCTION lattice_oidc_connection(text)")

    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        "TO lattice_app WITH CHECK (action LIKE 'access.%' AND actor LIKE 'member:%')"
    )
    op.execute("DROP POLICY app_member_status ON memberships")
    op.execute(
        "CREATE POLICY app_member_status ON memberships AS RESTRICTIVE FOR UPDATE "
        "TO lattice_app USING (status <> 'removed') "
        "WITH CHECK (status IN ('active', 'suspended'))"
    )
    op.execute("DROP POLICY app_member_insert ON memberships")
    op.execute("REVOKE INSERT ON memberships FROM lattice_app")
    op.execute("REVOKE INSERT ON users FROM lattice_app")
    op.execute("REVOKE UPDATE ON users FROM lattice_app")

    op.execute("DROP TRIGGER trg_memberships_home_organization ON memberships")
    op.execute("DROP FUNCTION lattice_membership_home_organization()")
    op.execute("DROP POLICY app_provisioned_users_update ON users")
    op.execute("DROP POLICY app_provision_users ON users")
    op.execute("DROP POLICY home_visible ON users")
    # Owned users go first: global e-mail uniqueness cannot hold for them.
    op.execute("DELETE FROM users WHERE home_organization_id IS NOT NULL")
    op.execute("DROP INDEX uq_users_home_organization_email")
    op.execute("DROP INDEX uq_users_email")
    op.execute("ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email)")
    op.execute("ALTER TABLE users DROP COLUMN home_organization_id")

    op.execute("REVOKE SELECT ON oidc_connections, scim_tokens FROM lattice_auth")
    op.execute(
        "DROP TABLE rate_limit_counters, scim_tokens, directory_identities, "
        "oidc_login_attempts, oidc_connections"
    )
