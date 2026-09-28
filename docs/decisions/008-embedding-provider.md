# ADR-008: Embedding provider for the first vertical slice

Status: Accepted (Phase 4, 2026-09-27). Implements decision D-2 within the egress
policy D-3.

**Scope note (Phase 5 closure, 2026-09-28).** This ADR is the *external API*
embedding decision, and it remains valid: when an organization uses an external
provider for embeddings, OpenAI `text-embedding-3-small` at 1024 dimensions is the
reference choice. OpenAI support is kept and documented. It is **optional**: it is an
organization bring-your-own-key provider (ADR-011), and Lattice does not require it.
The OpenAI real-provider evaluation has not run, because no credential is available
and none is required. Two further providers now sit behind the same Protocol, for
other deployment needs:
[ADR-009](009-local-embedding-provider.md) (local, zero-cost, no egress) and
[ADR-010](010-gemini-embedding-provider.md) (Gemini, optional BYOK; unpaid key =
synthetic only). The real semantic benchmark was run with those
(`evaluation-plan.md` §10; final status §12). The Voyage comparison below was not triggered.

## Context

Phase 4 needs one real external embedding provider behind the `EmbeddingProvider`
Protocol (`lattice.providers.embedding`), plus a deterministic fake for tests and CI.
Two candidates were compared: **OpenAI** and **Voyage AI**. Constraints that matter for
Lattice specifically:

- Egress (security-model §6, SEC-10): the embedding provider eventually receives the
  entire indexed *internal* corpus. It is the largest egress decision in the product.
  Security-model §6 requires "commercial API terms under which inputs are not used for
  training" and zero data retention where offered.
- Retrieval: hybrid lexical + semantic (retrieval-design §4–6). Semantic recall matters
  most for paraphrase questions; exact identifiers are handled lexically.
- Chunks are small (a section, a ticket comment, a Slack window; ingestion-design §3),
  so long context windows are not a differentiator.
- Storage: `chunks.embedding` is `vector(1024)` (a per-deployment dimension). The
  first provider must fit it, and so should the likely alternative.
- Corpus: about 2,000 records / 1,300 chunks in the synthetic dataset, English only.

## Facts from current official documentation

Retrieved 2026-09-27. Sources: OpenAI *Embeddings* guide, *Embeddings API reference*,
model pages for `text-embedding-3-small` / `-large`, *Your data* (data controls);
Voyage AI docs *Text Embeddings*, *Pricing*, *Rate Limits*, *FAQ*, *Service Level
Objectives*, *API key and installation*, *AWS Marketplace*; the Voyage AI Terms of
Service (as summarized by the Voyage site search) and the Voyage 4 announcement
(blog.voyageai.com, 2026-01-15). Items marked *not documented* were not found in those
pages. Vendor quality claims are labelled as such.

| | OpenAI `text-embedding-3-small` | OpenAI `text-embedding-3-large` | Voyage `voyage-4-lite` | Voyage `voyage-4` | Voyage `voyage-4-large` |
| --- | --- | --- | --- | --- | --- |
| Default dimensions | 1536 | 3072 | 1024 | 1024 | 1024 |
| Reduced dimensions | `dimensions` parameter (v3 models) | same | 256 / 512 / 2048 via `output_dimension` | same | same |
| Other output types | float, base64 | same | float, int8, uint8, binary, ubinary | same | same |
| Max input | 8,192 tokens per input | same | 32,000 tokens (truncates by default) | same | same |
| Per-request batch | ≤ 2,048 inputs, ≤ 300,000 tokens summed | same | ≤ 1,000 texts, ≤ 1M tokens | ≤ 1,000 texts, ≤ 320K tokens | ≤ 1,000 texts, ≤ 120K tokens |
| Price / 1M tokens | $0.02 | $0.13 | $0.02 | $0.06 | $0.12 |
| Free allowance | not documented | not documented | 200M tokens | 200M tokens | 200M tokens |
| Batch discount | Batch endpoint supported; separate price not stated on the model page | same | 33% via Batch API | same | same |
| Rate limits (entry paid tier) | Tier 1: 3,000 RPM, 1M TPM (Tier 5: 10,000 RPM, 10M TPM) | same | Tier 1: 2,000 RPM, 16M TPM (×2 at Tier 2, ×3 at Tier 3) | 2,000 RPM, 8M TPM | 2,000 RPM, 3M TPM |
| Query/document input types | none | none | `input_type` = `query` / `document` (prepends a retrieval prompt; recommended for retrieval) | same | same |
| Retrieval specialization | general purpose; guide cites MTEB 62.3% (3-small), 64.6% (3-large) | "english and non-english tasks" | "optimized for latency and cost" | "general-purpose and multilingual retrieval" | "best general-purpose and multilingual retrieval quality"; vendor claims top of the RTEB leaderboard (no comparison with OpenAI published) |
| Shared space across sizes | no | no | Voyage 4 series embeddings are mutually compatible (vendor claim) | same | same |
| Vectors normalized | yes (length 1; cosine = dot product) | yes | not stated in the pages read | | |

**Data use and retention**

| | OpenAI API | Voyage AI API |
| --- | --- | --- |
| Training on API inputs | **No by default** ("not used to train or improve OpenAI models (unless you explicitly opt in)") | **Yes by default**: the Terms grant Voyage the right to use Customer Content for training *unless you opt out*. Opt-out requires an organization Admin with a payment method on file, via a dashboard toggle; data sent before opting out may remain subject to that clause |
| Retention | Abuse-monitoring logs up to 30 days; no application state for embeddings | After opt-out: "zero-day retention" (deleted immediately after processing). Default retention period not documented |
| Zero data retention | `/v1/embeddings` is ZDR-eligible (approval required) | Achieved by the opt-out above |
| Data residency | Regions incl. US, Europe (EEA + Switzerland), UK, Canada, Japan, Australia and others; non-US regions require approvals (Europe: ZDR / modified abuse monitoring) | Not documented for the Voyage API; a Europe data-residency option is announced for the MongoDB Atlas Embedding and Reranking API |
| Private deployment | Not offered for these models | AWS Marketplace / SageMaker deployment into the customer's own account and VPC ("customers maintain full control over data flow"); the Voyage-published listing covers older models and points to MongoDB's listing; Voyage 4 is announced for AWS Marketplace, GCP Vertex AI Model Garden, Azure; `voyage-4-nano` has open weights |
| Availability commitment | not documented in the pages read | SLO (not SLA) ≥ 99.5% monthly uptime for api.voyageai.com; latency targets not documented |
| SDK / API | Official HTTP API; `input`, `model`, `dimensions`, `encoding_format` | HTTP API with Bearer token; official Python SDK |

## Options considered

1. **OpenAI `text-embedding-3-small` at 1024 dimensions.**
2. OpenAI `text-embedding-3-large` at 1024 dimensions: 6.5× the price for a model the
   guide ranks about 2 MTEB points higher; the benchmark gap is not Lattice's
   workload.
3. **Voyage `voyage-4-lite` or `voyage-4`** (1024 native), with query/document input
   types.
4. Voyage `voyage-4-large`: highest vendor-claimed quality, 6× `voyage-4-lite`'s
   price, lowest token budget per request.

## Lattice-specific assessment

For OpenAI:
- Privacy posture is safe by default. Inputs are not used for training, with no account
  step to forget, which satisfies security-model §6 as configured. ZDR is available on
  approval and the 30-day abuse-monitoring retention is documented.
- Broad data-residency options, useful for a European pilot.
- Cheapest (3-small, $0.02/1M), with generous per-request batching (2,048 inputs).
- No query/document distinction, so one representation serves both.
- No private or self-hosted path, so it cannot become the "approved for confidential"
  provider later without an external API.

For Voyage:
- Retrieval-specialized, multilingual, and supports `input_type`, which is plausibly
  better for questions against enterprise text. The vendor claims leadership on a
  retrieval benchmark. Unverified for our corpus.
- The shared embedding space lets query and document models differ later without
  re-indexing.
- A customer-hosted deployment path (in the customer's VPC, and open-weights
  `voyage-4-nano`) is the natural future candidate for an `organization_controlled`
  provider (D-15) approved for `confidential` (security-model §6).
- Training is on by default. An account whose admin never flipped the opt-out would
  send the whole internal corpus into model training. Lattice cannot verify that toggle
  through the API, so meeting security-model §6 depends on a manual step outside
  Lattice.
- Price is the same as OpenAI at the lite tier (plus a 200M free allowance) and 3× at
  `voyage-4`.

Both:
- Fit `vector(1024)` without a schema change (OpenAI via `dimensions=1024`, Voyage
  natively), so switching is a re-embed (`lattice reindex --reembed`), not a migration.
- Cost is negligible at slice scale: the full synthetic corpus is well under 1M tokens
  per full re-embed, cents with either.
- Latency is dominated by network round trips. Batching (at most one request per 128
  chunks) makes a full sync a handful of calls with either. Neither publishes latency
  targets in the pages read.

## Decision

**OpenAI `text-embedding-3-small`, `dimensions=1024`**, as the first real provider
(`lattice.providers.openai_embedding.OpenAIEmbeddingProvider`), called over plain HTTP
(`httpx`, no vendor SDK). The provider record is `approved_for_level = internal`,
`deployment = external_api` (D-3 default).

Neither candidate is clearly superior on retrieval quality before evaluation. The
brief's rule is to take the simpler candidate. OpenAI is simpler for Lattice where it
matters most: its default data terms already meet the provider requirement in
security-model §6, with no out-of-band account toggle; it has no input-type
semantics; and it is the cheapest option with the most permissive batching.

Voyage stays fully compatible with the abstraction. `EmbeddingProvider.embed(texts,
purpose)` already carries `purpose ∈ {document, query}`, which a Voyage adapter maps
to `input_type`, and the OpenAI adapter ignores. The 1024-dimension column fits both.

## Consequences

- The embedding model is recorded per chunk (`chunks.embedding_model`), so a provider
  change is a tracked re-embed of eligible chunks.
- Tests never call the network. The adapter is tested against a local mock transport:
  request shape, batching, ordering, dimension check, retry and error mapping.
- The API key is read from `LATTICE_OPENAI_API_KEY` by the provider only. It is never
  stored in `sources.config` or `model_providers`, and never logged.
- Only chunks of documents at or below the effective egress level (default
  `internal`) are ever sent (ingestion-design §7).
- Phase 5: queries are embedded with `purpose = "query"` (ignored by this adapter),
  only when the provider is recorded for the organization and the question's effective
  sensitivity (D-13: the organization default, raised but never lowered by a
  declaration) is within the effective level; query vectors are compared only with
  chunks of the same model (`retrieval-design.md` §5).

## Reconsider when

- **Phase 5 retrieval evaluation** (Recall@10 target ≥ 0.9) misses on paraphrase
  items and the misses are semantic. Then run the same golden set with `voyage-4-lite`
  and `voyage-4` using `input_type`, and switch if recall improves materially.
- An organization wants confidential content embedded. That requires an
  `organization_controlled` provider (D-15; e.g. Voyage in the customer's VPC) with an
  organization ceiling and an explicit approval for `confidential`, or a `local` model
  approved for it (security-model §6).
- Multilingual corpora appear.
- OpenAI's data terms change (training default, retention) or a pilot needs a
  residency region only one vendor offers.
