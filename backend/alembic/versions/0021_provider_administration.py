"""Provider administration, BYOK and egress policy in the product (Phase 12, P12.5;
ADR-017; phase-12-plan C3, C4, C6 / D-34, C11, C12).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-17
Reversible: no (the downgrade drops step-up grants and attempts, versions and test facts)
Locks: brief ACCESS EXCLUSIVE on org_settings, model_providers, provider_credentials,
  oidc_connections, oidc_login_attempts and audit_events (columns, checks, policies,
  triggers); one UPDATE per organization of its generation records (few rows)

The downgrade also cannot restore a denied external class (it becomes ``internal``) or tell
which generation approvals 0021 recorded (they stay).

No broad write is granted. ``lattice_app`` changes providers, credentials and the
organization's AI egress policy only through fenced functions (``SECURITY DEFINER``,
owned by ``lattice_owner``, search path pinned, ``EXECUTE`` for ``lattice_app`` only), each
re-checking an active administrator of the bound, active organization and refusing with
SQLSTATE ``LA001`` and a fixed reason:

* ``lattice_preview_egress_change(actor, change)`` -- applies ``change`` inside a
  subtransaction that is always rolled back and returns the policy version, the egress
  cells before and after (``lattice_egress_cells``: which data level may reach which
  provider record at which destination) and whether the change is an **escalation**
  (some cell is newly allowed, or the default question sensitivity is lowered, D-34).
  Nothing is ever written.
* ``lattice_apply_egress_change(actor, session, change, expected version, grant)`` -- the
  only API write path to ``org_settings`` and to the approval, enablement and data terms
  of ``model_providers``. Under the organization's egress lock it compares the policy
  version the administrator previewed, applies the change, computes the cells again and,
  **when the change is an escalation**, consumes a single-use step-up grant of the same
  administrator and session for exactly this change and this version, unexpired, with the
  session still live -- or refuses (``step_up_required``, ``grant_*``). Lowering needs no
  grant. The escalation decision is the database's, not the caller's.
* ``lattice_create_step_up_grant(actor, session, attempt, auth_time, acr)`` -- the OIDC
  callback's write after the API verified a fresh identity-provider re-authentication: the
  attempt must be the same administrator's and session's consumed ``step_up`` attempt,
  ``auth_time`` no older than five minutes and not before the attempt began, the session
  live; the grant copies the attempt's change, reason and policy version and expires five
  minutes after the authentication at most.
* ``lattice_create_provider(actor, provider, model, target, training_opt_out)`` -- a
  generation record, created **disabled and unapproved** (never an escalation). An
  ``ollama`` endpoint is never typed: it is an outbound target the operator registered for
  this organization and ``generation`` (C4), and the trust class follows it (loopback =
  ``local``, otherwise ``organization_controlled``, HTTPS only) -- an administrator cannot
  declare a destination local.
* ``lattice_set_provider_endpoint(actor, provider, expected version, target)`` -- another
  registered target; the trust class cannot change, and the record's approval is withdrawn
  (it was given for the previous destination).
* ``lattice_set_provider_credential(actor, provider, expected, credential, secret, key id,
  nonce, ciphertext, hint, data use)`` / ``lattice_revoke_provider_credential(actor,
  provider, expected)`` -- an organization-owned API key as AES-256-GCM ciphertext sealed by
  the API (C3.4), always ``vault:`` (an administrator never names an environment variable);
  replacing or revoking destroys the previous ciphertext, whatever managed it. A credential
  never touches ``model_providers`` or ``org_settings``.

Policy changes:

* **Generation needs an explicit approval** (``approved_at``) for every trust class: a
  record created in the product is unapproved until an administrator approves it. Existing
  generation records were approved for ``internal`` by being configured (ADR-011); they
  keep that, recorded as ``approved_at = created_at``.
* ``org_settings.external_egress_level`` may be NULL: external APIs are not allowed at all
  (as ``organization_controlled_level`` NULL already meant for that class). An organization
  without settings keeps the default (``internal``).
* ``egress_policy_versions``: a counter per organization, raised by every write to
  ``org_settings`` and to the policy columns of ``model_providers`` by **any** writer
  (operators and the worker included), so a preview and a step-up grant are bound to the
  exact policy they were computed on.
* ``oidc_connections.step_up_acr_values``: an operator-configured assurance requirement
  for step-up (``acr``); NULL = none (a fresh authentication is required, not a method).

Audit coupling (deferred constraint triggers, ``lattice_app`` only): provider record writes
need a ``provider.*`` event naming the record; settings writes ``egress.levels_changed``;
credential writes ``credential.*`` naming the provider; a grant ``egress.step_up_completed``
naming it and its consumption the committing event naming it. The audit insert policy
admits these families, and ``egress.escalation_refused``, from ``member:`` actors.

Facts (column grants, not audited): ``model_providers.last_test_at``,
``last_test_result``, ``last_test_credential_id`` -- the latest credential/endpoint test.
"""

# The function bodies are composed of this module's fixed SQL fragments; no input reaches
# them (S608 does not apply).
# ruff: noqa: S608
from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One egress-policy change of an organization at a time: b"LEGR".
EGRESS_LOCK = "pg_advisory_xact_lock(1279608658, hashtext(v_org::text))"
# One credential change per organization and provider: b"LCRD".
CREDENTIAL_LOCK = "pg_advisory_xact_lock(1279480388, hashtext(v_org::text || '|' || p_provider))"
_LEVELS = "('internal', 'confidential', 'restricted')"
_TEST_RESULTS = (
    "'valid', 'invalid_credential', 'configuration_invalid', 'provider_unavailable', "
    "'outbound_blocked', 'credential_missing', 'unsupported'"
)
_STEP_UP_OUTCOMES = (
    "'granted', 'not_fresh', 'session_mismatch', 'assurance_insufficient', 'step_up_invalid'"
)
_TEST_OUTCOMES_0020 = (
    "'matched', 'mismatched', 'not_provisioned', 'sign_in_failed', 'token_invalid', "
    "'provider_unavailable', 'invalid_configuration', 'outbound_blocked', "
    "'credentials_rejected'"
)
_PROVIDER_ACTIONS = (
    "'provider.configured', 'provider.enabled', 'provider.disabled', 'provider.approval_changed'"
)
_CREDENTIAL_ACTIONS = "'credential.set', 'credential.rotated', 'credential.revoked'"
_EGRESS_ACTIONS = "'egress.levels_changed', 'egress.step_up_completed', 'egress.escalation_refused'"
_SSO_ACTIONS = (
    "'sso.connection_created', 'sso.connection_updated', 'sso.connection_disabled', "
    "'sso.connection_enabled', 'sso.connection_tested'"
)
_SCIM_ACTIONS = "'scim.token_created', 'scim.token_rotated', 'scim.token_revoked'"
_AUDIT_0020 = (
    "(action LIKE 'access.%' AND (actor LIKE 'member:%' OR actor LIKE 'scim:%')) "
    "OR (action IN ('organization.updated', 'account.created', 'account.updated', "
    "'account.archived', 'account.reactivated', 'account.mapping_changed', "
    f"{_SSO_ACTIONS}, {_SCIM_ACTIONS}) AND actor LIKE 'member:%') "
    "OR (action = 'scim.request_refused' AND actor LIKE 'scim:%')"
)
_AUDIT_0021 = (
    "(action LIKE 'access.%' AND (actor LIKE 'member:%' OR actor LIKE 'scim:%')) "
    "OR (action IN ('organization.updated', 'account.created', 'account.updated', "
    "'account.archived', 'account.reactivated', 'account.mapping_changed', "
    f"{_SSO_ACTIONS}, {_SCIM_ACTIONS}, {_PROVIDER_ACTIONS}, {_CREDENTIAL_ACTIONS}, "
    f"{_EGRESS_ACTIONS}) AND actor LIKE 'member:%') "
    "OR (action = 'scim.request_refused' AND actor LIKE 'scim:%')"
)
_FUNCTIONS = (
    "lattice_preview_egress_change(uuid, jsonb)",
    "lattice_apply_egress_change(uuid, uuid, jsonb, bigint, uuid)",
    "lattice_create_step_up_grant(uuid, uuid, uuid, timestamptz, text)",
    "lattice_create_provider(uuid, text, text, uuid, boolean)",
    "lattice_set_provider_endpoint(uuid, uuid, bigint, uuid)",
    "lattice_set_provider_credential(uuid, text, uuid, uuid, uuid, text, bytea, bytea, text, text)",
    "lattice_revoke_provider_credential(uuid, text, uuid)",
)
# Owner-only helpers (no EXECUTE for any runtime role).
_HELPERS = (
    "lattice_egress_cells(uuid)",
    "lattice_egress_version(uuid)",
    "lattice_egress_write(uuid, jsonb)",
    "lattice_target_endpoint(uuid, uuid)",
    "lattice_require_policy_actor(uuid)",
)

_HEADER = """
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""
_REFUSE = "RAISE EXCEPTION '{0}' USING ERRCODE = 'LA001';"


def _fn(signature: str, returns: str, declare: str, body: str) -> str:
    return (
        f"CREATE FUNCTION {signature} RETURNS {returns} {_HEADER} AS $$\n"
        f"DECLARE\n  v_org uuid := public.lattice_current_org_id();\n{declare}\n"
        f"BEGIN\n  PERFORM public.lattice_require_policy_actor(p_actor);\n{body}\nEND\n$$"
    )


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (organization_id = lattice_current_org_id()) "
        "WITH CHECK (organization_id = lattice_current_org_id())"
    )


def _per_org(statement: str) -> None:
    """Run ``statement`` once per organization, bound to it (forced RLS binds the owner)."""
    op.execute(
        f"""
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            {statement}
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )


def upgrade() -> None:
    # --- policy columns -------------------------------------------------------------------
    # NULL: external APIs are not allowed (the CHECK admits NULL; the default stays).
    op.execute("ALTER TABLE org_settings ALTER COLUMN external_egress_level DROP NOT NULL")
    # Generation needs an explicit approval from now on; configured records were approved
    # for internal by their configuration (ADR-011) -- record that, not a new approval.
    _per_org(
        "UPDATE model_providers SET approved_at = created_at "
        "WHERE organization_id = org AND purpose = 'generation' AND approved_at IS NULL;"
    )
    op.execute(
        f"""
        ALTER TABLE model_providers
          ADD COLUMN last_test_at timestamptz,
          ADD COLUMN last_test_result text,
          ADD COLUMN last_test_credential_id uuid,
          ADD CONSTRAINT ck_model_providers_last_test CHECK (
            (last_test_at IS NULL AND last_test_result IS NULL
             AND last_test_credential_id IS NULL)
            OR (last_test_at IS NOT NULL AND last_test_result IN ({_TEST_RESULTS})))
        """
    )
    op.execute(
        "ALTER TABLE oidc_connections ADD COLUMN step_up_acr_values text[], "
        "ADD CONSTRAINT ck_oidc_connections_step_up_acr CHECK (step_up_acr_values IS NULL "
        "OR (cardinality(step_up_acr_values) BETWEEN 1 AND 10 "
        "AND array_position(step_up_acr_values, NULL) IS NULL))"
    )

    # --- the policy version ---------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE egress_policy_versions (
          organization_id uuid NOT NULL,
          version bigint NOT NULL DEFAULT 0,
          changed_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_egress_policy_versions PRIMARY KEY (organization_id),
          CONSTRAINT fk_egress_policy_versions_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT ck_egress_policy_versions_version CHECK (version >= 0)
        )
        """
    )
    _tenant_rls("egress_policy_versions")
    op.execute("GRANT SELECT ON egress_policy_versions TO lattice_app")
    op.execute(
        """
        CREATE FUNCTION lattice_bump_egress_version() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            UPDATE public.egress_policy_versions SET version = version + 1, changed_at = now()
            WHERE organization_id = OLD.organization_id;
            RETURN NULL;
          END IF;
          INSERT INTO public.egress_policy_versions (organization_id, version)
          VALUES (NEW.organization_id, 1)
          ON CONFLICT (organization_id) DO UPDATE
            SET version = public.egress_policy_versions.version + 1, changed_at = now();
          RETURN NULL;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION lattice_bump_egress_version() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_org_settings_egress_version AFTER INSERT OR UPDATE OR DELETE "
        "ON org_settings FOR EACH ROW EXECUTE FUNCTION lattice_bump_egress_version()"
    )
    op.execute(
        "CREATE TRIGGER trg_model_providers_egress_version AFTER INSERT OR DELETE OR UPDATE OF "
        "purpose, provider, model, deployment, approved_for_level, approved_at, enabled, "
        "endpoint, retention_mode, training_opt_out ON model_providers "
        "FOR EACH ROW EXECUTE FUNCTION lattice_bump_egress_version()"
    )

    # --- step-up attempts and grants (D-34) -------------------------------------------------
    op.execute(
        f"""
        ALTER TABLE oidc_login_attempts
          ADD COLUMN session_id uuid,
          ADD COLUMN change jsonb,
          ADD COLUMN reason text,
          ADD COLUMN policy_version bigint,
          ADD CONSTRAINT fk_oidc_login_attempts_session
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
          DROP CONSTRAINT ck_oidc_login_attempts_purpose,
          ADD CONSTRAINT ck_oidc_login_attempts_purpose CHECK (
            (purpose = 'sign_in' AND membership_id IS NULL AND proposed_issuer IS NULL
             AND proposed_subject_claim IS NULL AND outcome IS NULL
             AND observed_identifier IS NULL AND completed_at IS NULL
             AND proof_used_at IS NULL AND session_id IS NULL AND change IS NULL
             AND reason IS NULL AND policy_version IS NULL)
            OR (purpose = 'test' AND membership_id IS NOT NULL AND session_id IS NULL
                AND change IS NULL AND reason IS NULL AND policy_version IS NULL)
            OR (purpose = 'step_up' AND membership_id IS NOT NULL AND session_id IS NOT NULL
                AND change IS NOT NULL AND jsonb_typeof(change) = 'object'
                AND octet_length(change::text) <= 4096
                AND reason IS NOT NULL AND length(reason) BETWEEN 1 AND 500
                AND policy_version IS NOT NULL AND proposed_issuer IS NULL
                AND proposed_subject_claim IS NULL AND proof_used_at IS NULL)),
          DROP CONSTRAINT ck_oidc_login_attempts_outcome,
          ADD CONSTRAINT ck_oidc_login_attempts_outcome CHECK (
            (outcome IS NULL AND completed_at IS NULL AND observed_identifier IS NULL)
            OR (outcome IN ({_TEST_OUTCOMES_0020}, {_STEP_UP_OUTCOMES})
                AND completed_at IS NOT NULL
                AND (observed_identifier IS NULL
                     OR length(observed_identifier) BETWEEN 1 AND 255)))
        """
    )
    op.execute(
        """
        CREATE TABLE step_up_grants (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          membership_id uuid NOT NULL,
          session_id uuid NOT NULL,
          attempt_id uuid NOT NULL,
          change jsonb NOT NULL,
          change_digest bytea NOT NULL,
          reason text NOT NULL,
          policy_version bigint NOT NULL,
          auth_time timestamptz NOT NULL,
          acr text,
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          consumed_at timestamptz,
          CONSTRAINT pk_step_up_grants PRIMARY KEY (id),
          CONSTRAINT uq_step_up_grants_attempt UNIQUE (attempt_id),
          CONSTRAINT fk_step_up_grants_membership FOREIGN KEY (organization_id, membership_id)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_step_up_grants_session
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
          CONSTRAINT ck_step_up_grants_digest CHECK (octet_length(change_digest) = 32),
          CONSTRAINT ck_step_up_grants_reason CHECK (length(reason) BETWEEN 1 AND 500),
          CONSTRAINT ck_step_up_grants_window CHECK (
            expires_at > auth_time AND expires_at <= auth_time + interval '5 minutes'
            AND expires_at <= created_at + interval '5 minutes'),
          CONSTRAINT ck_step_up_grants_acr CHECK (acr IS NULL OR length(acr) BETWEEN 1 AND 200)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_step_up_grants_organization_session "
        "ON step_up_grants (organization_id, session_id)"
    )
    _tenant_rls("step_up_grants")
    op.execute("GRANT SELECT ON step_up_grants TO lattice_app")

    # --- reads and facts for the API role -------------------------------------------------
    op.execute(
        "GRANT UPDATE (last_test_at, last_test_result, last_test_credential_id) "
        "ON model_providers TO lattice_app"
    )
    op.execute("GRANT SELECT (step_up_acr_values) ON oidc_connections TO lattice_app")

    # --- helpers (owner only) -------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION lattice_require_policy_actor(p_actor uuid) RETURNS void
        LANGUAGE plpgsql STABLE
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_org uuid := public.lattice_current_org_id();
        BEGIN
          IF v_org IS NULL THEN
            RAISE EXCEPTION 'no organization is bound' USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.memberships m JOIN public.users u ON u.id = m.user_id
            WHERE m.organization_id = v_org AND m.id = p_actor AND m.is_admin
              AND m.status = 'active' AND u.status = 'active'
          ) THEN
            RAISE EXCEPTION 'not_administrator' USING ERRCODE = 'LA001';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM public.organizations o
                         WHERE o.id = v_org AND o.status = 'active') THEN
            RAISE EXCEPTION 'organization_inactive' USING ERRCODE = 'LA001';
          END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION lattice_egress_version(p_org uuid) RETURNS bigint
        LANGUAGE sql STABLE
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          SELECT coalesce((SELECT v.version FROM public.egress_policy_versions v
                           WHERE v.organization_id = p_org), 0)
        $$
        """
    )
    # Which data level may reach which provider record at which destination: the egress
    # lattice of the recorded configuration (lattice.providers.lattice computes the same
    # cells with their reasons; tests/providers/test_egress_lattice.py proves they agree).
    op.execute(
        f"""
        CREATE FUNCTION lattice_egress_cells(p_org uuid) RETURNS jsonb
        LANGUAGE sql STABLE
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          WITH s AS (
            SELECT CASE WHEN o.organization_id IS NULL THEN 'internal'
                        ELSE o.external_egress_level END AS ext,
                   o.organization_controlled_level AS orgc,
                   coalesce(o.data_origin, 'customer') AS origin
            FROM (SELECT p_org AS id) x
            LEFT JOIN public.org_settings o ON o.organization_id = x.id
          ),
          lv(level, rank) AS (VALUES ('internal', 0), ('confidential', 1), ('restricted', 2)),
          p AS (
            SELECT m.id, coalesce(m.endpoint, '') AS endpoint, m.approved_for_level AS approval,
                   CASE m.deployment WHEN 'external_api' THEN s.ext
                                     WHEN 'organization_controlled' THEN s.orgc
                                     WHEN 'local' THEN 'restricted' END AS ceiling
            FROM public.model_providers m CROSS JOIN s
            WHERE m.organization_id = p_org
              AND m.deployment IN ('local', 'organization_controlled', 'external_api')
              AND (m.purpose <> 'generation' OR (m.enabled AND m.approved_at IS NOT NULL))
              AND (m.deployment <> 'organization_controlled' OR m.approved_at IS NOT NULL)
              AND (m.training_opt_out OR s.origin = 'synthetic')
              AND m.approved_for_level IN {_LEVELS}
          )
          SELECT coalesce(
            jsonb_agg(jsonb_build_object('p', p.id, 'e', p.endpoint, 'l', l.level)
                      ORDER BY p.id, l.rank),
            '[]'::jsonb)
          FROM p
          JOIN lv c ON c.level = p.ceiling
          JOIN lv a ON a.level = p.approval
          JOIN lv l ON l.rank <= least(c.rank, a.rank)
        $$
        """
    )
    # The endpoint of an operator-registered generation target (C4) and the trust class it
    # implies: loopback = local, otherwise organization_controlled, which must be https.
    op.execute(
        """
        CREATE FUNCTION lattice_target_endpoint(p_org uuid, p_target uuid)
        RETURNS TABLE (endpoint text, deployment text)
        LANGUAGE plpgsql STABLE
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          t record;
          v_local boolean;
        BEGIN
          SELECT o.scheme, o.host, o.port INTO t FROM public.outbound_targets o
          WHERE o.organization_id = p_org AND o.id = p_target AND o.purpose = 'generation'
            AND o.revoked_at IS NULL;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'target_not_registered' USING ERRCODE = 'LA001';
          END IF;
          v_local := t.host = 'localhost'
            OR (t.host ~ '^[0-9a-f.:]+$' AND (t.host::inet << inet '127.0.0.0/8'
                                               OR t.host::inet = inet '::1'));
          IF NOT v_local AND t.scheme <> 'https' THEN
            RAISE EXCEPTION 'endpoint_not_https' USING ERRCODE = 'LA001';
          END IF;
          endpoint := t.scheme || '://'
            || CASE WHEN position(':' IN t.host) > 0 THEN '[' || t.host || ']' ELSE t.host END
            || ':' || t.port::text;
          deployment := CASE WHEN v_local THEN 'local' ELSE 'organization_controlled' END;
          RETURN NEXT;
        END
        $$
        """
    )
    # Apply one change document to the bound organization's policy rows (no checks of who
    # may: the callers are the preview and apply functions).
    op.execute(
        f"""
        CREATE FUNCTION lattice_egress_write(p_org uuid, p_change jsonb) RETURNS jsonb
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          v_kind text := p_change ->> 'kind';
          v_p public.model_providers%ROWTYPE;
          v_before public.org_settings%ROWTYPE;
          v_after public.org_settings%ROWTYPE;
          v_id uuid;
          v_affected jsonb := '[]'::jsonb;
          v_approval text;
          v_changed boolean := false;
        BEGIN
          IF jsonb_typeof(p_change) IS DISTINCT FROM 'object' THEN
            {_REFUSE.format("change_invalid")}
          END IF;
          IF v_kind = 'settings' THEN
            IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_change) k
                       WHERE k NOT IN ('kind', 'external_egress_level',
                                       'organization_controlled_level',
                                       'default_query_sensitivity'))
               OR NOT (p_change ? 'external_egress_level'
                       OR p_change ? 'organization_controlled_level'
                       OR p_change ? 'default_query_sensitivity') THEN
              {_REFUSE.format("change_invalid")}
            END IF;
            IF (p_change ? 'external_egress_level'
                AND jsonb_typeof(p_change -> 'external_egress_level') <> 'null'
                AND (p_change ->> 'external_egress_level') NOT IN {_LEVELS})
               OR (p_change ? 'organization_controlled_level'
                   AND jsonb_typeof(p_change -> 'organization_controlled_level') <> 'null'
                   AND (p_change ->> 'organization_controlled_level') NOT IN {_LEVELS})
               OR (p_change ? 'default_query_sensitivity'
                   AND coalesce(p_change ->> 'default_query_sensitivity', '') NOT IN {_LEVELS})
            THEN
              {_REFUSE.format("change_invalid")}
            END IF;
            SELECT * INTO v_before FROM public.org_settings WHERE organization_id = p_org;
            IF NOT FOUND THEN
              INSERT INTO public.org_settings (organization_id) VALUES (p_org)
              RETURNING * INTO v_before;
            END IF;
            UPDATE public.org_settings SET
              external_egress_level = CASE WHEN p_change ? 'external_egress_level'
                THEN p_change ->> 'external_egress_level' ELSE external_egress_level END,
              organization_controlled_level = CASE WHEN p_change ? 'organization_controlled_level'
                THEN p_change ->> 'organization_controlled_level'
                ELSE organization_controlled_level END,
              default_query_sensitivity = CASE WHEN p_change ? 'default_query_sensitivity'
                THEN p_change ->> 'default_query_sensitivity' ELSE default_query_sensitivity END
            WHERE organization_id = p_org
            RETURNING * INTO v_after;
            IF v_after.external_egress_level IS NOT DISTINCT FROM v_before.external_egress_level
               AND v_after.organization_controlled_level
                   IS NOT DISTINCT FROM v_before.organization_controlled_level
               AND v_after.default_query_sensitivity = v_before.default_query_sensitivity THEN
              {_REFUSE.format("no_change")}
            END IF;
            RETURN v_affected;
          END IF;
          IF v_kind <> 'provider' THEN
            {_REFUSE.format("change_invalid")}
          END IF;
          IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_change) k
                     WHERE k NOT IN ('kind', 'provider_id', 'enabled', 'approval',
                                     'training_opt_out'))
             OR NOT (p_change ? 'enabled' OR p_change ? 'approval' OR p_change ? 'training_opt_out')
             OR coalesce(p_change ->> 'provider_id', '')
                !~ '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
             OR (p_change ? 'enabled' AND jsonb_typeof(p_change -> 'enabled') <> 'boolean')
             OR (p_change ? 'training_opt_out'
                 AND jsonb_typeof(p_change -> 'training_opt_out') <> 'boolean')
             OR (p_change ? 'approval' AND jsonb_typeof(p_change -> 'approval') <> 'null'
                 AND (p_change ->> 'approval') NOT IN {_LEVELS}) THEN
            {_REFUSE.format("change_invalid")}
          END IF;
          v_id := (p_change ->> 'provider_id')::uuid;
          SELECT * INTO v_p FROM public.model_providers
          WHERE organization_id = p_org AND id = v_id FOR UPDATE;
          IF NOT FOUND THEN
            {_REFUSE.format("provider_not_found")}
          END IF;
          -- Embedding is the deployment's (its data terms and use are not the
          -- organization's to change): only its approval for this organization.
          IF v_p.purpose = 'embedding'
             AND (p_change ? 'enabled' OR p_change ? 'training_opt_out') THEN
            {_REFUSE.format("deployment_managed")}
          END IF;
          IF p_change ? 'enabled' AND (p_change -> 'enabled')::boolean <> v_p.enabled THEN
            IF (p_change -> 'enabled')::boolean THEN
              -- One enabled generation configuration (the unique index): the other stops.
              WITH off AS (
                UPDATE public.model_providers SET enabled = false
                WHERE organization_id = p_org AND purpose = 'generation' AND enabled
                  AND id <> v_id
                RETURNING id
              )
              SELECT coalesce(jsonb_agg(off.id), '[]'::jsonb) INTO v_affected FROM off;
            END IF;
            UPDATE public.model_providers SET enabled = (p_change -> 'enabled')::boolean
            WHERE organization_id = p_org AND id = v_id;
            v_changed := true;
          END IF;
          IF p_change ? 'approval' THEN
            v_approval := p_change ->> 'approval';
            IF v_approval IS NULL THEN
              IF v_p.approved_at IS NOT NULL OR v_p.approved_for_level <> 'internal' THEN
                UPDATE public.model_providers SET approved_at = NULL,
                       approved_for_level = 'internal'
                WHERE organization_id = p_org AND id = v_id;
                v_changed := true;
              END IF;
            ELSIF v_p.approved_at IS NULL OR v_p.approved_for_level <> v_approval THEN
              UPDATE public.model_providers SET approved_at = now(),
                     approved_for_level = v_approval
              WHERE organization_id = p_org AND id = v_id;
              v_changed := true;
            END IF;
          END IF;
          IF p_change ? 'training_opt_out'
             AND (p_change -> 'training_opt_out')::boolean <> v_p.training_opt_out THEN
            IF v_p.provider <> 'gemini' THEN
              {_REFUSE.format("data_terms_fixed")}
            END IF;
            UPDATE public.model_providers SET
              training_opt_out = (p_change -> 'training_opt_out')::boolean,
              retention_mode = CASE WHEN (p_change -> 'training_opt_out')::boolean
                THEN 'abuse_monitoring_limited' ELSE 'unpaid_product_improvement' END
            WHERE organization_id = p_org AND id = v_id;
            v_changed := true;
          END IF;
          IF NOT v_changed THEN
            {_REFUSE.format("no_change")}
          END IF;
          RETURN v_affected;
        END
        $$
        """
    )

    # --- the egress functions -------------------------------------------------------------
    _cells_and_default = """
  v_cells := public.lattice_egress_cells(v_org);
  v_default := coalesce((SELECT s.default_query_sensitivity FROM public.org_settings s
                         WHERE s.organization_id = v_org), 'internal');"""
    _escalation = """
  v_escalation := EXISTS (
      SELECT 1 FROM jsonb_array_elements(v_after) a WHERE NOT v_before @> jsonb_build_array(a))
    OR array_position(ARRAY['internal', 'confidential', 'restricted'], v_default_after)
       < array_position(ARRAY['internal', 'confidential', 'restricted'], v_default_before);"""
    _declare = (
        "  v_version bigint;\n  v_cells jsonb;\n  v_default text;\n  v_before jsonb;\n"
        "  v_after jsonb;\n  v_default_before text;\n  v_default_after text;\n"
        "  v_escalation boolean;\n  v_affected jsonb;"
    )
    op.execute(
        _fn(
            "lattice_preview_egress_change(p_actor uuid, p_change jsonb)",
            "jsonb",
            _declare,
            f"""
  v_version := public.lattice_egress_version(v_org);
  {_cells_and_default}
  v_before := v_cells;
  v_default_before := v_default;
  BEGIN
    v_affected := public.lattice_egress_write(v_org, p_change);
    {_cells_and_default}
    v_after := v_cells;
    v_default_after := v_default;
    -- A dry run: always rolled back (the subtransaction ends in this exception).
    RAISE EXCEPTION 'preview' USING ERRCODE = 'LA002';
  EXCEPTION WHEN SQLSTATE 'LA002' THEN
    NULL;
  END;
  {_escalation}
  RETURN jsonb_build_object('version', v_version, 'before', v_before, 'after', v_after,
    'default_before', v_default_before, 'default_after', v_default_after,
    'escalation', v_escalation, 'affected', v_affected);""",
        )
    )
    op.execute(
        _fn(
            "lattice_apply_egress_change(p_actor uuid, p_session uuid, p_change jsonb, "
            "p_expected bigint, p_grant uuid)",
            "jsonb",
            _declare + "\n  v_g public.step_up_grants%ROWTYPE;",
            f"""
  PERFORM {EGRESS_LOCK};
  v_version := public.lattice_egress_version(v_org);
  IF p_expected IS NULL OR v_version <> p_expected THEN
    {_REFUSE.format("policy_changed")}
  END IF;
  {_cells_and_default}
  v_before := v_cells;
  v_default_before := v_default;
  v_affected := public.lattice_egress_write(v_org, p_change);
  {_cells_and_default}
  v_after := v_cells;
  v_default_after := v_default;
  {_escalation}
  IF v_escalation THEN
    -- D-34: newly allowed egress needs a fresh identity-provider re-authentication of this
    -- administrator, in this session, for exactly this change on exactly this policy.
    IF p_grant IS NULL THEN
      {_REFUSE.format("step_up_required")}
    END IF;
    SELECT * INTO v_g FROM public.step_up_grants g
    WHERE g.organization_id = v_org AND g.id = p_grant FOR UPDATE;
    IF NOT FOUND OR v_g.membership_id <> p_actor OR v_g.session_id IS DISTINCT FROM p_session
    THEN
      {_REFUSE.format("grant_invalid")}
    END IF;
    IF v_g.consumed_at IS NOT NULL THEN
      {_REFUSE.format("grant_consumed")}
    END IF;
    IF v_g.expires_at <= now() THEN
      {_REFUSE.format("grant_expired")}
    END IF;
    IF v_g.policy_version <> p_expected OR v_g.change <> p_change THEN
      {_REFUSE.format("grant_mismatch")}
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.sessions s
                   WHERE s.organization_id = v_org AND s.id = p_session
                     AND s.revoked_at IS NULL AND s.expires_at > now()) THEN
      {_REFUSE.format("session_ended")}
    END IF;
    UPDATE public.step_up_grants SET consumed_at = now()
    WHERE organization_id = v_org AND id = p_grant;
  END IF;
  RETURN jsonb_build_object('version', v_version, 'before', v_before, 'after', v_after,
    'default_before', v_default_before, 'default_after', v_default_after,
    'escalation', v_escalation, 'affected', v_affected,
    'grant', CASE WHEN v_escalation THEN p_grant END);""",
        )
    )
    op.execute(
        _fn(
            "lattice_create_step_up_grant(p_actor uuid, p_session uuid, p_attempt uuid, "
            "p_auth_time timestamptz, p_acr text)",
            "uuid",
            "  v_a public.oidc_login_attempts%ROWTYPE;\n  v_id uuid;",
            f"""
  SELECT * INTO v_a FROM public.oidc_login_attempts a
  WHERE a.organization_id = v_org AND a.id = p_attempt FOR UPDATE;
  IF NOT FOUND OR v_a.purpose <> 'step_up' OR v_a.membership_id <> p_actor
     OR v_a.session_id IS DISTINCT FROM p_session OR v_a.consumed_at IS NULL
     OR v_a.outcome IS NOT NULL THEN
    {_REFUSE.format("step_up_invalid")}
  END IF;
  -- Fresh: authenticated for this attempt (not before it began, one minute of clock
  -- leeway) and no more than five minutes ago.
  IF p_auth_time IS NULL OR p_auth_time > now() + interval '1 minute'
     OR p_auth_time < now() - interval '5 minutes'
     OR p_auth_time < v_a.created_at - interval '1 minute' THEN
    {_REFUSE.format("step_up_not_fresh")}
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.sessions s
                 WHERE s.organization_id = v_org AND s.id = p_session
                   AND s.revoked_at IS NULL AND s.expires_at > now()) THEN
    {_REFUSE.format("session_ended")}
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.directory_identities d
                 JOIN public.oidc_connections c
                   ON c.organization_id = d.organization_id AND c.id = d.connection_id
                  AND c.status = 'active'
                 WHERE d.organization_id = v_org AND d.connection_id = v_a.connection_id
                   AND d.membership_id = p_actor) THEN
    {_REFUSE.format("step_up_invalid")}
  END IF;
  INSERT INTO public.step_up_grants (organization_id, membership_id, session_id, attempt_id,
    change, change_digest, reason, policy_version, auth_time, acr, expires_at)
  VALUES (v_org, p_actor, p_session, p_attempt, v_a.change,
    sha256(convert_to(v_a.change::text, 'UTF8')), v_a.reason, v_a.policy_version,
    least(p_auth_time, now()), p_acr, least(p_auth_time, now()) + interval '5 minutes')
  RETURNING id INTO v_id;
  UPDATE public.oidc_login_attempts SET outcome = 'granted', completed_at = now()
  WHERE organization_id = v_org AND id = p_attempt;
  RETURN v_id;""",
        )
    )

    # --- provider records -----------------------------------------------------------------
    op.execute(
        _fn(
            "lattice_create_provider(p_actor uuid, p_provider text, p_model text, "
            "p_target uuid, p_training_opt_out boolean)",
            "uuid",
            "  v_endpoint text;\n  v_deployment text;\n  v_retention text;\n  v_id uuid;",
            f"""
  PERFORM {EGRESS_LOCK};
  IF p_model IS NULL OR p_model !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{{0,99}}$' THEN
    {_REFUSE.format("model_invalid")}
  END IF;
  IF p_provider = 'gemini' THEN
    IF p_target IS NOT NULL OR p_training_opt_out IS NULL THEN
      {_REFUSE.format("change_invalid")}
    END IF;
    v_deployment := 'external_api';
    v_retention := CASE WHEN p_training_opt_out THEN 'abuse_monitoring_limited'
                        ELSE 'unpaid_product_improvement' END;
  ELSIF p_provider = 'ollama' THEN
    IF p_target IS NULL THEN
      {_REFUSE.format("target_not_registered")}
    END IF;
    SELECT t.endpoint, t.deployment INTO v_endpoint, v_deployment
    FROM public.lattice_target_endpoint(v_org, p_target) t;
    v_retention := 'none';
    p_training_opt_out := true;
  ELSIF p_provider = 'fake' THEN
    -- The test double (the API refuses it in production; the factory never builds it there).
    IF p_target IS NOT NULL THEN
      {_REFUSE.format("change_invalid")}
    END IF;
    v_deployment := 'external_api';
    v_retention := 'none';
    p_training_opt_out := true;
  ELSE
    {_REFUSE.format("provider_unsupported")}
  END IF;
  IF EXISTS (SELECT 1 FROM public.model_providers m
             WHERE m.organization_id = v_org AND m.purpose = 'generation'
               AND m.provider = p_provider AND m.model = p_model) THEN
    {_REFUSE.format("provider_exists")}
  END IF;
  -- Disabled and unapproved: creating a record never lets anything leave.
  INSERT INTO public.model_providers (organization_id, purpose, provider, model, deployment,
    endpoint, retention_mode, training_opt_out, enabled, approved_for_level, approved_at)
  VALUES (v_org, 'generation', p_provider, p_model, v_deployment, v_endpoint, v_retention,
    p_training_opt_out, false, 'internal', NULL)
  RETURNING id INTO v_id;
  RETURN v_id;""",
        )
    )
    op.execute(
        _fn(
            "lattice_set_provider_endpoint(p_actor uuid, p_provider uuid, p_expected bigint, "
            "p_target uuid)",
            "void",
            "  v_p public.model_providers%ROWTYPE;\n  v_endpoint text;\n  v_deployment text;",
            f"""
  PERFORM {EGRESS_LOCK};
  IF p_expected IS NULL OR public.lattice_egress_version(v_org) <> p_expected THEN
    {_REFUSE.format("policy_changed")}
  END IF;
  SELECT * INTO v_p FROM public.model_providers m
  WHERE m.organization_id = v_org AND m.id = p_provider FOR UPDATE;
  IF NOT FOUND THEN
    {_REFUSE.format("provider_not_found")}
  END IF;
  IF v_p.provider <> 'ollama' OR v_p.purpose <> 'generation' THEN
    {_REFUSE.format("endpoint_fixed")}
  END IF;
  SELECT t.endpoint, t.deployment INTO v_endpoint, v_deployment
  FROM public.lattice_target_endpoint(v_org, p_target) t;
  IF v_deployment <> v_p.deployment THEN
    {_REFUSE.format("trust_class_change")}
  END IF;
  IF v_endpoint IS NOT DISTINCT FROM v_p.endpoint THEN
    {_REFUSE.format("no_change")}
  END IF;
  -- A new destination: the approval given for the previous one is withdrawn.
  UPDATE public.model_providers SET endpoint = v_endpoint, approved_at = NULL,
         approved_for_level = 'internal'
  WHERE organization_id = v_org AND id = p_provider;""",
        )
    )

    # --- credentials ----------------------------------------------------------------------
    _revoke_active = """
  IF v_cur IS NOT NULL THEN
    UPDATE public.encrypted_secrets SET nonce = NULL, ciphertext = NULL, hint = NULL,
           destroyed_at = now()
    WHERE organization_id = v_org AND owner_kind = 'provider_api_key' AND owner_id = v_cur
      AND destroyed_at IS NULL;
    UPDATE public.provider_credentials SET status = 'revoked', revoked_at = now()
    WHERE organization_id = v_org AND id = v_cur;
  END IF;"""
    _current = f"""
  PERFORM {CREDENTIAL_LOCK};
  SELECT c.id INTO v_cur FROM public.provider_credentials c
  WHERE c.organization_id = v_org AND c.provider = p_provider AND c.status = 'active'
  FOR UPDATE;
  IF v_cur IS DISTINCT FROM p_expected THEN
    {_REFUSE.format("credential_changed")}
  END IF;"""
    op.execute(
        _fn(
            "lattice_set_provider_credential(p_actor uuid, p_provider text, p_expected uuid, "
            "p_credential uuid, p_secret uuid, p_key_id text, p_nonce bytea, p_ciphertext bytea, "
            "p_hint text, p_data_use text)",
            "text",
            "  v_cur uuid;",
            f"""
  IF p_provider IS DISTINCT FROM 'gemini' THEN
    {_REFUSE.format("credential_not_used")}
  END IF;
  IF p_data_use IS NULL OR p_data_use NOT IN ('paid', 'unpaid') THEN
    {_REFUSE.format("data_use_invalid")}
  END IF;
  IF p_credential IS NULL OR p_secret IS NULL OR p_key_id IS NULL OR p_nonce IS NULL
     OR p_ciphertext IS NULL THEN
    {_REFUSE.format("secret_invalid")}
  END IF;
  {_current}
  {_revoke_active}
  INSERT INTO public.encrypted_secrets (id, organization_id, owner_kind, owner_id, key_id,
    nonce, ciphertext, hint)
  VALUES (p_secret, v_org, 'provider_api_key', p_credential, p_key_id, p_nonce, p_ciphertext,
    p_hint);
  INSERT INTO public.provider_credentials (id, organization_id, provider, secret_ref, data_use)
  VALUES (p_credential, v_org, p_provider, 'vault:' || p_secret::text, p_data_use);
  RETURN CASE WHEN v_cur IS NULL THEN 'set' ELSE 'replaced' END;""",
        )
    )
    op.execute(
        _fn(
            "lattice_revoke_provider_credential(p_actor uuid, p_provider text, p_expected uuid)",
            "void",
            "  v_cur uuid;",
            f"""
  IF p_expected IS NULL THEN
    {_REFUSE.format("credential_changed")}
  END IF;
  {_current}
  {_revoke_active}""",
        )
    )

    for function in _HELPERS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO lattice_app")

    # --- audit coupling -------------------------------------------------------------------
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        f"TO lattice_app WITH CHECK ({_AUDIT_0021})"
    )
    _coupling(
        "lattice_require_provider_audit",
        f"e.action IN ({_PROVIDER_ACTIONS}) AND (e.target ->> 'provider_id' = NEW.id::text "
        "OR coalesce(e.target -> 'affected_provider_ids', '[]'::jsonb) ? NEW.id::text)",
        "provider change without an audit event",
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_model_providers_setup_audit AFTER INSERT OR UPDATE OF "
        "purpose, provider, model, deployment, approved_for_level, approved_at, enabled, "
        "endpoint, retention_mode, training_opt_out ON model_providers "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_provider_audit()"
    )
    _coupling(
        "lattice_require_egress_settings_audit",
        "e.action = 'egress.levels_changed'",
        "egress settings change without an audit event",
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_org_settings_setup_audit AFTER INSERT OR UPDATE "
        "ON org_settings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_egress_settings_audit()"
    )
    _coupling(
        "lattice_require_credential_audit",
        f"e.action IN ({_CREDENTIAL_ACTIONS}) AND e.target ->> 'provider' = NEW.provider",
        "credential change without an audit event",
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_provider_credentials_setup_audit AFTER INSERT OR UPDATE "
        "OF status ON provider_credentials DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_credential_audit()"
    )
    _coupling(
        "lattice_require_step_up_audit",
        "e.target ->> 'grant_id' = NEW.id::text AND ((TG_OP = 'INSERT' "
        "AND e.action = 'egress.step_up_completed') OR (TG_OP = 'UPDATE' "
        f"AND e.action IN ({_PROVIDER_ACTIONS}, 'egress.levels_changed')))",
        "step-up grant without an audit event",
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_step_up_grants_setup_audit AFTER INSERT OR UPDATE OF "
        "consumed_at ON step_up_grants DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION lattice_require_step_up_audit()"
    )


def _coupling(name: str, condition: str, message: str) -> None:
    """A deferred audit-coupling trigger function (0013's mechanism): a ``lattice_app``
    write commits only with a matching event in the same transaction."""
    op.execute(
        f"""
        CREATE FUNCTION {name}() RETURNS trigger
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
              AND {condition}
          ) THEN
            RAISE EXCEPTION '{message}' USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {name}() FROM PUBLIC")


def downgrade() -> None:
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    for table, trigger, function in (
        ("step_up_grants", "trg_step_up_grants_setup_audit", "lattice_require_step_up_audit"),
        (
            "provider_credentials",
            "trg_provider_credentials_setup_audit",
            "lattice_require_credential_audit",
        ),
        ("org_settings", "trg_org_settings_setup_audit", "lattice_require_egress_settings_audit"),
        ("model_providers", "trg_model_providers_setup_audit", "lattice_require_provider_audit"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
        op.execute(f"DROP FUNCTION {function}()")
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        f"TO lattice_app WITH CHECK ({_AUDIT_0020})"
    )
    for function in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION {function}")
    for function in reversed(_HELPERS):
        op.execute(f"DROP FUNCTION {function}")
    op.execute("REVOKE SELECT (step_up_acr_values) ON oidc_connections FROM lattice_app")
    op.execute(
        "REVOKE UPDATE (last_test_at, last_test_result, last_test_credential_id) "
        "ON model_providers FROM lattice_app"
    )
    op.execute("DROP TABLE step_up_grants")
    # Step-up attempts cannot be represented in 0020.
    _per_org("DELETE FROM oidc_login_attempts WHERE organization_id = org AND purpose = 'step_up';")
    op.execute(
        f"""
        ALTER TABLE oidc_login_attempts
          DROP CONSTRAINT ck_oidc_login_attempts_outcome,
          ADD CONSTRAINT ck_oidc_login_attempts_outcome CHECK (
            (outcome IS NULL AND completed_at IS NULL AND observed_identifier IS NULL)
            OR (outcome IN ({_TEST_OUTCOMES_0020}) AND completed_at IS NOT NULL
                AND (observed_identifier IS NULL
                     OR length(observed_identifier) BETWEEN 1 AND 255))),
          DROP CONSTRAINT ck_oidc_login_attempts_purpose,
          ADD CONSTRAINT ck_oidc_login_attempts_purpose CHECK (
            (purpose = 'sign_in' AND membership_id IS NULL AND proposed_issuer IS NULL
             AND proposed_subject_claim IS NULL AND outcome IS NULL
             AND observed_identifier IS NULL AND completed_at IS NULL
             AND proof_used_at IS NULL)
            OR (purpose = 'test' AND membership_id IS NOT NULL)),
          DROP CONSTRAINT fk_oidc_login_attempts_session,
          DROP COLUMN policy_version, DROP COLUMN reason, DROP COLUMN change,
          DROP COLUMN session_id
        """
    )
    op.execute("DROP TRIGGER trg_model_providers_egress_version ON model_providers")
    op.execute("DROP TRIGGER trg_org_settings_egress_version ON org_settings")
    op.execute("DROP FUNCTION lattice_bump_egress_version()")
    op.execute("DROP TABLE egress_policy_versions")
    op.execute(
        "ALTER TABLE oidc_connections DROP CONSTRAINT ck_oidc_connections_step_up_acr, "
        "DROP COLUMN step_up_acr_values"
    )
    op.execute(
        "ALTER TABLE model_providers DROP CONSTRAINT ck_model_providers_last_test, "
        "DROP COLUMN last_test_credential_id, DROP COLUMN last_test_result, "
        "DROP COLUMN last_test_at"
    )
    # A denied external class has no 0020 representation: the most restrictive level.
    _per_org(
        "UPDATE org_settings SET external_egress_level = 'internal' "
        "WHERE organization_id = org AND external_egress_level IS NULL;"
    )
    op.execute("ALTER TABLE org_settings ALTER COLUMN external_egress_level SET NOT NULL")
