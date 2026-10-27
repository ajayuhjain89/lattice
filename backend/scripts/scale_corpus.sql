-- Multiply an ISOLATED evaluation database's corpus for scale measurements (F3,
-- docs/phase-13-followups.md). Superuser only; never the development database.
--   psql -U postgres -d lattice -v ON_ERROR_STOP=1 -v start=1 -v stop=9 < scale_corpus.sql
-- adds copies start..stop of every original document (external_id without '~'), its
-- chunks and grants, with new IDs; display references get a '-n' suffix and
-- supersession links are dropped on copies.
SET session_replication_role = replica;
CREATE TEMP TABLE m AS
  SELECT d.id AS old, gen_random_uuid() AS new, g.n
  FROM (SELECT * FROM documents WHERE external_id NOT LIKE '%~%') d CROSS JOIN generate_series(:start, :stop) AS g(n);
INSERT INTO documents
  SELECT (jsonb_populate_record(d, jsonb_build_object(
            'id', m.new,
            'external_id', d.external_id || '~' || m.n,
            'display_ref', CASE WHEN d.display_ref IS NULL THEN NULL ELSE d.display_ref || '-' || m.n END,
            'supersedes_document_id', NULL))).*
  FROM documents d JOIN m ON m.old = d.id;
INSERT INTO chunks
  SELECT (jsonb_populate_record(c, jsonb_build_object('id', gen_random_uuid(), 'document_id', m.new))).*
  FROM chunks c JOIN m ON m.old = c.document_id;
INSERT INTO document_grants
  SELECT (jsonb_populate_record(g, jsonb_build_object('id', gen_random_uuid(), 'document_id', m.new))).*
  FROM document_grants g JOIN m ON m.old = g.document_id;
ANALYZE documents; ANALYZE chunks; ANALYZE document_grants;
