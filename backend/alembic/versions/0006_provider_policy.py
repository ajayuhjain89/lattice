"""Provider policy: local deployments, question sensitivity (D-13) and data origin.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28
Reversible: no (the downgrade drops columns and local provider records)
Locks: ACCESS EXCLUSIVE on model_providers and org_settings

Phase 5 closure (docs/security-model.md §6, ADR-009, ADR-010).

* ``model_providers.deployment`` gains ``local``: inference inside the Lattice
  deployment (ADR-009). A local provider is not governed by the organization's
  *external* egress level -- nothing leaves the deployment -- but only by its own
  approval record, which still defaults to ``internal``.
* ``org_settings.default_query_sensitivity`` (D-13): the trusted minimum sensitivity
  of every question in the organization. A caller's declaration can only raise it.
* ``org_settings.data_origin``: ``customer`` (default) or ``synthetic``. A provider
  whose terms let it use submitted content (``training_opt_out = false``, e.g. the
  Gemini API's unpaid tier, ADR-010) may receive content only from a ``synthetic``
  organization. The default is the safe one: a real organization never sends anything
  to such a provider.

No existing row changes meaning: the new columns default to the behaviour before this
revision (questions ``internal``; no organization is synthetic until the seed says so).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEVELS = "('internal', 'confidential', 'restricted')"


def upgrade() -> None:
    op.execute("ALTER TABLE model_providers DROP CONSTRAINT ck_model_providers_deployment")
    op.execute(
        "ALTER TABLE model_providers ADD CONSTRAINT ck_model_providers_deployment "
        "CHECK (deployment IN ('external_api', 'private', 'local'))"
    )
    op.execute(
        f"""
        ALTER TABLE org_settings
          ADD COLUMN default_query_sensitivity text NOT NULL DEFAULT 'internal',
          ADD COLUMN data_origin text NOT NULL DEFAULT 'customer',
          ADD CONSTRAINT ck_org_settings_default_query_sensitivity
            CHECK (default_query_sensitivity IN {_LEVELS}),
          ADD CONSTRAINT ck_org_settings_data_origin
            CHECK (data_origin IN ('customer', 'synthetic'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE org_settings
          DROP COLUMN default_query_sensitivity,
          DROP COLUMN data_origin
        """
    )
    # Local provider records cannot be represented before this revision.
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            DELETE FROM model_providers WHERE deployment = 'local';
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )
    op.execute("ALTER TABLE model_providers DROP CONSTRAINT ck_model_providers_deployment")
    op.execute(
        "ALTER TABLE model_providers ADD CONSTRAINT ck_model_providers_deployment "
        "CHECK (deployment IN ('external_api', 'private'))"
    )
