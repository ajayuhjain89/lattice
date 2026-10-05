"""Stable organization-scoped key for access groups (decision D-9).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27
Reversible: no (the downgrade drops access_groups.key)
Locks: ACCESS EXCLUSIVE on access_groups (column, backfill, constraints)

Synthetic manifests (Phase 3) and future source synchronization (Phase 4) must refer
to a group deterministically, as departments and accounts already are (by ``key``).
``access_groups.key`` is that identity:

* stable: renaming a group changes ``name`` only; a different key is a different
  group (an identity/configuration change, never a cosmetic rename);
* human-readable, in the same format as organization slugs (lowercase letters, digits
  and hyphens, at most 63 characters), checked on the text value because citext
  regular-expression matching ignores case;
* unique per organization, so two organizations may use the same key.

The key is an identifier, not an authorization input: the policy grants by group ID
and never reads the key.

Existing groups are backfilled with ``group-<id>``. Tenant rows are only visible with
an organization bound (FORCE ROW LEVEL SECURITY applies to the owner too), so the
backfill binds each organization in turn.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE access_groups ADD COLUMN key citext")
    op.execute(
        """
        DO $$
        DECLARE
          org uuid;
        BEGIN
          FOR org IN SELECT id FROM organizations LOOP
            PERFORM set_config('app.org_id', org::text, true);
            UPDATE access_groups SET key = 'group-' || id::text WHERE key IS NULL;
          END LOOP;
          PERFORM set_config('app.org_id', '', true);
        END
        $$
        """
    )
    op.execute("ALTER TABLE access_groups ALTER COLUMN key SET NOT NULL")
    op.execute(
        "ALTER TABLE access_groups ADD CONSTRAINT uq_access_groups_organization_id_key "
        "UNIQUE (organization_id, key)"
    )
    op.execute(
        "ALTER TABLE access_groups ADD CONSTRAINT ck_access_groups_key_format "
        "CHECK (key::text ~ '^[a-z0-9][a-z0-9-]{0,62}$')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE access_groups DROP CONSTRAINT ck_access_groups_key_format")
    op.execute("ALTER TABLE access_groups DROP CONSTRAINT uq_access_groups_organization_id_key")
    op.execute("ALTER TABLE access_groups DROP COLUMN key")
