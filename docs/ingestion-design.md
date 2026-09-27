# Lattice — Ingestion Design

Status: Foundation draft (Phase 0).
Related: `connector-design.md`, `data-model.md`, `retrieval-design.md`.

---

## 1. Pipeline

```
sync(source, mode)
  │
  ├─ load + validate source config (connector config_model)
  ├─ connector.check()                       → fail fast: SourceUnavailable
  ├─ open source_sync_runs row (running)
  ├─ ACL groups: connector.list_access_groups() → upsert access_groups + members (origin=source)
  │
  ├─ for each record from connector.list_records(checkpoint):
  │     1. VALIDATE      schema, limits, timestamps, URI scheme           → RecordError
  │     2. NORMALIZE     NFC, strip control chars, normalize whitespace,
  │                      markdown→plain structure, parts → canonical body
  │     3. MAP ACCESS    sensitivity, departments, account_ref→account_id,
  │                      group grants  (fail closed, §6)                  → failure: access_unmappable
  │     4. HASH          content_hash(canonical body + title + parts meta)
  │                      access_hash(sensitivity, departments, account, grants)
  │     5. DECIDE        new | content_changed | access_changed | unchanged
  │     6. CHUNK         (new/content_changed only) kind-specific chunker
  │     7. EMBED         only chunk content_hashes not already embedded for this doc
  │                      (batching, retries with backoff; AI egress policy respected)
  │     8. PERSIST       one transaction per document (§4)
  │     9. RECORD        IngestionResult → run counters; failures → sync_record_failures
  │
  ├─ DELETION SWEEP (snapshot sources, full mode, no source-level failure):
  │     unseen = active docs of source not seen in this run
  │     if |unseen| / |active| > guard (default 20%) → abort sweep, run = partial, flag
  │        (override for genuine mass deletions: `--allow-mass-deletion`, logged)
  │     else DELETE unseen documents (cascade)
  │
  └─ close run: succeeded | partial (some record failures) | failed | aborted
```

Modes: `full` (snapshot + sweep), `incremental` (checkpointed; no sweep), `acl_only`
(re-read access hints + groups; update access columns/grants; never re-chunk or embed).

## 2. Idempotency and determinism

- Idempotency key: `(organization_id, source_id, external_id)`.
- Running the same sync twice produces zero writes the second time (all
  `unchanged`); asserted by test.
- Chunk IDs are deterministic (`uuid5(document_id, chunk_content_hash, occurrence)`),
  so unchanged chunks keep IDs across content updates of their document. Citations in
  recent traces still resolve if the passage they cite was not edited.
- Chunkers are pure functions of (kind, canonical parts, chunker_version). Changing
  the chunker bumps `chunker_version` and triggers a controlled re-chunk via
  `lattice reindex`.

## 3. Chunking (by kind)

Target: 250–450 tokens per chunk, hard max 600; never split mid-sentence when
avoidable. Every chunk carries a short **contextual header used for embedding and
lexical title weighting, not stored in `text`**:
`"{kind} · {title} · {account name} · {heading path} · {occurred_at date}"`.
This lets "What did we promise Acme?" match a comment that never says "Acme".
Open decision D-10 (implementation-plan §7): whether the account name belongs in the
embedded header and in the shared `tsv`, given D-8 (a grant does not disclose the
account) — the header is computed once per document, not per reader.

| Kind | Strategy | `occurred_at` | `author_display` |
| ---- | -------- | ------------- | ---------------- |
| `doc` | Split by headings, then paragraphs; pack paragraphs up to target; no overlap (header supplies context) | Section date if present, else doc `updated_at` | Doc author |
| `ticket` | Header part (subject/status/priority/requester) as one chunk; each comment its own chunk; long comments split by paragraph | Comment `created_at` | Comment author |
| `chat_thread` | Root + replies packed chronologically into windows up to target; a window never spans > 48 h | Last message in window | Speakers joined ("Tom, Sofia") |
| `chat_channel_day` | Messages packed chronologically; split on > 2 h gaps | Last message in window | Speakers |

Per-part timestamps are preserved, so freshness is judged on the evidence actually
cited (a ticket opened in March with a comment yesterday is fresh).

Header staleness: the contextual header includes the account name. If a document's
account changes (an access change) or an account is renamed, **authorization is
updated immediately** via the access columns, but the header text in `tsv` and the
embedding is refreshed by a follow-up `reindex` of the affected documents. This is a
ranking-quality lag, never an access lag.

## 4. Persistence transaction (per document)

```sql
BEGIN;
  SET LOCAL app.org_id = :org;
  INSERT … ON CONFLICT (organization_id, source_id, external_id) DO UPDATE …  -- documents
  DELETE FROM chunks WHERE document_id = :doc AND id <> ALL(:kept_chunk_ids);
  INSERT INTO chunks … ON CONFLICT (id) DO UPDATE SET ordinal, char_start, char_end, …;
  -- replace source-origin grants; admin-origin grants untouched
  DELETE FROM document_grants WHERE document_id = :doc AND origin = 'source';
  INSERT INTO document_grants …;
COMMIT;
```

Retrieval therefore sees either the old document or the new one, never a mix.
Embedding happens **before** the transaction (network calls are not held inside DB
transactions); if embedding fails, nothing is written for that document and the old
version stays searchable. Result: `failed(embed)`, retried next sync.

## 5. Updates, versions and supersession

- Content change → `version += 1`, chunks replaced as above, `ingested_at` updated.
- Only the current version is stored. Version history stays in the source of truth
  (non-goal: Lattice as an archive). Rationale: storing old versions would make old
  content retrievable after the source changed or deleted it.
- Explicit supersession (`supersedes_external_id`, e.g. "Refund policy v2 replaces
  v1") sets `supersedes_document_id`. The superseded document remains retrievable
  (the old policy may be what a customer was told) but answering marks it as
  superseded (retrieval-design §7).

## 6. Fail-closed access mapping

| Situation | Outcome |
| --------- | ------- |
| No sensitivity in hints and no source default | Impossible: `default_sensitivity` is required on sources |
| Unknown department key | Record fails: `access_unmappable:department` |
| `account_scoped` source and account_ref unmapped | Record fails: `access_unmappable:account` (not indexed as unscoped) |
| Group ref not declared by the connector | Record fails: `access_unmappable:group` |
| Private channel with zero mappable members | Indexed as `grants_only` with an empty group → readable by nobody until members map; reported as warning |
| `sensitivity = restricted` without `grants_only` | Forced to `grants_only` (restricted is always grant-only); DB CHECK backs this |

## 7. Embedding

- `EmbeddingProvider` Protocol: `embed(texts: list[str], purpose: Literal["document","query"]) -> list[vector]`, `model_id`, `dimensions`.
- Batching (provider-specific max), exponential backoff on rate limits, per-sync
  budget cap.
- Reuse: before calling the provider, look up existing chunks of the same document
  with identical `content_hash` and `embedding_model`; reuse their vectors. Typical
  ticket updates (one new comment) embed one chunk, not the whole ticket.
- AI egress policy (`security-model.md` §6): documents above the embedding
  provider's effective level — by default anything `confidential` or `restricted` —
  are not sent to it. They are lexical-only (embedding NULL); retrieval excludes
  NULL embeddings from the semantic path only, so they remain findable by keywords
  and identifiers. If the org later opts in (or configures a private embedding
  endpoint approved for that level), `lattice reindex --reembed --sensitivity
  confidential` fills the embeddings. Lowering the level deletes the affected
  embeddings in the same operation.
- Tests use `FakeEmbeddingProvider` (deterministic hashed bag-of-words vectors) — no
  network in CI.

## 8. Failure reporting (operator-visible)

Every sync produces a `source_sync_runs` row with counts and status, and a
`sync_record_failures` row per failed record with stage, code and templated
parameters (no free-text messages). The Sources page (Phase 8) shows admins: last sync
time and status, aggregate counts, top failure codes, deletion-guard trips, and
"permissions last synced". Failure rows show a record's filename/title/external ID
only if the admin can read that document; otherwise an opaque reference
(`security-model.md` §10). Members see only sources they can read, with counts
computed inside their authorization predicate. Nothing reports `succeeded` if
any record failed — that is `partial`.

## 9. Operations

- `lattice sync <source-key> [--mode full|incremental|acl_only] [--dry-run]`
  (`--dry-run` reports decisions without writing: useful before first sync of a new
  source).
- `lattice reindex <source-key> [--rechunk] [--reembed]`.
- `lattice source disconnect <source-key>` → immediate soft delete, async purge.
- Performance target (synthetic dataset, ~2k records, ~15k chunks): full first sync
  < 5 min with a remote embedding provider; no-op re-sync < 30 s.
- Concurrency: one sync per source at a time (advisory lock on source_id). The API
  keeps serving during syncs; per-document transactions keep locks short.
