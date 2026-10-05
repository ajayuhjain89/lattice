"""Deployment drill revision (deploy/drills; the local reference stack only, never released).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-05
Reversible: @REVERSIBLE@
Locks: none (no DDL)

Changes no object -- so the migrated schema still matches the models and the privilege
snapshot -- but takes a visible two seconds, so the stop-the-world interval is measured
with a real migration in it. The ``failing-migration`` drill makes it fail after that.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

FAIL = False


def upgrade() -> None:
    op.execute("SELECT pg_sleep(2)")
    if FAIL:
        op.execute("SELECT 1 / 0")


def downgrade() -> None:
    pass
