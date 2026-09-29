"""Query traces record the accounts their stored answer depends on.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-29

Phase 6 closure (docs/security-model.md §8, §10; D-8).

* ``query_traces.answer_account_ids``: the accounts whose structured metadata the
  stored answer may carry -- the clarification choices built from visible account
  mentions, and the accounts of the evidence placed in the prompt (their ``account``
  attribute is present only when visible). Reading a trace re-checks each one against
  the viewer's current account visibility and hides the answer if any is gone. NULL
  (rows written before this revision) means the dependencies are unknown: the answer is
  hidden.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE query_traces ADD COLUMN answer_account_ids uuid[]")


def downgrade() -> None:
    op.execute("ALTER TABLE query_traces DROP COLUMN answer_account_ids")
