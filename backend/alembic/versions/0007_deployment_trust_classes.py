"""Deployment trust classes (D-15): ``private`` becomes ``organization_controlled``.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-29

Final Phase 5 closure (docs/security-model.md §6, ADR-011).

* ``model_providers.deployment`` takes the explicit trust classes ``local``,
  ``organization_controlled`` and ``external_api``. The ambiguous label ``private``
  is gone: an existing ``private`` record is kept and becomes
  ``organization_controlled``, which sends nothing until an administrator both allows
  the class for the organization and approves the record explicitly (below). Any other
  value is rejected by the constraint, and the code treats anything unrecognised as
  ``unknown`` (nothing is sent).
* ``model_providers.approved_at``: set by an explicit administrator approval
  (``lattice provider approve``). An ``organization_controlled`` record needs it; a
  ``local`` or ``external_api`` record keeps the default approval of its first use
  (``internal``), as before.
* ``org_settings.organization_controlled_level``: the organization's ceiling for
  ``organization_controlled`` inference. NULL (the default) means the class is not
  allowed.

``local`` and ``external_api`` records change neither value nor meaning.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEVELS = "('internal', 'confidential', 'restricted')"


def _relabel(old: str, new: str) -> None:
    # model_providers forces row-level security: relabel organization by organization.
    # ``old`` and ``new`` are the fixed labels below, never input.
    op.execute(
        f"""
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            UPDATE model_providers SET deployment = '{new}' WHERE deployment = '{old}';
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """  # noqa: S608
    )


def upgrade() -> None:
    op.execute("ALTER TABLE model_providers DROP CONSTRAINT ck_model_providers_deployment")
    _relabel("private", "organization_controlled")
    op.execute(
        """
        ALTER TABLE model_providers
          ADD COLUMN approved_at timestamptz,
          ADD CONSTRAINT ck_model_providers_deployment
            CHECK (deployment IN ('local', 'organization_controlled', 'external_api'))
        """
    )
    op.execute(
        f"""
        ALTER TABLE org_settings
          ADD COLUMN organization_controlled_level text,
          ADD CONSTRAINT ck_org_settings_organization_controlled_level
            CHECK (organization_controlled_level IN {_LEVELS})
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE org_settings DROP COLUMN organization_controlled_level")
    op.execute("ALTER TABLE model_providers DROP CONSTRAINT ck_model_providers_deployment")
    op.execute("ALTER TABLE model_providers DROP COLUMN approved_at")
    _relabel("organization_controlled", "private")
    op.execute(
        "ALTER TABLE model_providers ADD CONSTRAINT ck_model_providers_deployment "
        "CHECK (deployment IN ('external_api', 'private', 'local'))"
    )
