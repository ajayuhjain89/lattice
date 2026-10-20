"""The purge's audit exemption holds only for the organization the session is bound to
(Phase 12, P12.8 review of 0023; phase-12-plan Part III P12.8).

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-20
Reversible: yes (the downgrade restores 0023's function body; nothing else changes)
Locks: none beyond replacing one function

0023 exempts, from 0013's access-audit coupling, the delete of a grant whose document (or
of a group membership whose group) no longer exists -- the cascade of
``lattice_purge_source``. The deferred trigger runs at commit as ``lattice_app``, and it
reads ``documents`` and ``access_groups`` through row-level security, i.e. as the
organization bound *at commit*. ``app.org_id`` is an ordinary setting any role may change,
so a session could delete one of its organization's administrator grants (or an
administrator group's member) with no ``access.*`` event, rebind to another organization
(or to none) before commit, and the still-existing parent would look deleted: the write
committed unaudited (``tests/sources/test_purge_audit.py``, the rebinding cases). Before
0023 that sequence failed closed (the event lookup sees no event either).

Now the exemption applies only to a row of the bound organization, whose documents and
groups row-level security shows in full. Every other path is 0023's: a rebound session's
delete needs its event, which it cannot see, and is refused.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
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
# 0023's exemption (a grant of a deleted document, a membership of a deleted group: the
# purge's cascade), read as {bound} -- the guard 0024 adds -- or without it (0023).
_EXEMPTION = """
            IF {bound}TG_TABLE_NAME = 'document_grants' THEN
              IF NOT EXISTS (
                SELECT 1 FROM public.documents d
                WHERE d.organization_id = (changed ->> 'organization_id')::uuid
                  AND d.id = (changed ->> 'document_id')::uuid
              ) THEN
                RETURN NULL;
              END IF;
            ELSIF {bound}TG_TABLE_NAME = 'access_group_members' THEN
              IF NOT EXISTS (
                SELECT 1 FROM public.access_groups g
                WHERE g.organization_id = (changed ->> 'organization_id')::uuid
                  AND g.id = (changed ->> 'group_id')::uuid
              ) THEN
                RETURN NULL;
              END IF;
            END IF;"""
# The parent is read through row-level security: only the bound organization's rows are
# seen in full, so only its rows may be exempted.
_BOUND = (
    "(changed ->> 'organization_id')::uuid = public.lattice_current_org_id()\n               AND "
)


def upgrade() -> None:
    op.execute(_FUNCTION.format(exemption=_EXEMPTION.format(bound=_BOUND)))


def downgrade() -> None:
    op.execute(_FUNCTION.format(exemption=_EXEMPTION.format(bound="")))
