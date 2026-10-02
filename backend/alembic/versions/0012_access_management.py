"""Access management: the API's administrative writes and the audit view.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-02

Phase 9 (implementation-plan §4 "Phase 9"; security-model §8, §10; threat model T3).

Until now ``lattice_app`` only *read* authorization state. Access management needs a
few writes; each is granted at the narrowest grain PostgreSQL offers and fenced by a
restrictive row-level policy (targeted at ``lattice_app`` only, so the owner's and the
worker's own policies are unchanged):

* ``memberships``: UPDATE of ``status``, ``clearance``, ``account_scope``,
  ``authz_updated_at`` and ``updated_at`` only -- never ``is_admin``, ``role_label``,
  the user or the organization. ``app_member_status``: a removed membership cannot be
  touched, and the API can set only ``active`` or ``suspended`` (removal is
  deprovisioning, not an access change).
* ``account_assignments`` and ``membership_departments``: INSERT and DELETE.
* ``access_group_members``: INSERT and DELETE, only for administrator-defined groups
  (``app_admin_groups_only``): a source-synced group mirrors the source and is replaced
  by every sync.
* ``document_grants``: INSERT and DELETE of administrator membership grants only
  (``app_admin_membership_grants``): source grants belong to the connector (D-12).
* ``audit_events``: SELECT (the administrators' audit view) and INSERT of ``access.*``
  events attributed to a member (``app_access_events``); the provider events stay
  operator-only. Still append-only for everyone (the 0008 trigger).

The API still cannot write documents, chunks, sources or settings, and still has no
DELETE on memberships, accounts, departments or groups. Plus an index for the audit
view's keyset pages.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADMIN_GROUP = (
    "EXISTS (SELECT 1 FROM access_groups g WHERE g.organization_id = "
    "access_group_members.organization_id AND g.id = access_group_members.group_id "
    "AND g.origin = 'admin')"
)
_ADMIN_MEMBERSHIP_GRANT = "(origin = 'admin' AND principal_type = 'membership')"


def upgrade() -> None:
    op.execute(
        "GRANT UPDATE (status, clearance, account_scope, authz_updated_at, updated_at) "
        "ON memberships TO lattice_app"
    )
    op.execute(
        "CREATE POLICY app_member_status ON memberships AS RESTRICTIVE FOR UPDATE "
        "TO lattice_app USING (status <> 'removed') "
        "WITH CHECK (status IN ('active', 'suspended'))"
    )
    for table in ("account_assignments", "membership_departments", "access_group_members"):
        op.execute(f"GRANT INSERT, DELETE ON {table} TO lattice_app")
    op.execute(
        "CREATE POLICY app_admin_groups_only_insert ON access_group_members AS RESTRICTIVE "
        f"FOR INSERT TO lattice_app WITH CHECK ({_ADMIN_GROUP})"
    )
    op.execute(
        "CREATE POLICY app_admin_groups_only_delete ON access_group_members AS RESTRICTIVE "
        f"FOR DELETE TO lattice_app USING ({_ADMIN_GROUP})"
    )
    op.execute("GRANT INSERT, DELETE ON document_grants TO lattice_app")
    op.execute(
        "CREATE POLICY app_admin_membership_grants_insert ON document_grants AS RESTRICTIVE "
        f"FOR INSERT TO lattice_app WITH CHECK {_ADMIN_MEMBERSHIP_GRANT}"
    )
    op.execute(
        "CREATE POLICY app_admin_membership_grants_delete ON document_grants AS RESTRICTIVE "
        f"FOR DELETE TO lattice_app USING {_ADMIN_MEMBERSHIP_GRANT}"
    )
    op.execute("GRANT SELECT, INSERT ON audit_events TO lattice_app")
    op.execute(
        "CREATE POLICY app_access_events ON audit_events AS RESTRICTIVE FOR INSERT "
        "TO lattice_app WITH CHECK (action LIKE 'access.%' AND actor LIKE 'member:%')"
    )
    op.execute(
        "CREATE INDEX ix_audit_events_organization_id_created_at "
        "ON audit_events (organization_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_audit_events_organization_id_created_at")
    op.execute("DROP POLICY app_access_events ON audit_events")
    op.execute("REVOKE ALL ON audit_events FROM lattice_app")
    for command in ("insert", "delete"):
        op.execute(f"DROP POLICY app_admin_membership_grants_{command} ON document_grants")
        op.execute(f"DROP POLICY app_admin_groups_only_{command} ON access_group_members")
    op.execute("REVOKE INSERT, DELETE ON document_grants FROM lattice_app")
    for table in ("account_assignments", "membership_departments", "access_group_members"):
        op.execute(f"REVOKE INSERT, DELETE ON {table} FROM lattice_app")
    op.execute("DROP POLICY app_member_status ON memberships")
    op.execute("REVOKE UPDATE ON memberships FROM lattice_app")
