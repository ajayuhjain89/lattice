"""Sessions record the SSO connection that issued them (D-25).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-05
Reversible: no (the downgrade drops sessions.oidc_connection_id)
Locks: ACCESS EXCLUSIVE on sessions (new nullable column, no rewrite); SHARE on sessions
  while its partial index builds

Phase 11, P11.3 (phase-11-plan §9.2; deployment.md §6).

* ``sessions.oidc_connection_id``: the OIDC connection whose sign-in created the session;
  NULL for development sign-in (and for sessions created before this revision). A
  composite foreign key keeps it inside the session's organization. ``lattice sso
  disable`` (and an identity-shape ``sso update``) revoke the connection's unrevoked
  sessions in the same transaction, found through a partial index.
* ``lattice_app`` may read ``id``, ``organization_id`` and ``status`` of its own
  organization's connections (row-level security as before) and nothing else of them --
  never the client ID, issuer or secret reference: an OIDC sign-in checks, under a shared
  advisory lock that ``sso disable`` takes exclusively, that its connection is still
  active before the session is created, so a sign-in racing a disable cannot leave a live
  session behind.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN oidc_connection_id uuid")
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_oidc_connection "
        "FOREIGN KEY (organization_id, oidc_connection_id) "
        "REFERENCES oidc_connections (organization_id, id)"
    )
    op.execute(
        "CREATE INDEX ix_sessions_oidc_connection_live ON sessions "
        "(organization_id, oidc_connection_id) WHERE revoked_at IS NULL"
    )
    op.execute("GRANT SELECT (id, organization_id, status) ON oidc_connections TO lattice_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT (id, organization_id, status) ON oidc_connections FROM lattice_app")
    op.execute("DROP INDEX ix_sessions_oidc_connection_live")
    op.execute("ALTER TABLE sessions DROP CONSTRAINT fk_sessions_oidc_connection")
    op.execute("ALTER TABLE sessions DROP COLUMN oidc_connection_id")
