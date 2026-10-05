"""Operations: the runtime role may read the schema revision.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-29
Reversible: yes (the downgrade only revokes one grant)
Locks: brief lock on alembic_version (GRANT)

Phase 8. The API refuses to start, and reports not ready, unless the database is at the
revision its code expects (``lattice.db.schema``). ``alembic_version`` holds one row,
the revision identifier -- no tenant data -- so the runtime role gets SELECT on it and
nothing else. It stays exempt from row-level security.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON alembic_version TO lattice_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON alembic_version FROM lattice_app")
