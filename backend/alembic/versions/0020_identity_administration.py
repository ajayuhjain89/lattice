"""Identity administration in the product: the OIDC connection and SCIM tokens (Phase 12,
P12.4; ADR-017; phase-12-plan C3, C4, C5, C11, C12).

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-13
Reversible: no (the downgrade drops test attempts, SCIM token hints and refusal events)
Locks: brief ACCESS EXCLUSIVE on oidc_connections, oidc_login_attempts, scim_tokens and
  audit_events (columns with no default, checks, policies, triggers); no table scan

No broad write is granted: ``lattice_app`` writes connections and tokens only through
fenced functions (``SECURITY DEFINER``, owned by ``lattice_owner``, search path pinned,
``EXECUTE`` for ``lattice_app`` only), each re-checking that the actor is an active
administrator of the bound organization and refusing with SQLSTATE ``LA001`` and a fixed
reason:

* ``lattice_oidc_create(actor, login_key, issuer, client_id, subject_claim)`` -- when the
  organization has no active connection, and no disabled one still binds members (its
  re-enable is the path: a new connection would strand them, D-37B).
* ``lattice_oidc_set_client(actor, connection, expected, client_id)`` -- the client ID.
* ``lattice_oidc_set_secret(actor, connection, expected, secret_id, key_id, nonce,
  ciphertext)`` / ``lattice_oidc_clear_secret(actor, connection, expected)`` -- the client
  secret as AES-256-GCM ciphertext sealed by the API (which holds the keys, C3.4) for this
  connection; the previous ``vault:`` ciphertext is destroyed in the same statement
  sequence; a public client (PKCE only) has none. Sessions are kept.
* ``lattice_oidc_set_status(actor, connection, expected, active)`` -- disable revokes the
  connection's live sessions under its advisory lock (D-25, the lock sign-in takes
  shared); enable refuses while another connection is active.
* ``lattice_oidc_change_identity(actor, connection, expected, issuer, subject_claim,
  proof)`` -- an issuer or identifying-claim change, only with an unused, unexpired test
  sign-in by the same administrator, under exactly these values, whose identifier equals
  their own directory identity on this connection (``proof``): the database re-checks the
  match. Revokes the connection's sessions. A provider whose subjects differ is not
  migrated (post-v1, D-37B).
* ``lattice_create_scim_token(actor, token_hash, hint)``, ``lattice_rotate_scim_token(actor,
  token, token_hash, hint)``, ``lattice_revoke_scim_token(actor, token)`` -- SHA-256 hashes
  only (verification-only secrets never become decryptable); rotation creates the
  replacement and revokes the old token in one transaction.
* ``lattice_scim_token_used(token)`` -- ``last_used_at`` at minute precision, written by the
  SCIM path (no actor: a fact, like the sign-in facts).

Audit coupling (deferred constraint triggers, ``lattice_app`` only): a connection write
needs an ``sso.connection_*`` event for that connection in the same transaction; a token
creation needs ``scim.token_created`` or ``scim.token_rotated`` naming it, a revocation
``scim.token_revoked`` or ``scim.token_rotated`` naming it as replaced. The audit insert
policy admits the ``sso.connection_*`` and ``scim.token_*`` families from ``member:`` actors
and ``scim.request_refused`` from ``scim:`` actors (the provisioning path, rate-limited in
code).

Columns: ``oidc_connections.last_failure_code`` (why the last sign-in failed:
``provider_unavailable`` / ``invalid_configuration`` / ``outbound_blocked`` /
``credentials_rejected``) and the last
administrator test (``last_test_at``, ``last_test_kind``, ``last_test_result``), written by
the API as facts (column grants, not audited -- the test's event is); the API may read
``client_id``, ``login_key``, ``client_secret_ref`` (shown as a kind, never the reference),
``created_at`` and ``updated_at``. ``oidc_login_attempts.purpose`` (``sign_in`` | ``test``)
with the testing administrator, proposed issuer and claim, outcome and the administrator's
own observed identifier; ``scim_tokens.last_used_at`` and ``hint`` (the token's last four
characters). ``lattice_app`` may read ``outbound_targets`` (its organization's), to apply
the operator's registrations to sign-in and tests (C4).
"""

# The function bodies are composed of this module's fixed SQL fragments; no input reaches
# them (S608 does not apply).
# ruff: noqa: S608
from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One advisory lock per OIDC connection (lattice.identity.sessions): b"LATT".
CONNECTION_LOCK = "pg_advisory_xact_lock(1279349844, hashtext({0}::text))"
_UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_TEST_OUTCOMES = (
    "'matched', 'mismatched', 'not_provisioned', 'sign_in_failed', 'token_invalid', "
    "'provider_unavailable', 'invalid_configuration', 'outbound_blocked', "
    "'credentials_rejected'"
)
_CHECK_RESULTS = (
    "'valid', 'invalid_configuration', 'credentials_rejected', 'provider_unavailable', "
    "'outbound_blocked', 'matched', 'mismatched', 'not_provisioned', 'sign_in_failed', "
    "'token_invalid'"
)
_SSO_ACTIONS = (
    "'sso.connection_created', 'sso.connection_updated', 'sso.connection_disabled', "
    "'sso.connection_enabled', 'sso.connection_tested'"
)
_SCIM_ACTIONS = "'scim.token_created', 'scim.token_rotated', 'scim.token_revoked'"
_AUDIT_INSERT = (
    "(action LIKE 'access.%' AND (actor LIKE 'member:%' OR actor LIKE 'scim:%')) "
    "OR (action IN ('organization.updated', 'account.created', 'account.updated', "
    "'account.archived', 'account.reactivated', 'account.mapping_changed', "
    f"{_SSO_ACTIONS}, {_SCIM_ACTIONS}) AND actor LIKE 'member:%') "
    "OR (action = 'scim.request_refused' AND actor LIKE 'scim:%')"
)
_AUDIT_INSERT_0018 = (
    "(action LIKE 'access.%' AND (actor LIKE 'member:%' OR actor LIKE 'scim:%')) "
    "OR (action IN ('organization.updated', 'account.created', 'account.updated', "
    "'account.archived', 'account.reactivated', 'account.mapping_changed') "
    "AND actor LIKE 'member:%')"
)
_FUNCTIONS = (
    "lattice_oidc_create(uuid, text, text, text, text)",
    "lattice_oidc_set_client(uuid, uuid, timestamptz, text)",
    "lattice_oidc_set_secret(uuid, uuid, timestamptz, uuid, text, bytea, bytea)",
    "lattice_oidc_clear_secret(uuid, uuid, timestamptz)",
    "lattice_oidc_set_status(uuid, uuid, timestamptz, boolean)",
    "lattice_oidc_change_identity(uuid, uuid, timestamptz, text, text, uuid)",
    "lattice_create_scim_token(uuid, bytea, text)",
    "lattice_rotate_scim_token(uuid, uuid, bytea, text)",
    "lattice_revoke_scim_token(uuid, uuid)",
    "lattice_scim_token_used(uuid)",
)

_REQUIRE_ORG = """
  IF v_org IS NULL THEN
    RAISE EXCEPTION 'no organization is bound' USING ERRCODE = 'insufficient_privilege';
  END IF;
"""
# Refuse unless p_actor is an active administrator of v_org (as 0017, 0018).
_REQUIRE_ACTOR = """
  IF NOT EXISTS (
    SELECT 1 FROM public.memberships m JOIN public.users u ON u.id = m.user_id
    WHERE m.organization_id = v_org AND m.id = p_actor AND m.is_admin
      AND m.status = 'active' AND u.status = 'active'
  ) THEN
    RAISE EXCEPTION 'not_administrator' USING ERRCODE = 'LA001';
  END IF;
"""
# The connection, locked, at the version the administrator reviewed.
_LOCK_CONNECTION = """
  SELECT * INTO v_conn FROM public.oidc_connections c
  WHERE c.organization_id = v_org AND c.id = p_connection FOR NO KEY UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'connection_not_found' USING ERRCODE = 'LA001';
  END IF;
  IF p_expected IS NULL OR v_conn.updated_at <> p_expected THEN
    RAISE EXCEPTION 'connection_changed' USING ERRCODE = 'LA001';
  END IF;
"""
_HEADER = """
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


def _fn(signature: str, returns: str, declare: str, body: str) -> str:
    return (
        f"CREATE FUNCTION {signature} RETURNS {returns} {_HEADER} AS $$\n"
        f"DECLARE\n  v_org uuid := public.lattice_current_org_id();\n{declare}\n"
        f"BEGIN\n{_REQUIRE_ORG}\n{body}\nEND\n$$"
    )


# Sign-in holds the connection's advisory lock shared while it checks the connection and
# creates a session (whose foreign key takes a KEY SHARE row lock): take the advisory
# lock first and the row lock after it, never the other way round.
_CONNECTION_ADVISORY = "  PERFORM " + CONNECTION_LOCK.format("p_connection") + ";"
_REVOKE_SESSIONS = """
  UPDATE public.sessions SET revoked_at = now()
  WHERE organization_id = v_org AND oidc_connection_id = p_connection AND revoked_at IS NULL;
  GET DIAGNOSTICS v_revoked = ROW_COUNT;
"""


def upgrade() -> None:
    # --- columns ---------------------------------------------------------------------------
    op.execute(
        f"""
        ALTER TABLE oidc_connections
          ADD COLUMN last_failure_code text,
          ADD COLUMN last_test_at timestamptz,
          ADD COLUMN last_test_kind text,
          ADD COLUMN last_test_result text,
          ADD CONSTRAINT ck_oidc_connections_last_failure_code CHECK (
            last_failure_code IN ('provider_unavailable', 'invalid_configuration',
                                  'outbound_blocked', 'credentials_rejected')),
          ADD CONSTRAINT ck_oidc_connections_last_test CHECK (
            (last_test_at IS NULL AND last_test_kind IS NULL AND last_test_result IS NULL)
            OR (last_test_at IS NOT NULL AND last_test_kind IN ('check', 'sign_in')
                AND last_test_result IN ({_CHECK_RESULTS})))
        """
    )
    op.execute(
        f"""
        ALTER TABLE oidc_login_attempts
          ADD COLUMN purpose text NOT NULL DEFAULT 'sign_in',
          ADD COLUMN membership_id uuid,
          ADD COLUMN proposed_issuer text,
          ADD COLUMN proposed_subject_claim text,
          ADD COLUMN outcome text,
          ADD COLUMN observed_identifier text,
          ADD COLUMN completed_at timestamptz,
          ADD COLUMN proof_used_at timestamptz,
          ADD CONSTRAINT fk_oidc_login_attempts_membership
            FOREIGN KEY (organization_id, membership_id)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE,
          ADD CONSTRAINT ck_oidc_login_attempts_purpose CHECK (
            (purpose = 'sign_in' AND membership_id IS NULL AND proposed_issuer IS NULL
             AND proposed_subject_claim IS NULL AND outcome IS NULL
             AND observed_identifier IS NULL AND completed_at IS NULL
             AND proof_used_at IS NULL)
            OR (purpose = 'test' AND membership_id IS NOT NULL)),
          ADD CONSTRAINT ck_oidc_login_attempts_proposed CHECK (
            (proposed_issuer IS NULL
             OR (proposed_issuer ~ '^https?://[^\\s/?#@]+(/[^\\s?#]*)?$'
                 AND length(proposed_issuer) <= 500))
            AND (proposed_subject_claim IS NULL
                 OR proposed_subject_claim ~ '^[A-Za-z_][A-Za-z0-9_]{{0,63}}$')),
          ADD CONSTRAINT ck_oidc_login_attempts_outcome CHECK (
            (outcome IS NULL AND completed_at IS NULL AND observed_identifier IS NULL)
            OR (outcome IN ({_TEST_OUTCOMES}) AND completed_at IS NOT NULL
                AND (observed_identifier IS NULL
                     OR length(observed_identifier) BETWEEN 1 AND 255)))
        """
    )
    op.execute(
        """
        ALTER TABLE scim_tokens
          ADD COLUMN last_used_at timestamptz,
          ADD COLUMN hint text,
          ADD CONSTRAINT ck_scim_tokens_last_used_at
            CHECK (last_used_at = date_trunc('minute', last_used_at)),
          ADD CONSTRAINT ck_scim_tokens_hint CHECK (hint ~ '^[A-Za-z0-9_-]{4}$')
        """
    )

    # --- reads and facts for the API role ---------------------------------------------------
    op.execute(
        "GRANT SELECT (client_id, login_key, client_secret_ref, created_at, updated_at, "
        "last_failure_code, last_test_at, last_test_kind, last_test_result) "
        "ON oidc_connections TO lattice_app"
    )
    op.execute(
        "GRANT UPDATE (last_failure_code, last_test_at, last_test_kind, last_test_result) "
        "ON oidc_connections TO lattice_app"
    )
    op.execute(
        "GRANT UPDATE (outcome, observed_identifier, completed_at) "
        "ON oidc_login_attempts TO lattice_app"
    )
    # A test sign-in's result stays readable (and usable as proof) until it expires.
    op.execute("DROP POLICY app_attempts_cleanup ON oidc_login_attempts")
    op.execute(
        "CREATE POLICY app_attempts_cleanup ON oidc_login_attempts AS RESTRICTIVE FOR DELETE "
        "TO lattice_app USING ((purpose = 'sign_in' AND consumed_at IS NOT NULL) "
        "OR expires_at <= now())"
    )
    op.execute("GRANT SELECT (last_used_at, hint) ON scim_tokens TO lattice_app")
    op.execute("GRANT SELECT ON outbound_targets TO lattice_app")

    # --- OIDC connection fences ---------------------------------------------------------------
    op.execute(
        _fn(
            "lattice_oidc_create(p_actor uuid, p_login_key text, p_issuer text, "
            "p_client_id text, p_subject_claim text)",
            "uuid",
            "  v_id uuid;",
            f"""
  {_REQUIRE_ACTOR}
  -- One change of the organization's connections at a time.
  PERFORM pg_advisory_xact_lock(1279349844, hashtext(v_org::text));
  IF EXISTS (SELECT 1 FROM public.oidc_connections c
             WHERE c.organization_id = v_org AND c.status = 'active') THEN
    RAISE EXCEPTION 'connection_exists' USING ERRCODE = 'LA001';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.directory_identities d
    JOIN public.oidc_connections c
      ON c.organization_id = d.organization_id AND c.id = d.connection_id
     AND c.status = 'disabled'
    JOIN public.memberships m
      ON m.organization_id = d.organization_id AND m.id = d.membership_id
     AND m.status <> 'removed'
    WHERE d.organization_id = v_org
  ) THEN
    RAISE EXCEPTION 'identity_namespace_in_use' USING ERRCODE = 'LA001';
  END IF;
  INSERT INTO public.oidc_connections (
    organization_id, login_key, issuer, client_id, client_secret_ref, subject_claim
  ) VALUES (v_org, p_login_key, p_issuer, p_client_id, NULL, p_subject_claim)
  RETURNING id INTO v_id;
  RETURN v_id;""",
        )
    )
    op.execute(
        _fn(
            "lattice_oidc_set_client(p_actor uuid, p_connection uuid, p_expected timestamptz, "
            "p_client_id text)",
            "void",
            "  v_conn public.oidc_connections%ROWTYPE;",
            f"""
  {_REQUIRE_ACTOR}
  {_LOCK_CONNECTION}
  IF p_client_id IS NOT DISTINCT FROM v_conn.client_id THEN
    RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
  END IF;
  UPDATE public.oidc_connections SET client_id = p_client_id
  WHERE organization_id = v_org AND id = p_connection;""",
        )
    )
    op.execute(
        _fn(
            "lattice_oidc_set_secret(p_actor uuid, p_connection uuid, p_expected timestamptz, "
            "p_secret uuid, p_key_id text, p_nonce bytea, p_ciphertext bytea)",
            "text",
            "  v_conn public.oidc_connections%ROWTYPE;",
            f"""
  {_REQUIRE_ACTOR}
  {_LOCK_CONNECTION}
  IF p_secret IS NULL OR p_key_id IS NULL OR p_nonce IS NULL OR p_ciphertext IS NULL THEN
    RAISE EXCEPTION 'secret_invalid' USING ERRCODE = 'LA001';
  END IF;
  -- The previous ciphertext (rotation): destroyed, not kept beside the new one.
  UPDATE public.encrypted_secrets SET nonce = NULL, ciphertext = NULL, hint = NULL,
         destroyed_at = now()
  WHERE organization_id = v_org AND owner_kind = 'oidc_client_secret'
    AND owner_id = p_connection AND destroyed_at IS NULL;
  INSERT INTO public.encrypted_secrets (
    id, organization_id, owner_kind, owner_id, key_id, nonce, ciphertext, hint
  ) VALUES (p_secret, v_org, 'oidc_client_secret', p_connection, p_key_id, p_nonce,
            p_ciphertext, NULL);
  UPDATE public.oidc_connections SET client_secret_ref = 'vault:' || p_secret::text
  WHERE organization_id = v_org AND id = p_connection;
  RETURN CASE WHEN v_conn.client_secret_ref IS NULL THEN 'set' ELSE 'replaced' END;""",
        )
    )
    op.execute(
        _fn(
            "lattice_oidc_clear_secret(p_actor uuid, p_connection uuid, p_expected timestamptz)",
            "void",
            "  v_conn public.oidc_connections%ROWTYPE;",
            f"""
  {_REQUIRE_ACTOR}
  {_LOCK_CONNECTION}
  IF v_conn.client_secret_ref IS NULL THEN
    RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
  END IF;
  UPDATE public.encrypted_secrets SET nonce = NULL, ciphertext = NULL, hint = NULL,
         destroyed_at = now()
  WHERE organization_id = v_org AND owner_kind = 'oidc_client_secret'
    AND owner_id = p_connection AND destroyed_at IS NULL;
  UPDATE public.oidc_connections SET client_secret_ref = NULL
  WHERE organization_id = v_org AND id = p_connection;""",
        )
    )
    op.execute(
        _fn(
            "lattice_oidc_set_status(p_actor uuid, p_connection uuid, p_expected timestamptz, "
            "p_active boolean)",
            "integer",
            "  v_conn public.oidc_connections%ROWTYPE;\n  v_revoked integer := 0;",
            f"""
  {_REQUIRE_ACTOR}
  PERFORM pg_advisory_xact_lock(1279349844, hashtext(v_org::text));
{_CONNECTION_ADVISORY}
  {_LOCK_CONNECTION}
  IF p_active IS NULL OR (p_active AND v_conn.status = 'active')
     OR (NOT p_active AND v_conn.status = 'disabled') THEN
    RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
  END IF;
  IF p_active THEN
    IF EXISTS (SELECT 1 FROM public.oidc_connections c
               WHERE c.organization_id = v_org AND c.status = 'active') THEN
      RAISE EXCEPTION 'connection_exists' USING ERRCODE = 'LA001';
    END IF;
    UPDATE public.oidc_connections SET status = 'active'
    WHERE organization_id = v_org AND id = p_connection;
    RETURN 0;
  END IF;
  -- Sign-in holds the advisory lock shared while it creates a session: none survives.
  {_REVOKE_SESSIONS}
  UPDATE public.oidc_connections SET status = 'disabled'
  WHERE organization_id = v_org AND id = p_connection;
  RETURN v_revoked;""",
        )
    )
    op.execute(
        _fn(
            "lattice_oidc_change_identity(p_actor uuid, p_connection uuid, "
            "p_expected timestamptz, p_issuer text, p_subject_claim text, p_proof uuid)",
            "integer",
            "  v_conn public.oidc_connections%ROWTYPE;\n  v_proof record;\n"
            "  v_revoked integer := 0;",
            f"""
  {_REQUIRE_ACTOR}
{_CONNECTION_ADVISORY}
  {_LOCK_CONNECTION}
  IF v_conn.status <> 'active' THEN
    RAISE EXCEPTION 'connection_disabled' USING ERRCODE = 'LA001';
  END IF;
  IF p_issuer IS NULL OR p_subject_claim IS NULL
     OR (p_issuer = v_conn.issuer AND p_subject_claim = v_conn.subject_claim) THEN
    RAISE EXCEPTION 'no_change' USING ERRCODE = 'LA001';
  END IF;
  -- The administrator's own sign-in under exactly these values matched their identity:
  -- the subjects are unchanged for them. Anything else would be a changed-subject
  -- migration, which is not supported (D-37B).
  SELECT a.* INTO v_proof FROM public.oidc_login_attempts a
  WHERE a.organization_id = v_org AND a.id = p_proof AND a.connection_id = p_connection
    AND a.purpose = 'test' AND a.membership_id = p_actor
  FOR UPDATE;
  IF NOT FOUND OR v_proof.outcome IS DISTINCT FROM 'matched'
     OR v_proof.proof_used_at IS NOT NULL OR v_proof.expires_at <= now()
     OR coalesce(v_proof.proposed_issuer, v_conn.issuer) <> p_issuer
     OR coalesce(v_proof.proposed_subject_claim, v_conn.subject_claim) <> p_subject_claim
     OR NOT EXISTS (
       SELECT 1 FROM public.directory_identities d
       WHERE d.organization_id = v_org AND d.connection_id = p_connection
         AND d.membership_id = p_actor AND d.external_id = v_proof.observed_identifier)
  THEN
    RAISE EXCEPTION 'identity_unverified' USING ERRCODE = 'LA001';
  END IF;
  UPDATE public.oidc_login_attempts SET proof_used_at = now()
  WHERE organization_id = v_org AND id = p_proof;
  {_REVOKE_SESSIONS}
  -- The proof was a successful sign-in under the new values: the connection is verified.
  UPDATE public.oidc_connections SET issuer = p_issuer, subject_claim = p_subject_claim,
         last_success_at = v_proof.completed_at, last_failure_at = NULL,
         last_failure_code = NULL, last_test_at = v_proof.completed_at,
         last_test_kind = 'sign_in', last_test_result = 'matched'
  WHERE organization_id = v_org AND id = p_connection;
  RETURN v_revoked;""",
        )
    )

    # --- SCIM token fences --------------------------------------------------------------------
    op.execute(
        _fn(
            "lattice_create_scim_token(p_actor uuid, p_token_hash bytea, p_hint text)",
            "uuid",
            "  v_connection uuid;\n  v_id uuid;",
            f"""
  {_REQUIRE_ACTOR}
  SELECT c.id INTO v_connection FROM public.oidc_connections c
  WHERE c.organization_id = v_org AND c.status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no_active_connection' USING ERRCODE = 'LA001';
  END IF;
  INSERT INTO public.scim_tokens (organization_id, connection_id, token_hash, hint)
  VALUES (v_org, v_connection, p_token_hash, p_hint) RETURNING id INTO v_id;
  RETURN v_id;""",
        )
    )
    op.execute(
        _fn(
            "lattice_rotate_scim_token(p_actor uuid, p_token uuid, p_token_hash bytea, "
            "p_hint text)",
            "uuid",
            "  v_connection uuid;\n  v_id uuid;",
            f"""
  {_REQUIRE_ACTOR}
  PERFORM 1 FROM public.scim_tokens t
  WHERE t.organization_id = v_org AND t.id = p_token AND t.revoked_at IS NULL FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'token_not_live' USING ERRCODE = 'LA001';
  END IF;
  SELECT c.id INTO v_connection FROM public.oidc_connections c
  WHERE c.organization_id = v_org AND c.status = 'active';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no_active_connection' USING ERRCODE = 'LA001';
  END IF;
  INSERT INTO public.scim_tokens (organization_id, connection_id, token_hash, hint)
  VALUES (v_org, v_connection, p_token_hash, p_hint) RETURNING id INTO v_id;
  UPDATE public.scim_tokens SET revoked_at = now()
  WHERE organization_id = v_org AND id = p_token;
  RETURN v_id;""",
        )
    )
    op.execute(
        _fn(
            "lattice_revoke_scim_token(p_actor uuid, p_token uuid)",
            "void",
            "",
            f"""
  {_REQUIRE_ACTOR}
  UPDATE public.scim_tokens SET revoked_at = now()
  WHERE organization_id = v_org AND id = p_token AND revoked_at IS NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'token_not_live' USING ERRCODE = 'LA001';
  END IF;""",
        )
    )
    op.execute(
        _fn(
            "lattice_scim_token_used(p_token uuid)",
            "void",
            "",
            """
  UPDATE public.scim_tokens SET last_used_at = date_trunc('minute', now())
  WHERE organization_id = v_org AND id = p_token AND revoked_at IS NULL
    AND last_used_at IS DISTINCT FROM date_trunc('minute', now());""",
        )
    )
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO lattice_app")

    # --- audit coupling -------------------------------------------------------------------------
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        f"TO lattice_app WITH CHECK ({_AUDIT_INSERT})"
    )
    op.execute(
        f"""
        CREATE FUNCTION lattice_require_connection_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
          IF current_user <> 'lattice_app' THEN
            RETURN NULL;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id() AND e.organization_id = NEW.organization_id
              AND e.action IN ({_SSO_ACTIONS}) AND e.action <> 'sso.connection_tested'
              AND e.target ->> 'connection_id' = NEW.id::text
          ) THEN
            RAISE EXCEPTION 'connection change without an audit event'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_require_connection_audit() FROM PUBLIC")
    # The setup columns only: sign-in and test facts are not configuration changes.
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_oidc_connections_setup_audit AFTER INSERT OR UPDATE OF "
        "issuer, client_id, client_secret_ref, subject_claim, status ON oidc_connections "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_connection_audit()"
    )
    op.execute(
        """
        CREATE FUNCTION lattice_require_scim_token_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
          IF current_user <> 'lattice_app' THEN
            RETURN NULL;
          END IF;
          IF TG_OP = 'INSERT' AND NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id() AND e.organization_id = NEW.organization_id
              AND e.action IN ('scim.token_created', 'scim.token_rotated')
              AND e.target ->> 'token_id' = NEW.id::text
          ) THEN
            RAISE EXCEPTION 'SCIM token created without an audit event'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF TG_OP = 'UPDATE' AND NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id() AND e.organization_id = NEW.organization_id
              AND ((e.action = 'scim.token_revoked'
                    AND e.target ->> 'token_id' = NEW.id::text)
                OR (e.action = 'scim.token_rotated'
                    AND e.target ->> 'replaced_token_id' = NEW.id::text))
          ) THEN
            RAISE EXCEPTION 'SCIM token revoked without an audit event'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_require_scim_token_audit() FROM PUBLIC")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_scim_tokens_setup_audit AFTER INSERT OR UPDATE OF "
        "revoked_at ON scim_tokens DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_scim_token_audit()"
    )


def downgrade() -> None:
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("DROP TRIGGER trg_scim_tokens_setup_audit ON scim_tokens")
    op.execute("DROP FUNCTION lattice_require_scim_token_audit()")
    op.execute("DROP TRIGGER trg_oidc_connections_setup_audit ON oidc_connections")
    op.execute("DROP FUNCTION lattice_require_connection_audit()")
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        f"TO lattice_app WITH CHECK ({_AUDIT_INSERT_0018})"
    )
    for function in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION {function}")
    op.execute("REVOKE SELECT ON outbound_targets FROM lattice_app")
    op.execute("REVOKE SELECT (last_used_at, hint) ON scim_tokens FROM lattice_app")
    op.execute("DROP POLICY app_attempts_cleanup ON oidc_login_attempts")
    op.execute(
        "CREATE POLICY app_attempts_cleanup ON oidc_login_attempts AS RESTRICTIVE FOR DELETE "
        "TO lattice_app USING (consumed_at IS NOT NULL OR expires_at <= now())"
    )
    op.execute(
        "REVOKE UPDATE (outcome, observed_identifier, completed_at) "
        "ON oidc_login_attempts FROM lattice_app"
    )
    op.execute(
        "REVOKE UPDATE (last_failure_code, last_test_at, last_test_kind, last_test_result) "
        "ON oidc_connections FROM lattice_app"
    )
    op.execute(
        "REVOKE SELECT (client_id, login_key, client_secret_ref, created_at, updated_at, "
        "last_failure_code, last_test_at, last_test_kind, last_test_result) "
        "ON oidc_connections FROM lattice_app"
    )
    op.execute(
        "ALTER TABLE scim_tokens DROP CONSTRAINT ck_scim_tokens_hint, "
        "DROP CONSTRAINT ck_scim_tokens_last_used_at, DROP COLUMN hint, DROP COLUMN last_used_at"
    )
    # Test attempts cannot be represented in 0019: delete them (an owner path).
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            DELETE FROM oidc_login_attempts WHERE organization_id = org AND purpose = 'test';
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE oidc_login_attempts DROP CONSTRAINT ck_oidc_login_attempts_outcome, "
        "DROP CONSTRAINT ck_oidc_login_attempts_proposed, "
        "DROP CONSTRAINT ck_oidc_login_attempts_purpose, "
        "DROP CONSTRAINT fk_oidc_login_attempts_membership, DROP COLUMN proof_used_at, "
        "DROP COLUMN completed_at, DROP COLUMN observed_identifier, DROP COLUMN outcome, "
        "DROP COLUMN proposed_subject_claim, DROP COLUMN proposed_issuer, "
        "DROP COLUMN membership_id, DROP COLUMN purpose"
    )
    op.execute(
        "ALTER TABLE oidc_connections DROP CONSTRAINT ck_oidc_connections_last_test, "
        "DROP CONSTRAINT ck_oidc_connections_last_failure_code, DROP COLUMN last_test_result, "
        "DROP COLUMN last_test_kind, DROP COLUMN last_test_at, DROP COLUMN last_failure_code"
    )
    # Refusal events of the identity provider (the action 0019's policy never admitted).
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            DELETE FROM audit_events
            WHERE organization_id = org AND action = 'scim.request_refused';
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )
