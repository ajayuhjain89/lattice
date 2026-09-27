"""Least privilege for the pre-authentication lookups and their owner, lattice_auth.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27

Brings databases migrated with the original 0001 (and bootstrapped by the original
bootstrap) to the steady state that the current 0001 and bootstrap produce directly.
Two corrections:

1. lattice_auth no longer holds CREATE on the public schema. Only the ownership
   transfer needs it, and 0001 now grants and revokes it inside its own transaction.
2. EXECUTE on the two lookup functions belongs to lattice_app only. The original 0001
   issued its REVOKE FROM PUBLIC / GRANT TO lattice_app after handing the functions to
   lattice_auth; lattice_owner was then no longer the owner, so PostgreSQL reduced both
   statements to warnings and the default PUBLIC EXECUTE remained. The ACL can now be
   changed only by the owner, so this migration briefly acts as lattice_auth (the
   bootstrap lets lattice_owner SET ROLE to it) for exactly these statements.

On a database created with the current 0001 and bootstrap every statement is a no-op.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTIONS = (
    "lattice_resolve_session(bytea)",
    "lattice_identity_memberships(text, text)",
)


def upgrade() -> None:
    op.execute("REVOKE CREATE ON SCHEMA public FROM lattice_auth")
    op.execute("SET LOCAL ROLE lattice_auth")
    for function in _FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO lattice_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    # Deliberately restores neither the schema CREATE privilege nor PUBLIC EXECUTE:
    # no revision needs them.
    pass
