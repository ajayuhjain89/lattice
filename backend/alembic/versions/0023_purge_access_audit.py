"""A source's purge may remove the grants and group memberships of what it deletes
(Phase 12, P12.7 fix; phase-12-plan Part III P12.7).

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-19
Reversible: yes (the downgrade restores 0013's function body; nothing else changes)
Locks: none beyond replacing one function

Found by the P12.7 onboarding journey: the product's *Disconnect* (``lattice_purge_source``,
0022) failed at COMMIT for every source whose documents carry source-origin grants -- a
Slack export's private channels, a Markdown document's ``grants:`` -- and left the source
``deleting``. 0013's deferred access-audit trigger requires, for each ``lattice_app`` write
to ``document_grants`` and ``access_group_members``, an ``access.*`` event naming the row's
membership; the purge removes them by the foreign-key cascade from ``sources`` (documents,
synced groups), and a group or department grant names no membership, so no event can ever
satisfy it. P12.6's tests disconnected only a Tickets source (no grants).

The exemption is exactly the purge's cascade: a ``DELETE`` of a grant whose document, or
of a group membership whose group, no longer exists in the transaction. ``lattice_app``
has no privilege to delete documents or groups; only ``lattice_purge_source`` (0022) can,
and only for a source already ``deleting`` -- recorded by ``source.disconnected`` with its
counts. Everything else is unchanged: any other grant or membership write by the API role
still needs its ``access.*`` event, and the members who lose a synced group are still
audited one by one -- the purge bumps their ``memberships`` rows, which keep the
requirement, and the product records ``access.member_changed`` for each
(``lattice.sources.admin.disconnect``).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = """
        CREATE OR REPLACE FUNCTION lattice_require_access_audit() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
          changed jsonb;
          member text;
        BEGIN
          IF current_user <> 'lattice_app' THEN
            RETURN NULL;
          END IF;
          IF TG_OP = 'DELETE' THEN
            changed := to_jsonb(OLD);{exemption}
          ELSE
            changed := to_jsonb(NEW);
          END IF;
          member := changed ->> TG_ARGV[0];
          IF member IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.audit_events e
            WHERE e.txid = pg_current_xact_id()
              AND e.organization_id = (changed ->> 'organization_id')::uuid
              AND e.action LIKE 'access.%'
              AND e.target ->> 'membership_id' = member
          ) THEN
            RAISE EXCEPTION 'access change on % without an audit event', TG_TABLE_NAME
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NULL;
        END
        $$
"""
# A grant of a deleted document, or a membership of a deleted group: the purge's cascade
# (the API role cannot delete either parent; the trigger runs as the writer, so these
# reads are confined to its organization by row-level security).
_EXEMPTION = """
            IF TG_TABLE_NAME = 'document_grants' THEN
              IF NOT EXISTS (
                SELECT 1 FROM public.documents d
                WHERE d.organization_id = (changed ->> 'organization_id')::uuid
                  AND d.id = (changed ->> 'document_id')::uuid
              ) THEN
                RETURN NULL;
              END IF;
            ELSIF TG_TABLE_NAME = 'access_group_members' THEN
              IF NOT EXISTS (
                SELECT 1 FROM public.access_groups g
                WHERE g.organization_id = (changed ->> 'organization_id')::uuid
                  AND g.id = (changed ->> 'group_id')::uuid
              ) THEN
                RETURN NULL;
              END IF;
            END IF;"""


def upgrade() -> None:
    op.execute(_FUNCTION.format(exemption=_EXEMPTION))


def downgrade() -> None:
    op.execute(_FUNCTION.format(exemption=""))
