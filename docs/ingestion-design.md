# Lattice — Ingestion Design

Status: **Implemented (Phase 4)** — `backend/src/lattice/ingestion/`, migration
`0005_ingestion.py`; tests in `backend/tests/ingestion/`. Deviations from the Phase 0
draft are listed in `implementation-plan.md` (Phase 4, as built).
Related: `connector-design.md`, `data-model.md`, `retrieval-design.md`.

---

## 1. Pipeline

```
lattice sync --org SLUG --sources FILE [--source KEY] [--mode full|acl_only] [--dry-run]
  │
  ├─ load + validate sources.yaml (lattice.sources.config); check its account and
  │  department keys against the organization          → source_config:* (nothing written)
  ├─ upsert the source row (refuses a changed connector type or a `deleting` source)
  ├─ advisory lock: one sync per source at a time      → source_busy
  ├─ open source_sync_runs row (running)
  ├─ connector.check()                                 → SourceUnavailable: run failed, no sweep
  ├─ egress level = min(org_settings level, provider's approved level); first use of a
  │  provider records it in model_providers, approved for `internal`
  ├─ ACL groups: connector.access_groups() → upsert source groups + members by email
  │
  ├─ for each batch of records from connector.records():
  │     1. VALIDATE      the connector's typed models (RecordError → failure row)
  │     2. NORMALIZE     NFC, LF, no control chars, whitespace; parts → canonical body
  │                      with exact part spans (lattice.ingestion.canonical)
  │     3. MAP ACCESS    sensitivity, access mode, departments, account, source grants
  │                      (fail closed, §6)                    → access_unmappable:<what>
  │     4. HASH          content_hash (content only), access_hash (access only)
  │     5. DECIDE        create | update | acl | unchanged (| skip in acl_only)
  │     6. CHUNK         create/update only, kind-specific (§3)
  │     7. EMBED         outside any transaction: reuse vectors whose input hash is
  │                      unchanged; embed the rest if the egress policy allows (§7)
  │     8. PERSIST       one transaction per document (§4)
  │     9. RECORD        counts on the run; failures → sync_record_failures (§8)
  │
  ├─ DELETION SWEEP (full mode, snapshot connectors only; §5)
  ├─ supersession links resolved; stale source groups removed (full mode, no guard trip)
  ├─ embedding reconciliation: remove vectors the policy no longer allows; fill
  │  pending/failed/newly eligible chunks
  └─ close run: succeeded | partial (any record or embedding failure, or a guard trip)
     | failed (source-level); sources.last_synced_at / last_succeeded_at / acl_synced_at
```

Modes:

- `full` — snapshot sync: create, update, access-update, quarantine, delete (guarded).
- `acl_only` — re-read access hints and groups; update access columns and source grants
  of existing documents only; never re-chunk or delete, and content changes are ignored.
  New records are skipped (counted) until the next full sync. Vectors change only where
  a new classification changes what the egress policy allows (removed at once when
  raised above the level; filled when lowered back).
- `reindex` (`lattice reindex`) — `full` without the deletion sweep, optionally
  re-chunking every document (`--rechunk`, after a chunker change) and re-embedding
  every eligible chunk (`--reembed`, after a provider, model or template change).
- `--dry-run` — runs everything in a read-only snapshot, makes no embedding call, and
  reports what a real run would do (`would create/update/quarantine/delete`).

`incremental` mode is not built: no connector supports checkpoints yet.

Ingestion is an operator tool. It connects as `lattice_owner` and writes **through**
row-level security, one organization bound per transaction, exactly like the seed. The
runtime role (`lattice_app`) has SELECT only on the ingestion tables.

## 2. Idempotency and determinism

- Idempotency key: `(organization_id, source_id, external_id)`.
- A second sync of unchanged input writes nothing: no document, chunk, grant, group,
  membership or provider row changes — only the run row and the source's timestamps
  (asserted with row versions of every table in `tests/ingestion/test_sync.py`).
- Document IDs are `uuid5(source_id/external_id)`; chunk IDs are
  `uuid5(document_id, chunk content hash, occurrence)`, so an unchanged passage keeps its
  ID — and its vector — across content updates of its document.
- Canonicalization (`CANONICAL_VERSION`), chunking (`CHUNKER_VERSION`) and the embedding
  input template (`EMBEDDING_INPUT_VERSION`) are pure and versioned; golden outputs pin
  them (`tests/ingestion/golden/chunks.json`). Changing one is a deliberate version bump
  followed by `lattice reindex --rechunk` (or `--reembed` for the template).
- The content hash covers title, body, part structure, authorship, source timestamps,
  supersession and per-kind extras — never access metadata. The access hash covers
  sensitivity, access mode, account, departments and source grants — never admin grants
  (Lattice configuration, D-12).

## 3. Chunking (by kind)

Budget: target ~400 tokens (1,600 characters), hard maximum ~600 (2,400), estimated
at four characters per token (no tokenizer dependency). Every chunk is an exact, trimmed
slice `documents.body[char_start:char_end]`; no text is lost.

| Kind | Strategy | `occurred_at` | `author_display` |
| ---- | -------- | ------------- | ---------------- |
| `doc` | One H1/H2 section at a time; paragraphs packed up to the target; a chunk never spans sections, so editing one section changes no other chunk | Doc `updated_at` | Doc author |
| `ticket` | Header part (subject, status, priority, type, requester, assignee, tags) as one chunk; each comment its own chunk; long comments split by paragraph. A new comment adds one chunk | Comment `created_at` | Comment author |
| `chat_thread` | Root + replies packed chronologically up to the target; a window never spans more than 48 h | Last message in window | Speakers, joined |
| `chat_channel_day` | The same, and a gap of more than 2 h starts a new window | Last message in window | Speakers |

Chat messages render as `[YYYY-MM-DD HH:MM UTC] Speaker: text`, so speaker and time stay
with the text wherever a chunk is cut. Oversized paragraphs split at sentence ends, then
at whitespace.

**Contextual header (decision D-10, decided).** The text sent to the embedding provider
is a short header plus the chunk text:

```
Support ticket: SSO redirect loop after switching IdP to Entra
Comment 3 · Priya Natarajan (agent)

<chunk text>
```

The header carries the kind label, the title and the heading path — content of the
document itself — and nothing else: **no structured account name or alias**, no internal
IDs, sensitivity, departments, groups, grants, emails or display reference. The lexical
index weights title and heading path (and, in the `simple` configuration, the display
reference such as `HAL-4821`) the same way. The header is shared by every reader; an
account term in it would let a query naming an out-of-scope account match a document
read through a grant (D-8). If the document's own text names its customer, that is
content and stays. Account-derived terms are applied at query time, only for accounts
the reader may see (`account_visible`; `retrieval-design.md` §4) — nothing is stored
for them. Consequence: an account change or rename never makes the index stale.

## 4. Persistence transaction (per document)

```sql
BEGIN;  -- lattice_owner, app.org_id bound
  INSERT INTO documents … ON CONFLICT (organization_id, source_id, external_id) DO UPDATE …;
  DELETE FROM chunks WHERE document_id = :doc AND NOT (id = ANY(:kept_chunk_ids));
  INSERT INTO chunks … ON CONFLICT (id) DO UPDATE SET ordinal, char_start, …;  -- vectors, tsv
  -- replace source-origin grants only if they differ; admin-origin grants untouched
  DELETE FROM document_grants WHERE document_id = :doc AND origin = 'source';
  INSERT INTO document_grants … origin = 'source';
COMMIT;
```

Retrieval therefore sees the old document or the new one, never a mix (a failure after
the chunk deletion rolls everything back; tested). An access-only change is one
transaction updating the access columns and source grants, plus the egress consequence
for its chunks (below). Embeddings are computed before the transaction; no network call
is held inside one.

**Embedding failures (as built).** A document whose embedding call fails is still
written — its new content is searchable lexically at once — with those chunks marked
`embedding_status = 'failed'` (no vector). The run is `partial`; the next sync retries
them. The Phase 0 draft kept the old version instead, which would have kept stale
content (and its old vectors) retrievable to wait for a provider.

**A failed re-embed never leaves a stale vector attached to new content.** A chunk
row is always built without a vector; it gets one only by reusing a vector of the same
document whose `embedding_input_hash` (template version + exact provider input) is
identical, or by a successful call for its own input. A changed passage is a new chunk
(its ID derives from its content), so the old row and its vector are deleted in the
document's transaction; a passage whose text is unchanged but whose input changed (a
new title or heading) keeps its ID and the upsert overwrites its vector with NULL. The
CHECK `ck_chunks_embedding_matches_status` refuses a vector on any chunk that is not
`embedded`, so a semantic path reading vectors cannot see a failed chunk. Evidence:
`tests/ingestion/test_embedding_consistency.py` (both transitions, the recovery sync
sending exactly the inputs still missing, and a check that every stored vector is the
vector of its chunk's *current* input), with three targeted mutations — upsert keeping
the old vector, reuse keyed by chunk ID instead of input hash, failed chunks not
retried — each caught.

## 5. Updates, deletion, supersession, quarantine

- Content change → `version += 1`, chunks replaced as above, `ingested_at` updated.
  Only the current version is stored (non-goal: Lattice as an archive).
- **Deletion sweep** (full mode, snapshot connectors): documents whose records are
  absent from the snapshot are deleted (CASCADE chunks with their vectors and lexical
  entries, and all grants). **Guard** — the sweep does not run when:
  - more than 20% of the source's documents (and more than one) would go
    (`deletion_guard:mass_deletion`), or the snapshot is empty
    (`deletion_guard:empty_snapshot`): override with `--allow-mass-deletion` after
    checking the export; `--dry-run` shows `would delete` first;
  - any record failed before it could be identified (a corrupt line or file:
    `deletion_guard:unidentified_failures`). Not overridable: that record could be any
    unseen document. The unseen documents are **quarantined** instead — the source may
    have changed their access — until a sync reads them again.
- A source that cannot be read (`SourceUnavailable`) fails the run; nothing is swept.
- **Quarantine** (fail closed): an existing document whose record now fails access
  mapping, normalization or validation becomes `quarantined` — kept, unreadable — rather
  than readable on access metadata the source no longer states. The next successful
  sync restores it.
- Explicit supersession (`supersedes_external_id`, e.g. refund policy v2 replaces v1)
  sets `supersedes_document_id` within the same source; links to deleted documents are
  cleared. The superseded document remains retrievable; answering marks it
  (retrieval-design §7).
- **Source disconnect** (`lattice source disconnect`): one transaction makes the source
  `deleting` and all its documents `deleted` (unreadable from the next request); a second
  transaction deletes the source row, cascading to documents, chunks, every grant on them
  (source and admin — re-apply `admin-grants.yaml` after a reconnect), source groups and
  members (those members' `authz_updated_at` is bumped), sync runs and failures. If the
  purge fails, re-running the command resumes it.

## 6. Fail-closed access mapping

`lattice.ingestion.access.map_access` turns a connector's untrusted `AccessHints` into
the stored access record through the source's Lattice configuration. It decides what is
stored, never who may read (that is `lattice.authz` over the stored record). The rules
mirror the dataset's expected-access oracle, and a contract test compares the two for
every record and persona (`tests/ingestion/test_ingested_access.py`).

| Situation | Outcome |
| --------- | ------- |
| No sensitivity in hints | Channel's configured sensitivity (chat), else the source's required default |
| `sensitivity = restricted` | Always `grants_only` (DB CHECK backs this) |
| Unknown department key (restriction or grant) | Record fails: `access_unmappable:department` — never dropped |
| Unknown admin-group key in a grant | Record fails: `access_unmappable:group` |
| Group reference the connector did not declare | Record fails: `access_unmappable:group` |
| Account key that does not exist (document front matter) | Record fails: `access_unmappable:account` — never "no account" |
| Ticketing organization / channel reference | Mapped **exactly** by the source's `account_external_refs` (P12.3), read once per sync; a mapping to an *archived* account keeps resolving |
| Ticket naming a ticketing organization with no mapping — on **any** source | Record fails: `access_unmappable:account` (never indexed without an account); the failure carries only the external reference to map |
| Reference an administrator unmapped (*unresolved*, migration 0019) — on **any** source | Record fails: `access_unmappable:account`; its indexed document is quarantined until the reference is mapped again |
| Record that still names the reference which gave its indexed document an account, now resolving to none | Record fails: `access_unmappable:account` (defence in depth: a missing mapping never lowers a document's restriction) |
| `account_scoped` source and a record with no account (no reference, or a channel never mapped) | Record fails: `access_unmappable:account` |
| Chat channel never mapped, on a source that is not account-scoped (`#general`) | Indexed without an account: the channel is not an account reference |
| Private channel with no mappable members | Indexed `grants_only` with an empty group: readable by nobody until members map |
| Existing document whose record now fails | Quarantined (§5) |

Source configuration problems (a configured account or department key that does not
exist; a sources file mapping a *new* reference to an archived account,
`source_config:account_archived`) refuse the whole sync before anything is written
(`source_config:*`).

**Mapping changes (P12.3)** apply at the next sync, deterministically, through these same
rules — nothing already indexed is rewritten when an administrator maps, remaps or
unmaps: a newly mapped reference's waiting records are indexed; a remapped reference's
documents get the new account (an access update); an unmapped reference stays known as
*unresolved* (`account_external_refs.account_id` NULL, migration 0019) and its documents
are **quarantined** — on every source, account-scoped or not — keeping their account,
version and content; new records naming it are not indexed. **A missing account mapping
never widens access.** Mapping the reference again restores them at the next sync.
Repeating a sync with the same mappings and export changes nothing.

## 7. Embedding

- `EmbeddingProvider` Protocol (`lattice.providers.embedding`): `embed(texts, purpose)`
  plus `provider`, `model`, `dimensions`, `deployment`, `retention_mode`,
  `training_opt_out`, `max_batch`. Implementations: `OpenAIEmbeddingProvider`
  (`text-embedding-3-small` at 1024 dimensions over plain HTTP; retries with exponential
  backoff on 429/5xx/timeouts; fixed error codes, never response bodies; ADR-008),
  `FakeEmbeddingProvider` (deterministic hashed bag-of-words; development and tests;
  refused in production; treated as an external provider so the egress policy is
  exercised), `RecordingEmbeddingProvider` (tests: keeps every payload).
- `--embedding none|fake|local|gemini|openai` (default `none`: eligible chunks wait as
  `pending`). `local` and `gemini` were added in the Phase 5 closure (ADR-009, ADR-010).
- **Reuse:** a chunk reuses a stored vector of the same document when its
  `embedding_input_hash` (template version + exact input) and model match. A new ticket
  comment embeds one chunk; an access-only change embeds nothing.
- **AI egress policy** (`security-model.md` §6): a chunk is sent only if its document's
  sensitivity is at or below the provider's effective level; by default `internal`. As
  built in Phase 4 that was `min(org_settings.external_egress_level,
  model_providers.approved_for_level)`; since the Phase 5 closure the level depends on
  the provider's deployment trust class (D-15: `local`, `organization_controlled`,
  `external_api`; `unknown` receives nothing), per the table in security-model §6. Chunks above the level
  are `policy_excluded`: no vector, lexical-only, still findable by keywords and
  identifiers for readers who are authorized. A classification raised above the level
  removes the document's vectors in the same transaction as the access change;
  lowering the organization's level or the provider's approval removes the affected
  vectors on the next sync of each source (`exclude_ineligible`); raising them fills
  the newly eligible chunks. Tests assert the exact payload set under each setting and
  that no canary above the level is ever sent.

## 8. Failure reporting (operator-visible)

Every sync produces a `source_sync_runs` row with counts and status, and a
`sync_record_failures` row per failed record with stage, code, safe parameters and an
opaque reference — no free-text message (`data-model.md` §3). The CLI prints counts and
codes only, never titles, record IDs or filenames; logs carry the run's counts and
codes. The Sources page (Phase 8) shows admins last sync time and status, aggregate
counts, top failure codes, deletion-guard trips and "permissions last synced"; a
failure shows a record's identifier only if the admin can read that document, otherwise
its opaque reference (`security-model.md` §10). Nothing reports `succeeded` if any
record failed — that is `partial`.

## 9. Operations

```
lattice sync --org SLUG --sources data/synthetic/<org>/sources.yaml [--source KEY ...]
             [--mode full|acl_only] [--dry-run] [--allow-mass-deletion]
             [--embedding none|fake|local|gemini|openai]
lattice reindex --org SLUG --sources FILE [--source KEY ...] [--rechunk] [--reembed]
                [--embedding none|fake|local|gemini|openai]
lattice source disconnect --org SLUG --source KEY
lattice apply-admin-grants --org SLUG --grants FILE [--dry-run] [--prune]
```

Exit codes: 0 success; 1 a sync failed (source-level) or was aborted (an unexpected
error: the run is recorded `aborted` with `internal_error`, never left `running`), or
admin grants were not applied; 2 configuration or input error; 3 a sync completed as
`partial` (records rejected, embeddings failed or the deletion guard tripped). `make
sync` accepts 3 — the dataset's HAL-4951 is unmapped on purpose. Makefile wrappers: `make sync`,
`make sync-dry-run`, `make admin-grants` (README).

- Performance (synthetic dataset, fake provider, laptop): first sync of both
  organizations ≈ 3 s; a no-op re-sync writes nothing. Remote-provider timings are
  recorded when a real key is used.
- Concurrency: one sync per source (a session-level advisory lock on a dedicated
  connection); the per-document transactions keep row locks short, so the API keeps
  serving.
