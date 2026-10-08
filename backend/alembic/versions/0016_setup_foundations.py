"""Setup foundations: encrypted credentials, outbound targets, sign-in facts and the
first fenced setup write (Phase 12, P12.1; ADR-017; phase-12-plan C1, C3, C4, C12).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-08
Reversible: no (the downgrade drops encrypted_secrets and outbound_targets)
Locks: new tables; brief ACCESS EXCLUSIVE on organizations, oidc_connections,
  provider_credentials, scim_tokens and audit_events (constraints, columns, policies);
  the organizations name check scans the table once

* ``encrypted_secrets`` (C3.2): AES-256-GCM ciphertext of recoverable credentials (OIDC
  client secrets, generation-provider keys), referenced as ``vault:<id>`` by the record
  that uses it. Never plaintext, never a key: the master keys stay in the deployment
  environment. ``lattice_app`` may read rows of its organization (the API opens what it
  presents); the worker, ``lattice_auth`` and the web have no privilege on the rows.
  Destroying (rotation, revocation) sets the ciphertext and nonce to NULL.
* ``lattice_secret_key_ids()`` -- the distinct key IDs of live ciphertext across the
  deployment, nothing else: the API refuses to start in production, and
  ``check-deployment`` fails, when one is not configured (C3.3). Owned by
  ``lattice_auth``, which may read ``key_id`` and ``destroyed_at`` only.
* ``outbound_targets`` (C4): private endpoints an operator registered for one
  organization and purpose (exact scheme, host, port); revoked, never deleted. Operator
  only -- no runtime role has any privilege on it in P12.1.
* ``oidc_connections.last_success_at`` / ``last_failure_at`` (C12): the identity setup
  record's facts. ``lattice_app`` may read and set these two columns only.
  ``client_secret_ref`` and ``provider_credentials.secret_ref`` accept ``vault:<uuid>``.
* ``scim_tokens``: ``lattice_app`` may read ``id``, ``organization_id``,
  ``connection_id``, ``created_at`` and ``revoked_at`` -- never ``token_hash`` -- for the
  provisioning setup record.
* **Organization rename** (ADR-017, the first setup fence): ``lattice_app`` may update
  ``organizations.name`` (and ``updated_at``) of its own, active organization only; a
  deferred constraint trigger refuses to commit it unless the same transaction recorded
  an ``organization.updated`` event for that organization; the audit insert policy admits
  ``organization.updated`` from ``member:`` actors (nothing else is widened). A name check
  (1-200 characters, no control characters) holds for every writer.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OIDC_REF_CHECK = (
    r"client_secret_ref ~ '^env:LATTICE_OIDC_SECRET_[A-Z0-9_]{1,64}$' OR "
    r"client_secret_ref ~ '^vault:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
)
_PROVIDER_REF_CHECK = (
    r"secret_ref ~ '^(env|memory):[A-Za-z0-9_.-]{1,96}$' OR "
    r"secret_ref ~ '^vault:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
)
_AUDIT_INSERT = (
    "(action LIKE 'access.%' AND (actor LIKE 'member:%' OR actor LIKE 'scim:%')) "
    "OR (action = 'organization.updated' AND actor LIKE 'member:%')"
)
_KEY_IDS = "lattice_secret_key_ids()"


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (organization_id = lattice_current_org_id()) "
        "WITH CHECK (organization_id = lattice_current_org_id())"
    )


def upgrade() -> None:
    # --- encrypted_secrets ---------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE encrypted_secrets (
          id uuid NOT NULL,
          organization_id uuid NOT NULL,
          owner_kind text NOT NULL,
          owner_id uuid NOT NULL,
          key_id text NOT NULL,
          nonce bytea,
          ciphertext bytea,
          hint text,
          created_at timestamptz NOT NULL DEFAULT now(),
          destroyed_at timestamptz,
          CONSTRAINT pk_encrypted_secrets PRIMARY KEY (id),
          CONSTRAINT fk_encrypted_secrets_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT uq_encrypted_secrets_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT ck_encrypted_secrets_owner_kind
            CHECK (owner_kind IN ('oidc_client_secret', 'provider_api_key')),
          CONSTRAINT ck_encrypted_secrets_key_id CHECK (key_id ~ '^[a-z0-9][a-z0-9_-]{0,31}$'),
          CONSTRAINT ck_encrypted_secrets_material CHECK (
            (destroyed_at IS NULL AND octet_length(nonce) = 12
             AND octet_length(ciphertext) BETWEEN 17 AND 4112)
            OR (destroyed_at IS NOT NULL AND nonce IS NULL AND ciphertext IS NULL)),
          CONSTRAINT ck_encrypted_secrets_hint CHECK (
            hint IS NULL OR (owner_kind = 'provider_api_key' AND length(hint) = 4
                             AND destroyed_at IS NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_encrypted_secrets_live_owner ON encrypted_secrets "
        "(organization_id, owner_kind, owner_id) WHERE destroyed_at IS NULL"
    )
    _tenant_rls("encrypted_secrets")
    op.execute("GRANT SELECT ON encrypted_secrets TO lattice_app")

    # Key IDs of live ciphertext, across organizations (no material, no owner).
    op.execute("GRANT SELECT (key_id, destroyed_at) ON encrypted_secrets TO lattice_auth")
    op.execute(
        "CREATE POLICY auth_key_ids ON encrypted_secrets FOR SELECT TO lattice_auth USING (true)"
    )
    op.execute(
        """
        CREATE FUNCTION lattice_secret_key_ids()
        RETURNS TABLE (key_id text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          SELECT DISTINCT s.key_id FROM public.encrypted_secrets s
          WHERE s.destroyed_at IS NULL ORDER BY 1
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_KEY_IDS} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_KEY_IDS} TO lattice_app")
    op.execute("GRANT CREATE ON SCHEMA public TO lattice_auth")
    op.execute(f"ALTER FUNCTION {_KEY_IDS} OWNER TO lattice_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM lattice_auth")
    # A grant to the schema owner made before the transfer would be rewritten to the
    # new owner: grant it as lattice_auth (the owner holds SET, not INHERIT, on it).
    op.execute("SET LOCAL ROLE lattice_auth")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_KEY_IDS} TO lattice_owner")
    op.execute("RESET ROLE")

    # --- outbound_targets (operator only) ------------------------------------------------
    op.execute(
        """
        CREATE TABLE outbound_targets (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          purpose text NOT NULL,
          scheme text NOT NULL,
          host text NOT NULL,
          port integer NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          revoked_at timestamptz,
          CONSTRAINT pk_outbound_targets PRIMARY KEY (id),
          CONSTRAINT fk_outbound_targets_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT uq_outbound_targets_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT ck_outbound_targets_purpose CHECK (purpose IN ('oidc', 'generation')),
          CONSTRAINT ck_outbound_targets_scheme CHECK (scheme IN ('https', 'http')),
          CONSTRAINT ck_outbound_targets_host CHECK (host ~ '^[a-z0-9.:-]{1,253}$'),
          CONSTRAINT ck_outbound_targets_port CHECK (port BETWEEN 1 AND 65535),
          CONSTRAINT ck_outbound_targets_revoked
            CHECK (revoked_at IS NULL OR revoked_at >= created_at)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_outbound_targets_live ON outbound_targets "
        "(organization_id, purpose, scheme, host, port) WHERE revoked_at IS NULL"
    )
    _tenant_rls("outbound_targets")

    # --- OIDC: vault references and sign-in facts ----------------------------------------
    op.execute(
        "ALTER TABLE oidc_connections ADD COLUMN last_success_at timestamptz, "
        "ADD COLUMN last_failure_at timestamptz"
    )
    op.execute("ALTER TABLE oidc_connections DROP CONSTRAINT ck_oidc_connections_client_secret_ref")
    op.execute(
        "ALTER TABLE oidc_connections ADD CONSTRAINT ck_oidc_connections_client_secret_ref "
        f"CHECK ({_OIDC_REF_CHECK})"
    )
    op.execute(
        "GRANT SELECT (last_success_at, last_failure_at), "
        "UPDATE (last_success_at, last_failure_at) ON oidc_connections TO lattice_app"
    )
    op.execute(
        "ALTER TABLE provider_credentials DROP CONSTRAINT ck_provider_credentials_secret_ref"
    )
    op.execute(
        "ALTER TABLE provider_credentials ADD CONSTRAINT ck_provider_credentials_secret_ref "
        f"CHECK ({_PROVIDER_REF_CHECK})"
    )
    op.execute(
        "GRANT SELECT (id, organization_id, connection_id, created_at, revoked_at) "
        "ON scim_tokens TO lattice_app"
    )

    # --- Organization rename: the first ADR-017 fence ------------------------------------
    op.execute(
        "ALTER TABLE organizations ADD CONSTRAINT ck_organizations_name "
        "CHECK (length(btrim(name)) BETWEEN 1 AND 200 AND name !~ '[[:cntrl:]]')"
    )
    op.execute("GRANT UPDATE (name, updated_at) ON organizations TO lattice_app")
    op.execute(
        "CREATE POLICY app_rename_active ON organizations AS RESTRICTIVE FOR UPDATE "
        "TO lattice_app USING (status = 'active') WITH CHECK (status = 'active')"
    )
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        f"TO lattice_app WITH CHECK ({_AUDIT_INSERT})"
    )
    # Deferred to commit, so the event may be written after the change; SECURITY INVOKER
    # (the API role reads its own organization's events), as 0013's access trigger.
    op.execute(
        """
        CREATE FUNCTION lattice_require_setup_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          changed jsonb := to_jsonb(NEW);
          subject text := changed ->> TG_ARGV[2];
          org text := changed ->> TG_ARGV[3];
        BEGIN
          IF current_user <> 'lattice_app' THEN
            RETURN NULL;
          END IF;
          IF subject IS NULL OR org IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id()
              AND e.organization_id = org::uuid
              AND e.action = TG_ARGV[0]
              AND e.target ->> TG_ARGV[1] = subject
          ) THEN
            RAISE EXCEPTION 'setup change on % without an audit event', TG_TABLE_NAME
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_require_setup_audit() FROM PUBLIC")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_organizations_setup_audit AFTER UPDATE ON organizations "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        "lattice_require_setup_audit('organization.updated', 'organization_id', 'id', 'id')"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_organizations_setup_audit ON organizations")
    op.execute("DROP FUNCTION lattice_require_setup_audit()")
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        "TO lattice_app WITH CHECK (action LIKE 'access.%' "
        "AND (actor LIKE 'member:%' OR actor LIKE 'scim:%'))"
    )
    op.execute("DROP POLICY app_rename_active ON organizations")
    op.execute("REVOKE UPDATE (name, updated_at) ON organizations FROM lattice_app")
    op.execute("ALTER TABLE organizations DROP CONSTRAINT ck_organizations_name")

    op.execute(
        "REVOKE SELECT (id, organization_id, connection_id, created_at, revoked_at) "
        "ON scim_tokens FROM lattice_app"
    )
    op.execute(
        "ALTER TABLE provider_credentials DROP CONSTRAINT ck_provider_credentials_secret_ref"
    )
    op.execute(
        "ALTER TABLE provider_credentials ADD CONSTRAINT ck_provider_credentials_secret_ref "
        "CHECK (secret_ref ~ '^(env|memory):[A-Za-z0-9_.-]{1,96}$') NOT VALID"
    )
    op.execute(
        "REVOKE SELECT (last_success_at, last_failure_at), "
        "UPDATE (last_success_at, last_failure_at) ON oidc_connections FROM lattice_app"
    )
    op.execute("ALTER TABLE oidc_connections DROP CONSTRAINT ck_oidc_connections_client_secret_ref")
    op.execute(
        "ALTER TABLE oidc_connections ADD CONSTRAINT ck_oidc_connections_client_secret_ref "
        r"CHECK (client_secret_ref ~ '^env:LATTICE_OIDC_SECRET_[A-Z0-9_]{1,64}$') NOT VALID"
    )
    op.execute(
        "ALTER TABLE oidc_connections DROP COLUMN last_success_at, DROP COLUMN last_failure_at"
    )

    op.execute("DROP TABLE outbound_targets")
    op.execute(f"DROP FUNCTION {_KEY_IDS}")
    op.execute("REVOKE SELECT (key_id, destroyed_at) ON encrypted_secrets FROM lattice_auth")
    op.execute("DROP TABLE encrypted_secrets")
