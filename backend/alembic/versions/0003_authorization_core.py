"""Authorization core: access groups, the document access record, document grants.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27
Reversible: no (the downgrade drops the authorization tables and their rows)
Locks: new tables only

Phase 2 (docs/implementation-plan.md): the tables the authorization policy reads
(docs/authorization-model.md §3-4, docs/data-model.md §2, §4, §5). There is no content
yet: ``documents`` carries only its identity, organization, lifecycle status and access
columns. Phase 4 adds source, content and provenance columns.

Every table is tenant-owned, has forced row-level security with the tenant policy, and
references other tenant rows only through composite ``(organization_id, …)`` foreign
keys, so a row in one organization can never point at a row in another. Where a
foreign key is impossible (``documents.department_ids`` is an array), a trigger
enforces the same rule.

``document_grants`` deviates from the draft's polymorphic ``(principal_type,
principal_id)``: a polymorphic ID cannot carry a foreign key, so a grant could name a
membership, department or group of *another* organization, or keep naming one that was
deleted. Instead each principal kind has its own nullable column with a composite
foreign key, and a CHECK ties ``principal_type`` to exactly one of them.

The runtime role gets SELECT only: nothing writes these tables at runtime in Phase 2
(writers arrive with ingestion in Phase 4 and access management in Phase 9).
lattice_auth gets nothing.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("access_groups", "access_group_members", "documents", "document_grants")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE access_groups (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          origin text NOT NULL,
          external_ref text,
          name text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_access_groups PRIMARY KEY (id),
          CONSTRAINT fk_access_groups_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          CONSTRAINT uq_access_groups_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT ck_access_groups_origin CHECK (origin IN ('admin', 'source')),
          -- Source-synced groups mirror an external access set (e.g. a Slack channel).
          CONSTRAINT ck_access_groups_external_ref_iff_source
            CHECK ((origin = 'source') = (external_ref IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE access_group_members (
          organization_id uuid NOT NULL,
          group_id uuid NOT NULL,
          membership_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_access_group_members PRIMARY KEY (group_id, membership_id),
          CONSTRAINT fk_access_group_members_organization_id_group_id_access_groups
            FOREIGN KEY (organization_id, group_id)
            REFERENCES access_groups (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_access_group_members_organization_id_membership_id_memberships
            FOREIGN KEY (organization_id, membership_id)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE
        )
        """
    )
    # Resolving a member's groups (every request) and cascading membership deletes.
    op.execute(
        "CREATE INDEX ix_access_group_members_organization_id_membership_id "
        "ON access_group_members (organization_id, membership_id)"
    )

    op.execute(
        """
        CREATE TABLE documents (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          account_id uuid,
          sensitivity text NOT NULL,
          access_mode text NOT NULL,
          department_ids uuid[] NOT NULL DEFAULT '{}',
          status text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_documents PRIMARY KEY (id),
          CONSTRAINT fk_documents_organization_id_organizations
            FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
          -- NO ACTION, deliberately: deleting an account must not silently turn its
          -- documents into non-account-specific ones (SET NULL would widen access).
          CONSTRAINT fk_documents_organization_id_account_id_accounts
            FOREIGN KEY (organization_id, account_id) REFERENCES accounts (organization_id, id),
          CONSTRAINT uq_documents_organization_id_id UNIQUE (organization_id, id),
          CONSTRAINT ck_documents_sensitivity
            CHECK (sensitivity IN ('internal', 'confidential', 'restricted')),
          CONSTRAINT ck_documents_access_mode CHECK (access_mode IN ('policy', 'grants_only')),
          -- Restricted content is reachable only through explicit grants.
          CONSTRAINT ck_documents_restricted_is_grants_only
            CHECK (sensitivity <> 'restricted' OR access_mode = 'grants_only'),
          CONSTRAINT ck_documents_department_ids_no_nulls
            CHECK (array_position(department_ids, NULL) IS NULL),
          CONSTRAINT ck_documents_status CHECK (status IN ('active', 'deleted'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_documents_organization_id_account_id "
        "ON documents (organization_id, account_id)"
    )
    op.execute("CREATE INDEX ix_documents_department_ids ON documents USING gin (department_ids)")
    # Arrays cannot carry foreign keys. Every department named by a document must be a
    # department of the document's own organization when it is written. (A department
    # deleted later leaves a dangling ID, which only makes the document less visible:
    # no member can hold a department that does not exist.) The explicit
    # organization_id filter keeps this correct even for roles that bypass RLS.
    op.execute(
        """
        CREATE FUNCTION lattice_check_document_departments() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        BEGIN
          IF (SELECT count(*) FROM public.departments d
              WHERE d.organization_id = NEW.organization_id AND d.id = ANY (NEW.department_ids))
             <> (SELECT count(DISTINCT x) FROM unnest(NEW.department_ids) AS x) THEN
            RAISE EXCEPTION 'documents.department_ids must reference departments of the '
                            'document''s organization'
              USING ERRCODE = 'foreign_key_violation',
                    CONSTRAINT = 'fk_documents_department_ids_departments';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_documents_department_ids
        BEFORE INSERT OR UPDATE OF organization_id, department_ids ON documents
        FOR EACH ROW EXECUTE FUNCTION lattice_check_document_departments()
        """
    )

    op.execute(
        """
        CREATE TABLE document_grants (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL,
          document_id uuid NOT NULL,
          principal_type text NOT NULL,
          membership_id uuid,
          department_id uuid,
          group_id uuid,
          origin text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_document_grants PRIMARY KEY (id),
          CONSTRAINT fk_document_grants_organization_id_document_id_documents
            FOREIGN KEY (organization_id, document_id)
            REFERENCES documents (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_document_grants_organization_id_membership_id_memberships
            FOREIGN KEY (organization_id, membership_id)
            REFERENCES memberships (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_document_grants_organization_id_department_id_departments
            FOREIGN KEY (organization_id, department_id)
            REFERENCES departments (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_document_grants_organization_id_group_id_access_groups
            FOREIGN KEY (organization_id, group_id)
            REFERENCES access_groups (organization_id, id) ON DELETE CASCADE,
          CONSTRAINT uq_document_grants_document_id_membership_id
            UNIQUE (document_id, membership_id),
          CONSTRAINT uq_document_grants_document_id_department_id
            UNIQUE (document_id, department_id),
          CONSTRAINT uq_document_grants_document_id_group_id UNIQUE (document_id, group_id),
          CONSTRAINT ck_document_grants_origin CHECK (origin IN ('admin', 'source')),
          -- principal_type names exactly one principal column, and only that one is set.
          CONSTRAINT ck_document_grants_one_principal CHECK (
            (principal_type = 'membership' AND membership_id IS NOT NULL
               AND department_id IS NULL AND group_id IS NULL)
            OR (principal_type = 'department' AND department_id IS NOT NULL
               AND membership_id IS NULL AND group_id IS NULL)
            OR (principal_type = 'group' AND group_id IS NOT NULL
               AND membership_id IS NULL AND department_id IS NULL)
          )
        )
        """
    )
    for column in ("membership_id", "department_id", "group_id"):
        op.execute(
            f"CREATE INDEX ix_document_grants_organization_id_{column} "
            f"ON document_grants (organization_id, {column})"
        )

    for table in ("access_groups", "documents"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION lattice_set_updated_at()"
        )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organization_id = lattice_current_org_id()) "
            "WITH CHECK (organization_id = lattice_current_org_id())"
        )

    op.execute(
        "GRANT SELECT ON access_groups, access_group_members, documents, document_grants "
        "TO lattice_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS document_grants, documents, access_group_members, access_groups"
    )
    op.execute("DROP FUNCTION IF EXISTS lattice_check_document_departments()")
