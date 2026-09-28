# ADR-010: Gemini as an optional external embedding provider

Status: Accepted (Phase 5 closure, 2026-09-28). An addition to ADR-008 (OpenAI),
not a replacement. Gemini is an optional, organization-BYOK external provider.

## Context

The project owner has a Gemini API key with limited, free (unpaid-tier) quota and
no paid OpenAI credential. Gemini is useful for a real *external* semantic benchmark
on the synthetic corpus, and as a BYOK option for organizations that already use
Google. A key is not an approval. What may be sent to Gemini is decided by the egress
policy (security-model §6, ADR-011), and the unpaid tier's data terms put it out of
reach of real data entirely.

## Facts from the official documentation

Retrieved 2026-09-28 from ai.google.dev: *Embeddings* guide (updated 2026-09-17),
*Gemini Embedding 2* model page, the *Embeddings* API reference and the v1beta
discovery document, *Pricing*, *Rate limits*, and the *Gemini API Additional Terms of
Service* (last modified 2026-04-28).

| | `gemini-embedding-2` | `gemini-embedding-001` |
| --- | --- | --- |
| Status | Stable, released April 2026 (latest; multimodal) | Stable, legacy (text only) |
| Output dimensions | 128–3,072 via `output_dimensionality`; default 3,072; recommended 768 / 1,536 / 3,072 | same range and recommendations |
| Normalization | the service normalizes truncated dimensions | **not** normalized below 3,072 (normalize yourself) |
| Max input | 8,192 tokens | 2,048 tokens |
| Query / document semantics | **text prefixes**, `task_type` not supported: query `task: search result \| query: {content}`, document `title: {title} \| text: {content}` (`title: none` without a title) | `taskType` `RETRIEVAL_QUERY` / `RETRIEVAL_DOCUMENT` |
| Batching | `batchEmbedContents`, one `EmbedContentRequest` per text (several parts in one request are **aggregated** into one embedding) | same |
| Space compatibility | incompatible with 001: re-embed to switch | |
| Price (paid, text) | $0.20 / 1M tokens; Batch API $0.10 / 1M | (not re-checked) |
| Free tier | yes, text input free of charge | |

- **Per-batch limit**: the reference states none. The service rejects more than 100
  requests per `batchEmbedContents` call (widely reported; the adapter uses 100).
- **Rate limits** are per project, not per key. They are measured as RPM, input TPM
  and RPD. RPD resets at midnight Pacific. The documentation publishes no per-model
  numbers and says to view them in Google AI Studio. Whether each text in a batch
  counts as one request is not documented.
- **Data use** (Additional Terms): for **Unpaid Services**, which include the unpaid
  quota on the Gemini API, Google "uses the content you submit … to provide, improve,
  and develop Google products". Human reviewers may read, annotate and process API
  input and output. The terms say not to submit sensitive, confidential or personal
  information. For **Paid Services**, which require a Cloud project with active
  billing, prompts and responses are not used to improve products. They are logged for
  a limited period for abuse detection, under the Data Processing Addendum.
- **Retries**: 429 is `RESOURCE_EXHAUSTED` with a `RetryInfo.retryDelay`. Clients
  should back off exponentially on 429 and 5xx.

Discrepancies with the closure brief's assumptions: none that block. The brief
anticipated "task types"; the current model uses text prefixes instead, and the
adapter handles both families. The recommended dimension list does not include 1024,
but 1024 is inside the supported range (below).

## Decision

1. **Model: `gemini-embedding-2`**, configurable to `gemini-embedding-001`
   (`LATTICE_GEMINI_EMBEDDING_MODEL`). Any other name is refused.
2. **Dimension: 1024**, via `embedContentConfig.outputDimensionality`. It is inside the
   supported 128–3,072 range, so Lattice's `vector(1024)` needs no migration. It is not
   one of the three *recommended* sizes, but the model is trained with
   Matryoshka-style truncation, so 1024 is a truncation of the full vector. The adapter
   L2-normalizes every vector: a no-op for gemini-embedding-2, and required for 001.
3. **Query/document semantics** live only in `GeminiEmbeddingProvider`:
   `purpose = "query"` → `task: search result | query: …`;
   `purpose = "document"` → `title: none | text: …`, where the text is Lattice's
   embedding input (kind, title, heading path, chunk text, per D-10). The provider
   never parses document structure, so the title stays inside the text. For 001 the
   same purposes map to `RETRIEVAL_QUERY` / `RETRIEVAL_DOCUMENT`.
4. **Transport**: plain HTTPS with `httpx`, no Google SDK. The key goes in the
   `x-goog-api-key` header only: never in the URL, logs, traces, errors or `repr`. It is
   read from `LATTICE_GEMINI_API_KEY` or Google's own `GEMINI_API_KEY`.
5. **Retries**: 5xx and timeouts get exponential backoff (3 attempts). 429 waits the
   server's `retryDelay`, capped at 60 s, at most twice. After that the provider fails
   fast without sending anything for the cap period (a circuit breaker), so a
   quota-limited sync marks the remaining chunks `failed`, to be retried by the next
   sync, instead of hammering the quota. Other errors are not retried. Errors are fixed
   codes and never echo the body.
6. **Data use is declared, and the unpaid tier is synthetic-only.** Lattice cannot
   check a key's billing tier, so the operator declares it with
   `LATTICE_GEMINI_DATA_USE=unpaid|paid`, default **`unpaid`**. An unpaid key:
   - is recorded with `training_opt_out = false` and
     `retention_mode = unpaid_product_improvement`;
   - is **refused by the factory when `LATTICE_ENV=production`**;
   - may receive content **only from organizations whose `org_settings.data_origin =
     'synthetic'`** (Halden and Veridian, set by the seed from their manifests). For
     any `customer` organization the egress policy sends nothing: no document chunk and
     no question (`provider_data_use`), whatever the key's validity. The paid
     declaration cannot override a record that says otherwise: the more restrictive
     reading wins.
   - is never production-approved automatically. Like every provider record it starts
     `approved_for_level = internal`, so confidential and restricted synthetic content
     is not sent either unless explicitly approved.

   A paid-tier key (`paid`) meets security-model §6's "not used for training" provider
   requirement. It is then an ordinary external BYOK provider: capped by
   `min(external egress level, approval)`, `internal` by default.

## Consequences

- Gemini can run a real external semantic benchmark on the synthetic corpus at no
  cost, without real data ever being eligible.
- Measured on the synthetic golden set (evaluation-plan §10): Recall@10 0.726, MRR
  0.648, all security metrics 0. That is the best of the real providers tested, and
  still below the 0.90 target. Quality is not eligibility: an unpaid key remains
  synthetic-only.
- The free tier allows 1,000 embedded texts per day per project and model, counted
  per text; the synthetic corpus needed two days (or a second project's key). Quota use
  for the full synthetic evaluation, measured: see evaluation-plan §10.
  Documents are about 20 batched requests (1,289 internal chunks, ~306k characters,
  ~76k tokens). Queries take 1–2 batched requests, because the evaluation prefetches
  the questions its own egress check allows. A sync that exhausts the quota
  resumes with only the failed chunks.
- Tests never call Google. A mock transport covers request shape, both model families,
  batching, ordering, 429 / 5xx / timeout handling, the circuit breaker, malformed
  responses, wrong dimensions, authentication failures and key hygiene.

## Reconsider when

- Google publishes per-model limits or batch-counting rules. Revisit the batch size
  and the circuit-breaker cap.
- An organization brings a paid Gemini key and wants it approved above `internal`.
  That is an explicit administrator approval under ADR-011, never automatic.
- A newer Gemini embedding model becomes the recommended one. It is a new embedding
  space: re-embed, never mix.
