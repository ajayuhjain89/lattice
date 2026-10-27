# Phase 13 — Retrieval, Grounding and Answer Quality: plan

Status: **P13.0 (planning, evaluation design and baseline) written 2026-10-20 on
`phase/13-retrieval-answer-quality` from `main` 00a15d2 (e9f967d); direction approved
by the product owner 2026-10-21 with decisions D-38–D-45 (§15) and the evaluation
refinements of §9.1.** **P13.1 (evaluation foundation) delivered 2026-10-21** (§19;
evaluation-plan §15): measurement only, no retrieval or answering behaviour changed;
approved 2026-10-24.** **P13.2 (golden set growth, splits and label provenance)
delivered 2026-10-24** (§20; evaluation-plan §16): dataset and evaluation infrastructure
only; engineering complete, the product owner's review of the held-out labels (D-41)
pending. **P13.3 (relevance gate and abstention calibration) delivered 2026-10-27 and
accepted by the product owner the same day as committed (cb82ce2, `gate_v1`), with its
held-out trade-off recorded** (§21; evaluation-plan §17): the first behaviour change --
answering only; retrieval unchanged.
**P13.4 (lexical ranking) and P13.5 (fusion and selection) delivered 2026-10-27 with no
change adopted** (§22–23; evaluation-plan §18–19): no variant improved held-out. **P13.6
(second hop) approved under D-40, built and not adopted** (§24, §28). **P13.7 (validator suite, prompt-2)
delivered 2026-10-27** (§25; evaluation-plan §20). **P13.8 (conflicts, temporal)
delivered 2026-10-27 with no behaviour change** (§26; evaluation-plan §21). **P13.9
engineering closure 2026-10-27** (§27): the phase's remaining decisions are the owner's. P13.4–P13.9 were authorized together
on 2026-10-27 ("finish P13"); nothing is pushed.

Scope and roadmap position: `implementation-plan.md` §4 "Later phases", row 13. Phase 12
is merged (00a15d2, branch and merge CI green, images published). Phase 14 owns scale and
adversarial hardening, Phase 15 GA (with the deferred external-staging gate,
phase-11-plan §21.1).

Related: [retrieval-design.md](retrieval-design.md) (Part I retrieval, Part II answers),
[evaluation-plan.md](evaluation-plan.md) (§9–13 results; §14 the P13.0 baseline),
[security-model.md](security-model.md), [authorization-model.md](authorization-model.md),
ADR-003, ADR-004, ADR-006, ADR-009, ADR-011, ADR-012.

---

## 1. Objective

Find out how good Lattice's retrieval and grounded answers really are, make the
weaknesses measurable, and improve them, without moving the central invariant:

> **The model never receives content the asking user is not authorized to read**
> (SEC-1). Authorization stays inside every retrieval statement (SEC-3), before semantic
> retrieval and before generation; tenant isolation (SEC-2), unreadable = nonexistent
> (SEC-5), the AI egress policy (SEC-10, D-3, D-13, D-15, D-34) and claim-level
> fail-closed citations (SEC-7, SEC-13) are not traded for quality.

A ranking or answering gain that puts one forbidden document into any candidate list,
prompt, response, trace or log is rejected regardless of its quality gain.

## 2. Non-goals

- Weakening, bypassing or reordering any authorization layer (E1–E7), the RLS policies,
  the E4 tripwire, D-8/D-10/D-13/D-14/D-15/D-18 or SEC-15 (admin ≠ content) to raise
  recall. Quality is measured only over evidence the persona may read.
- Sending more content to external providers to raise quality. Egress ceilings, query
  sensitivity (D-13) and trust classes (D-15) are unchanged.
- Paid model calls, paid infrastructure, Ollama benchmarks or any local LLM run on the
  reference machine (M1, 8 GB; ADR-012; the zero-cost constraint of phase-11-plan D-22 as amended).
- Reintroducing the rejected cross-encoder (evaluation-plan §11) unless its
  reconsideration condition is met and the product owner reopens it.
- LLM query rewriting, HyDE, agentic or multi-call retrieval: each adds model calls and
  an egress surface for every question. Not proposed (§11 explains the deterministic
  alternatives).
- Conversational memory (ADR-007), streaming of unvalidated text (ADR-006), new source
  connectors, the account timeline workspace, scale work (Phase 14).
- Choosing a default generation provider (D-4 stays open; §15 D-43 covers evaluation
  only).

## 3. Starting state (verified 2026-10-20)

| Check | Result |
| ----- | ------ |
| `main` | 00a15d2 "Merge Phase 12: Organization Setup & Integrations" = `origin/main` |
| Working tree | clean |
| Phase 12 | merged --no-ff (parents 143392f + 197cf48) |
| CI | merge run 39416064098 green (backend 3,241 tests, web, images, dependency audit) |
| Phase 13 work present | none (`git log`, branches, docs: only roadmap mentions) |
| Branch | `phase/13-retrieval-answer-quality` from 00a15d2 |
| Development database | not touched (still 0018; its container was not running) |
| P13.0 measurements | isolated Compose project `lattice-p13eval`, port 5440, own volume, migrated to 0024, seeded and synced exactly as CI does (§6) |

## 4. Current architecture as implemented

Read from the code, not only the docs. Paths are under `backend/src/lattice/`.

```
POST /v1/ask → answering.service.answer_question
  is_action_request(question)                              D-11, code only
  tenant_snapshot (REPEATABLE READ, READ ONLY, org-bound)
    resolve_authorization_context                          E1/E2, from the DB only
    retrieval.service.retrieve_with_context  (limit = EVIDENCE_LIMIT 12)
      normalize · extract_identifiers · has_temporal_intent
      visible_accounts → resolve_scope → mentioned_accounts       D-10 (visible only)
      question_policy (D-13) → query_egress_block (D-15) → embed query (5 s, concurrent)
      exact    : documents.display_ref = ANY(ids)       ┐ each one SQL statement with
      lexical  : FTS any-lexeme match, coverage rank    │ tenant + lifecycle +
      semantic : pgvector cosine, exact scan, k=50      ┘ readable_documents_predicate
      E4 assert_all_readable(every candidate)                 abort, never filter
      RRF(k=60, pool 40) → rerank_v1 → select_evidence (dedupe, ≤3/doc first)
      hydrate (predicate again) → E4 again → trace
    _relevant (relevance floor) · _ambiguous · resolve_generation (egress, D-13/D-15)
  [snapshot closed]
  budget (≤12 items, ≤24,000 chars) → build_prompt (nonce blocks, chronological)
  recheck_generation (P12.5) → generate → validate_draft → (one regeneration)
  assemble: decide_status, supersessions, conflicts, freshness, notices
  _response_invariant → query_traces INSERT
```

| Stage | As built | Where |
| ----- | -------- | ----- |
| Query understanding | Deterministic only: NFC + control/format stripping; identifiers `[A-Za-z][A-Za-z0-9]{1,15}(-[0-9]+)+` (≤5, from the question only); temporal-intent regex (latest, current, now, still, recent, today, this/last week/month); no date parsing, no quoted-phrase must-match, no rewriting | `retrieval/query.py` |
| Account detection | Whole-phrase, case-insensitive name/alias match **among the reader's visible accounts only**; ≥2 bare-alias matches with no full name = `ambiguous` | `retrieval/query.py`, `answering/service.py::_ambiguous` |
| Authorization context | Resolved from the database inside the request snapshot; nothing from the client | `authz` (E1–E3), `retrieval/permitted.py` |
| Exact channel | `display_ref` equality through `(organization_id, display_ref)`; all chunks of a matched record, ≤50 | `retrieval/exact.py` |
| Lexical channel | `chunks.tsv` = title + heading path (A, english), display ref (A, simple), text (D, english); **no account names** (D-10). Mode `coverage`: OR of the question's quoted lexemes; score = distinct question lexemes matched + `ts_rank_cd(…,32)` + 1 per identifier phrase + 1 if the chunk's *visible* account is named (that term alone also matches) | `retrieval/lexical.py` |
| Semantic channel | Query embedding only if the egress policy allows it (D-13 effective sensitivity, D-15 trust class, provider record, data terms); same model and 1024 dims only; `embedding_status='embedded'`; exact cosine scan (no ANN index); any failure degrades to exact + lexical with a typed outcome | `retrieval/semantic.py` |
| Fusion | RRF, equal weight per channel, `k=60`, pool 40; identity `(organization_id, chunk_id)`; total deterministic order | `retrieval/fusion.py` |
| Rerank | `rerank_v1`: bounded multiplicative features — account match 0.15, authority 0.10, recency×temporal intent 0.20 (scale 60 d), identifier 0.50. Weights are the design's, **never tuned** | `retrieval/rerank.py` |
| Selection | Dedupe identical text (content hash); ≤3 chunks per document first, the rest deferred (not dropped); stop at the limit | `retrieval/rerank.py::select_evidence` |
| Chunking | `chunker-1`: doc by H1/H2 section then paragraphs (~1,600 chars target, 2,400 max); ticket header + one chunk per comment; chat windows (≤48 h thread, 2 h gap for channel days). Embedding input = kind label + title + heading path + text (`embed-input-1`) | `ingestion/chunking.py`, `ingestion/embedding_input.py` |
| Evidence | Readable chunks only; account only when visible (D-8); supersession links between readable documents only (D-14); channel ranks, scores, features | `retrieval/evidence.py`, `retrieval/models.py::Evidence` |
| Relevance floor | An evidence item may enter the prompt if the exact **or lexical** channel matched it, or its semantic similarity reaches `SEMANTIC_FLOOR[model]`. `SEMANTIC_FLOOR` is **empty** (no model calibrated), so semantic-only evidence never enters a prompt | `answering/service.py::_relevant` |
| Context construction | Relevant items within the egress level, in rank order, ≤12 items and ≤24,000 chars; blocks ordered chronologically with rank as an attribute; nonce delimiters; title, ref, visible account, date, author, authority | `answering/prompt.py` |
| Generation | One call (+ one regeneration with rejection codes); `answer-prompt-1`; hand-written JSON schema; 2,048 output tokens; deadline 120 s (720 s offline) | `answering/service.py`, `providers/generation*.py` |
| Citation validation | Claim-level fail-closed: handles, verbatim quotes (normalized, no fuzzy), quote floor, number support, shared-term support floor, unsafe content; conflicts need ≥2 valid positions from ≥2 documents | `answering/validate.py` |
| Conflicts | Potential conflicts are **model-proposed only**, then verified; supersession is deterministic from explicit links | `answering/assemble.py` |
| Abstention | Deterministic: `no_relevant_evidence` (floor), `ambiguous` (accounts), `unsupported_request` (D-11), `evidence_only` (egress / unavailable), `claims_unverified`. Otherwise the model's advisory status, recomputed by code | `answering/assemble.py::decide_status` |
| Evidence-only fallback | No provider may be called, or nothing relevant fits the egress level → evidence list without a draft | `answering/service.py`, `answering/policy.py` |
| Providers | Embedding: fake, local mxbai (ONNX), Gemini, OpenAI. Generation: fake, scripted (tests), Ollama, Gemini. No Anthropic/OpenAI generation adapter (D-4 open) | `providers/` |
| Traces | `query_traces`: retrieval trace (IDs, ranks, features, counts, stage timings; no text), prompt/cited chunk IDs, egress decision, validation counts, usage; re-authorized on read (E6) | `tracing/`, `answering/service.py` |

## 5. Existing evaluation capabilities

Two harnesses (`lattice eval retrieval`, `lattice eval answer`), the golden suites in
`tests/retrieval/golden/` and `tests/answering/golden/`, and about 190 targeted retrieval
and answering test functions. What each requested property is measured by today:

| Property | Measured? | By |
| -------- | --------- | -- |
| Retrieval recall | **Yes** | `eval retrieval`: Recall@1/5/10/20 (item-macro, chunk/part level), per category and persona; CI floors (R@10 ≥ 0.49 fake, ≥ 0.64 exact+lexical, overall only) in `test_golden_retrieval.py` |
| Ranking relevance | **Partial** | MRR of the first target. No nDCG, no precision; `acceptable` labels (247 keys) are parsed by the lint but **unused by the harness** |
| Citation correctness | **Yes, structurally** | Validator (handles, verbatim quotes) + `visible_unverified_claims` and `dangling_citations` = 0 in `eval answer` and the golden suite. *Whether a quote supports its claim* is only the uncalibrated judge |
| Evidence support | **Partial** | Deterministic support floor (`unsupported_number`, `no_shared_terms`) in tests; no measured rejection precision/recall on adversarial drafts |
| Conflicting sources | **Plumbing only** | `potential_conflict_found` (0.0: the fake never proposes one), supersession found / never unfounded; conflict coverage (both sides in the context) is listed in evaluation-plan §5 but **not implemented** |
| Insufficient evidence | **Status only** | Status accuracy per category; no metric for whether the deterministic gate fires (it never does, §6.3) |
| Exact identifier retrieval | **Yes** | Exact-channel tests; golden items tagged `exact_identifier` (5) and `cross_org_same_identifier` (17) |
| Freshness / temporal relevance | **Partial** | `staleness` check (8 runs), `stale` category recall; no "as of" or date-filter questions |
| Multi-document questions | **Recall only** | `cross_document`, `cross_source` categories; no context-recall or completeness metric |
| Account scoping | **Yes (security), little quality** | Scope refusal tests, D-8/D-10 checks; only 5 golden items carry a scope |
| Authorization and leakage | **Yes, release-blocking** | Every stage checked against the dataset oracle (never the DB): unauthorized/cross-org candidates, `must_not_retrieve`, D-8 metadata, D-10 mentions, canaries in payload/answer/trace/logs, egress violations, secrets; property test with RLS bypassed; SQL-structure tests; same-question groups |
| Performance | **Minimal** | Answer eval reports total and generation p50/p95; retrieval stage timings exist in every trace but are **not summarized** by the retrieval harness |
| Real answer quality | **Not measured** | No real generation provider has run (ADR-012; evaluation-plan §13) |

Planned in evaluation-plan §5 but not built at P13.0: Precision@10 (exhaustive items),
document-level recall, context recall, conflict coverage. Tracked in §17. **P13.1 built
all of them except Precision@10** (no reviewed exhaustive labels yet), plus nDCG@10
(informational), identifier hit rate, the rerank ablation, abstention catch/false
abstention, stage latency, prompt-size metrics, item-level baselines and `lattice eval
compare` (§19).

## 6. Baseline evidence

### 6.1 Measured in P13.0 (HEAD 00a15d2, 2026-10-20)

Isolated database `lattice-p13eval`, migrated 0001→0024, both organizations seeded and
synced with fake embeddings, admin grants applied (the CI sequence). Corpus: Halden
1,110 chunks, Veridian 215 (1,325; 36 `policy_excluded`). `retrieval-6`, `rerank_v1`,
`AS_OF` 27 September 2026. 89 golden items, 68 with retrieval targets (151 targets).

| Configuration | R@1 | R@5 | R@10 | R@20 | MRR | Security |
| ------------- | --: | --: | ---: | ---: | --: | -------- |
| A. Exact + lexical (`--embedding none`) | 0.316 | 0.534 | **0.668** | 0.779 | 0.641 | all 0 |
| B. Fake semantic (*test double*) | 0.238 | 0.440 | 0.512 | 0.685 | 0.515 | all 0 |

- A reproduces the committed Phase 5 retrieval-6 report **exactly** (0.6679 / 0.6405);
  ranking code is unchanged since the Phase 5 closure (only evidence hydration changed
  in Phase 6), so Phases 6–12 did not move retrieval.
- **B is not reproducible across machines.** The same commit gives R@10 0.5123 here
  (Apple M1) and **0.5172** on CI's x86-64 runner (run 39416064098; misses
  `ranked_below_k` 27 vs 26): one target differs. A is not affected. Cause not yet
  identified; likely candidates are floating-point ordering of near-tied cosine scores
  or platform differences in the CI PostgreSQL service. Consequence: a baseline compare
  must be exact on A and per-item with explained differences on B (EC-6).
  **Explained in P13.1** (§19, evaluation-plan §15): pgvector's float32 cosine distances
  differ in the last bits between aarch64 and x86-64 (FMA build), and the fake vectors'
  exact ties are then ordered by rounding instead of the stable tie-break. 22 items rank
  differently; one target outcome differs (`xsrc-kestrel-outage-daniel`). B now has one
  exact baseline per database architecture.

Distribution of A's results (the deterministic floor):

| Slice | Value |
| ----- | ----- |
| Items whose targets are all found @10 / none found @10 | 32 / 7 (of 68) |
| R@10, items with one target (20) vs ≥3 targets (25) | **0.900 vs 0.437** |
| R@10 by category | lookup 0.900, supersession 0.917, cross_org 0.861, cross_source 0.630, prompt_injection 0.625, stale 0.619, cross_document 0.563, account_visibility 0.563, potential_conflicts 0.542, inaccessible 0.500, ai_egress 0.486 |
| Questions containing an identifier (8) | R@10 1.000 |
| Misses @10 (64): never returned / outside fused pool / fused but not in the 20 / in the 20 but below 10 | 21 / 3 / 21 / 19 |
| Misses fused within the top 10 yet absent from the evidence (selection) | 4 |

Answer pipeline with the fake extractive generator (96 runs = items × egress
configurations). The generator quotes the best-ranked evidence; it measures plumbing,
**not answer quality**.

| Metric | A: exact + lexical | B: fake semantic |
| ------ | -----------------: | ---------------: |
| Security metrics (11) | all 0 | all 0 |
| Status accuracy | 0.771 | 0.771 |
| Context recall: expected relevant evidence reaching the prompt (71 answerable runs, macro) | **0.701** | 0.534 |
| Runs with every relevant target in the prompt / none | 36 / 6 | 27 / 19 |
| Citation coverage (cited / relevant targets in the prompt) | 0.528 | 0.587 |
| Evidence withheld from the prompt: relevance floor / budget / egress | 0 / 56 / 16 | 98 / 55 / 2 |
| Expected `insufficient_evidence` (13 runs) answered instead | **13** | 13 |
| Expected `evidence_only` (4 runs) answered instead | 4 | 4 |
| Supersession found / never unfounded | 0.875 / 1.0 | 0.875 / 1.0 |
| Staleness flag as expected | 0.75 | 0.75 |
| Pipeline latency p50 / p95 (fake model, M1, local DB) | 32 / 40 ms | 63 / 92 ms |
| Prompt size per generation call (chars ÷ 4, excluding the fixed instructions; 89 calls) | ≈1,520 tokens | ≈1,350 tokens |

Context recall and the floor counts were computed in P13.0 from the harness's JSON
reports (`targets_in_prompt`, `not_used`); the harness does not print them yet (P13.1).

### 6.2 Historical (not re-measured; ranking code unchanged since)

From evaluation-plan §10–12, measured 2026-09-28/29 against the same retrieval code:
local `mxbai-embed-large-v1` approved `restricted` R@10 **0.695** (MRR 0.640); approved
`internal` 0.660; Arctic L v2.0 0.657; Gemini `gemini-embedding-2` **0.726** (MRR 0.648;
synthetic data only, quota spent, never re-run). Cross-encoder rerank: measured and
rejected (§11 there). These remain the only real-semantic figures. Their evaluation
databases were not retained.

### 6.3 Not measured

| What | Why | Reproducible alternative (§9, §11) |
| ---- | --- | ---------------------------------- |
| Real-model answer quality (facts, entailment, conflicts, abstention) | No paid provider; local LLM forbidden on the reference machine (ADR-012); Gemini quota exhausted | Deterministic validator suites with scripted adversarial drafts (zero cost); optional BYOK run on synthetic data only (D-43) |
| Real-semantic retrieval at HEAD | Needs the corpus embedded with the local model (~6 min, ~1.5 GB RSS) | One approved local run per corpus change in an isolated DB, dumped and reused (D-38) |
| Judge accuracy | No 30-item human calibration exists (evaluation-plan §6) | Product-owner labelling (D-44); until then the judge gates nothing |
| Precision@k, nDCG, document-level recall, conflict coverage | Not implemented; precision needs exhaustive labels | **P13.1 built nDCG (informational), document recall, conflict coverage**; precision waits for the `exhaustive` flag reviewed per item (P13.2) |
| Retrieval stage latency distribution | Not summarized | **P13.1 summarizes trace timings** (evaluation-plan §15) |
| Cost per question | No paid provider configured | Provider-reported tokens × owner-supplied prices, only for an optional BYOK run |

## 7. Quality gaps, ranked by impact

Impact = how often the product is wrong or unhelpful × how badly, from the evidence above.

| # | Gap | Evidence | Impact |
| - | --- | -------- | ------ |
| **G1** | **The relevance floor is effectively always met, so deterministic abstention never fires.** The floor admits any item the lexical channel matched; since retrieval-2 the lexical channel matches *any single question lexeme*, and a visible account named in the question matches *every chunk of that account*. The floor was designed when lexical matching required every term (implementation-plan §8 correction log). | 13/13 expected-abstain runs (insufficient evidence, inaccessible, prospect probes) reached the model with up to 12 chunks; 0 items withheld by the floor in run A. "What is Acme's NPS?" is answered from Acme chunks that do not contain an NPS | Highest: abstention depends entirely on an unmeasured model; a confident model answers from loosely related evidence. Product promise "honest about gaps" (PR-2) |
| **G2** | **Multi-document evidence is collected poorly.** | R@10 0.900 single-target vs 0.437 for ≥3-target items; context recall 0.701 (6 runs with no relevant evidence in the prompt); `critical_scenario` R@10 0.083; Phase 5 found 14–15 targets no channel retrieves within 50 (multi-hop/vocabulary) | High: synthesis questions (cross_source, cross_document, conflicts) are the product's core use |
| **G3** | **Semantic-only evidence can never reach the prompt** (`SEMANTIC_FLOOR` empty). Real embeddings only reorder lexically matched chunks; paraphrase-only evidence is retrieved, shown in the list, never used. G1 and G3 together: the floor is too loose for lexical and absolute for semantic | 98 items withheld by the floor in run B; Phase 5: semantic channel alone places 52–54% of targets in its top 10 | High once a real embedding model is used; invisible in CI (fake) |
| **G4** | **Fusion and selection lose found evidence.** Equal-weight RRF ignores per-channel reliability; a semantic list crowds lexical-only (confidential, never embedded externally) chunks out of the pool; selection drops fused top-10 targets | Gemini: 17 targets in some channel's top 10 lost; local: 9; run A: 4 fused-top-10 targets lost at selection; `ai_egress` 0.486 → 0.181 under an `internal`-approved semantic list | Medium-high |
| **G5** | **Lexical ranking ties.** Title/heading terms (weight A) repeat in every chunk of a document; the account term adds +1 to every chunk of the account; no length or proportion normalization | Phase 5: 52 lexical-matched targets ranked out; 37 targets (local) at channel ranks 11–50 | Medium |
| **G6** | **Conflict detection is model-only; conflict coverage is unmeasured.** No deterministic proposal of candidate disagreements; nothing ensures both sides reach the context | `potential_conflict_found` 0/16 by construction; supersession 7/8 (one side not retrieved) | Medium (correctness of high-stakes answers) |
| **G7** | **Temporal handling is shallow.** Regex intent → recency boost only; no in-text dates ("last month", "in Q3"); staleness thresholds fixed per kind | staleness 6/8; `stale` R@10 0.619 | Medium-low |
| **G8** | **Evaluation cannot yet support tuning.** 89 items (3 Veridian, 5 scoped, no paraphrase pairs, binary labels); one set used for both tuning and reporting (overfitting risk); labels authored with the dataset by engineering, not independently verified; overall-only CI floors with 2–3 points of slack; no per-item baseline diff; fake run not cross-platform reproducible; context recall, precision, nDCG, conflict coverage, stage latency not reported | §5, §6.1 | Enabling: every other gap needs it first |
| **G9** | **Answers degrade to weak drafts under egress.** When the decisive evidence is above the egress level, loosely related internal evidence still passes the floor and is drafted from (with the egress notice) instead of `evidence_only` | 4/4 expected `evidence_only` runs answered; `egress_notice_when_held_back` 1.0 | Medium-low (honest notice exists; draft is weak) |
| **G10** | **No real-model answer measurement exists.** | §6.3 | Cannot be closed at ₹0 without a user-supplied key; bounded by D-43 |

Not a gap: security. Every security metric is 0 in every configuration measured since
Phase 5, and at HEAD.

## 8. Security invariants (non-negotiable for Phase 13)

| ID | Invariant | Phase 13 guard |
| -- | --------- | -------------- |
| S1 | SEC-1/SEC-3: every retrieval statement — including any new expansion, second-hop or diversity query — carries tenant, lifecycle and `readable_documents_predicate` in its own WHERE clause; no retrieve-then-filter | `test_sql_structure.py` extended to every new statement; property test with RLS bypassed covers it |
| S2 | E4 runs over every candidate of every stage (new stages included) and over the evidence; it aborts, never filters | Tripwire tests per new stage |
| S3 | SEC-2: no cross-tenant identity or merge; Veridian/Halden twins stay in the golden set | `cross_org` items + new expansion probes |
| S4 | SEC-5: every new decision (relevance gate, abstention, expansion, conflict pre-pass) is computed from permitted evidence only, so its outcome is identical with and without unreadable documents | "same result with and without a hidden strong match" tests for each new decision |
| S5 | D-8/D-10: hidden accounts never match, boost, expand, disambiguate or appear; account-aware features use the *visible* account only | Golden `hidden_accounts`, `account_metadata_hidden` |
| S6 | D-13/D-15/SEC-10: anything sent to a provider (query text, **query expansions**, judge inputs) obeys the egress level. Text derived from evidence carries that evidence's classification: an expansion built from `confidential` evidence is never sent to a provider approved for `internal` | New `expansion_egress_violations` metric = 0 |
| S7 | D-14: no signal from unreadable successors; ranking never infers supersession | `test_successor_visibility.py` |
| S8 | SEC-7/SEC-13: claim-level fail-closed validation is never relaxed to raise answer rates; regeneration stays at most one | Validator adversarial suite (P13.7) |
| S9 | SEC-6: identifiers and instructions inside evidence never steer authorization; if any expansion uses evidence text (D-40), injected identifiers can only reach documents the reader may already read, and the expansion is bounded and traced | Injection golden items + new planted-identifier fixture |
| S10 | SEC-8/SEC-14: new trace fields and reports hold IDs, codes, counts and scores of permitted rows only; no question or evidence text in logs | Canary checks over payloads, answers, traces, logs |
| S11 | D-18, SEC-15, D-34, P12.5 call-time re-check: unchanged | Existing suites stay green |

A checkpoint whose security metrics are not all 0 does not close.

## 9. Evaluation contracts

**EC-1 Layers stay separate** (evaluation-plan §1). Retrieval metrics never use a
model; answer-plumbing metrics use fake/scripted generators and are labelled as such;
real answer quality is reported only from a real provider run, never inferred from the
fake.

**EC-2 Configurations.**

| ID | Configuration | Role | Where |
| -- | ------------- | ---- | ----- |
| A | Exact + lexical | Deterministic floor; bit-reproducible; gates ranking changes | CI and local |
| B | Fake embeddings + fake generation | Security and plumbing gate only; **never a quality figure** | CI |
| C | Local `mxbai-embed-large-v1`, approved `restricted` | Reference real semantic configuration (zero cost); decides fusion/semantic changes | Local, manual, isolated DB (D-38) |
| C′ | Local mxbai approved `internal` | The external-provider egress shape without egress; measures G4's confidential crowd-out | Local, manual |
| D | External BYOK provider | Optional, synthetic data only, user-supplied key, manual, never CI (D-43) | Local, manual |

**EC-3 Retrieval metrics** (item-macro over items with targets; targets as in
evaluation-plan §7):

| Metric | Definition | Supported by labels? |
| ------ | ---------- | -------------------- |
| Recall@k, k ∈ {1, 5, 10, 20} | Targets found in the first k evidence chunks | Yes (existing) |
| Document recall@k | Target *records* with any chunk in the first k | Yes |
| MRR | 1 / rank of the first target | Yes (existing) |
| Precision@k | Fraction of the first k chunks that satisfy `relevant ∪ acceptable` | **Only items flagged `exhaustive: true`** after review (P13.2); never averaged with others |
| nDCG@10 | Gains relevant = 2, acceptable = 1, unlabelled = 0 | Informational only: unlabelled ≠ irrelevant, so it is a lower bound |
| Context recall | Relevant targets that reach the prompt | Yes (answer pipeline, no model needed beyond fake) |
| Conflict coverage | For items with `conflict.records`, all sides in the prompt | Yes, where records are labelled |
| Channel attribution | Per target: best rank per channel, fused rank, rerank delta, selection fate | Yes (existing miss diagnosis, extended) |
| Rerank contribution | Metrics with `rerank_v1` vs RRF order only (ablation flag) | Yes |
| Exact-identifier hit rate | Items with an identifier: the record at rank 1 | Yes |

**EC-4 Answer metrics.**

| Metric | Deterministic? | Source |
| ------ | -------------- | ------ |
| Status accuracy (per expected status, confusion matrix) | Yes | Fake/scripted for pipeline decisions; real provider for model decisions |
| Abstention gate: catch rate on expected-abstain items, false abstention on answerable items | Yes | Pipeline (G1) |
| Claim validity, rejection codes, regeneration, withheld answers | Yes | Existing |
| Validator precision/recall on adversarial drafts (unsupported claim, wrong number, unrelated quote, foreign handle, cross-claim citation) | Yes | Scripted provider, authored drafts (P13.7) |
| Citation completeness: direct-answer claims citing every expected record they need | Judge or real provider | D only |
| Required-fact coverage, forbidden claims, unsupported-claim rate, presented-as-resolved | Judge | D only; gates nothing until calibrated (D-44) |
| Multi-hop use: answers citing ≥2 expected records on multi-record items | Yes given a real draft | D only |

**EC-5 Security metrics** (each must be 0; never averaged): the existing retrieval and
answer sets (evaluation-plan §5, §7, §13) plus `expansion_egress_violations`,
`expansion_unauthorized` (any expansion stage), and a per-decision equivalence check
(S4).

**EC-6 Reproducibility and regression.**
- A committed, compact **item-level baseline** (per item: per-target first rank, R@k,
  status; per run: config version, corpus fingerprint = chunk count and chunker/embed
  input versions, golden version). Full reports stay uncommitted (`.gitignore`).
- `lattice eval compare` against it: configuration A must match exactly unless the
  change intends it; B per item with every difference listed; C/D reported, not gated
  in CI.
- Regression rule (evaluation-plan §7, made per item): any category R@10 drop > 2 points
  on A or C, any item losing all its targets, or any security metric > 0 must be
  explained in the checkpoint notes; the security case blocks.

**EC-7 Splits.** Tuning (weights, thresholds, gates) uses a **dev** split only. A
**held-out** split is reported at each checkpoint and never used to choose a value.
Changing a held-out item's labels after seeing results needs the product owner.

**EC-8 Label provenance.** Every golden item records `label_source`
(`authored_phase3`, `authored_phase13`, `owner_reviewed`). Labels authored by
engineering are the maintainers' expectations, not independently verified ground truth;
reports state the provenance mix. No label is changed to improve a score; a label error
is fixed in its own commit with the evidence.

**EC-9 Performance.** Retrieval stage timings (accounts, exact, lexical, embed_query,
semantic, tripwire, fuse_rerank, hydrate, total) p50/p95 from traces; prompt items and
characters; generation calls, provider-reported tokens. Latency figures name the machine
and configuration; the M1 is not a production benchmark.

### 9.1 Evaluation refinements (product owner, 2026-10-21)

Binding on every checkpoint, in addition to EC-1–EC-9:

- Category-level **and** item-level regressions stay visible in every report and compare;
  an aggregate gain never hides an item or category that got worse.
- Every category and status figure is reported with its denominator.
- Percentage changes in very small slices (a few items) are not over-interpreted: the
  item-level change is the finding, not the percentage.
- Nothing is ever tuned against the held-out split (EC-7).
- Every authorization or egress security violation is an unconditional blocker, in
  every configuration and split.

## 10. Evaluation dataset

**Current set** (`evals/golden/`, 89 items): 14 categories, 11 personas (86 Halden, 3
Veridian), 10 same-question groups, 7 egress-differentiated items, 51 tagged `security`;
statuses expected: answered 67, insufficient_evidence 13, partial 4, evidence_only 4,
ambiguous 4, unsupported_request 4 (over 96 runs); 140 required facts on 63 items; 144
`must_not_retrieve`, 247 `acceptable` keys; 16 potential-conflict and 8 supersession
expectations; binary relevance; 5 scoped items; 9 questions with identifiers.

**Additions proposed (P13.2), ~60–80 items, authored against the existing corpus first;
new corpus records only where a class cannot be expressed otherwise:**

| Class | Why | Labels |
| ----- | --- | ------ |
| Paraphrase pairs (same need, different words) | Separates lexical from semantic gains (G3) | Same targets; `paraphrase_group` |
| Keyword-heavy vs natural-language variants | Lexical ranking (G5) | Same targets |
| Exact identifier variants (lower case, inline, several IDs) | Exact channel and fusion | Record at rank 1 |
| Ambiguous account names, incl. alias collisions and hidden-account collisions | Account detection, S5 | `clarification`, `hidden_accounts` |
| Multiple supporting documents (≥3 records) | G2 | Per-record targets; `exhaustive` where complete |
| Contradictory evidence without supersession | G6 | `conflict.records` for every side |
| Outdated vs current (supersession) and "as of" questions | G7, S7 | `freshness`, `superseded` |
| Insufficient evidence near a relevant topic (same account, missing fact) | G1 false-answer risk | `insufficient_evidence` |
| Must-abstain under egress (decisive evidence above the level) | G9 | `evidence_only` |
| Restricted documents that are the strongest match | S1, S4 | `must_not_retrieve`, `trap_phrases` |
| Multi-account comparison questions | Account scoping | Targets per account; visibility per persona |
| Scoped questions (account, source type, dates) | Scope narrowing | `scope` |
| Misleading or adversarial source text (planted identifiers, fake "decision", instructions) | S9, D-40 | `must_not_contain`, `forbidden_claims` |
| More Veridian items | Tenant twin coverage | `cross_org_*` tags |

Each item distinguishes: **retrievable relevant evidence** (`relevant`), **authorized
relevant evidence** (checked by the lint against the reference policy per persona),
**expected answer facts** (`required_facts`), **acceptable citations** (`relevant ∪
acceptable`) and **expected refusal/abstention** (`status`, `status_reason`). New
fields: `label_source`, `split` (`dev` | `held_out`), `exhaustive`, `paraphrase_group`.
Split: stratified by category; same-question and paraphrase groups stay in one split;
held-out ≈ 35%.

**As built (P13.2, 2026-10-24).** 70 new items against the existing corpus (no record was
added: every class could be expressed with the Phase 3 data), 159 in total. The fields
above plus `classes` (the §10 class vocabulary, lint-checked against the labels),
`exhaustive_review` (the documented candidate review) and `owner_review` (D-41 batch and
status); `paraphrase_group` means "the same information need", not only rewording.
Rules and procedures: evaluation-plan §3.1; results §16; delivery record §20.

## 11. Proposed checkpoints

Each checkpoint is one reviewable commit series on this branch, stops for approval, and
records its measurements in evaluation-plan §14+. "Re-index" means `lattice reindex`
(no re-embedding); "re-embed" means new vectors.

### P13.1 — Evaluation foundation

- **Objective.** Make every later change measurable, reproducible and comparable.
- **Scope.** Harness metrics of EC-3/EC-4 that need no new labels (document recall,
  context recall, conflict coverage, abstention catch/false-abstention, rerank ablation,
  identifier hit rate, nDCG informational, stage latency, prompt size); item-level
  baseline file and `lattice eval compare`; CI compares configuration A exactly (cheap:
  one extra eval run on the already-synced corpus) and B per item; investigate the
  M1/x86 difference of B and fix the harness or document the tie; fix the stale
  `retrieval/lexical.py` module docstring (§17); evaluation-plan §5 table states what is
  built.
- **Non-goals.** Any retrieval or answering behaviour change; new golden items.
- **Prerequisites.** P13.0 approved (2026-10-21).
- **Acceptance.** Baseline committed; compare reproduces this plan's A numbers exactly
  and flags an injected regression (test); the M1/x86 B difference explained or the
  item recorded as platform-variant; CI time increase ≤ 1 minute; security 0.
- **Security regressions to watch.** Reports must not add question or evidence text to
  logs (S10); compare output uses record keys of synthetic data only.
- **Resources.** CPU-only, seconds per run; no provider calls.
- **Status: delivered 2026-10-21** (§19). Acceptance: baselines committed; A reproduced
  exactly on aarch64 and x86-64; injected regression flagged (golden and synthetic
  tests); the B difference explained and recorded per item and per architecture; CI
  increase ≈ 16 s measured on the M1; security 0 in every run.

### P13.2 — Golden set growth, splits and label provenance

- **Objective.** A set large and varied enough to tune on without overfitting.
- **Scope.** §10 classes; `label_source`, `split`, `exhaustive`, `paraphrase_group`;
  lint rules for the new fields (authorized-relevant check per persona, split
  integrity); minimal new corpus records with canaries where a class needs them;
  baseline regenerated for the larger set (A and B; C if D-38 approved).
- **Non-goals.** Pipeline changes; relabelling Phase 3 items (except errors with
  evidence, separate commit).
- **Prerequisites.** P13.1; D-41 (who reviews labels).
- **Acceptance.** ≥ 150 items; every class in §10 has ≥ 4 items; held-out ≈ 35%
  stratified; lint passes; security 0 on A and B; owner review recorded per D-41.
- **Security regressions.** New restricted/confidential records carry canaries and
  `must_not_retrieve` for every persona that may not read them; Veridian twins updated.
- **Resources.** Authoring time; re-sync seconds (A/B), ~6 min for C.
- **Status: engineering complete 2026-10-24, owner review pending** (§20). 159 items (70
  new, no corpus change); every §10 class has ≥ 4 new items; held-out 56/159 (35.2%),
  group-aware; lint passes; security 0 on A, B (both architectures) and answer A; 56
  held-out items in four review batches, none reviewed yet, so no held-out gate that
  needs owner approval is active.

### P13.3 — Relevance gate and abstention calibration (G1, G3, G9)

- **Objective.** Deterministic abstention that fires when the permitted evidence does not
  address the question, without refusing answerable questions.
- **Scope.** Replace "lexical matched" in `_relevant` with an evidence-strength rule
  computed from the candidate's own signals: content-lexeme coverage of the question
  (the account term and stopwords excluded), identifier match, and a semantic floor
  calibrated per model on the dev split (only for C; external models stay uncalibrated
  until measured). Answer-level gate: no item above strength → `no_relevant_evidence`;
  decisive evidence held back by egress with only weak remainder → `evidence_only`
  (G9). Trace records the gate's per-item reason codes.
- **Non-goals.** Model-based relevance judgement; changing what retrieval returns to the
  asker (items below the gate stay in the evidence list).
- **Prerequisites.** P13.1, P13.2 (abstain classes, splits), D-39 (trade-off), D-38 for
  the semantic floor.
- **Acceptance (proposed; D-39 sets final values).** On held-out: ≥ 60% of
  expected-abstain items stop before any model call; ≤ 5% false abstention on answerable
  items; context recall not lower than baseline by > 2 points; security 0; S4
  equivalence test for the gate.
- **Security regressions.** The gate must not use unreadable information (S4); a hidden
  strong match must not change the status (SEC-5).
- **Resources.** A/B seconds; C one query-side run (~30 s) on the dumped corpus.
- **Status: delivered and accepted 2026-10-27** (cb82ce2; §21). Dev: A 11/17 caught, 4/81 refused; C 11/17,
  2/81 -- D-39's provisional targets met on dev. Held-out (reported only): 4/8 caught in
  A and C, 6/45 (A) and 3/45 (C) refused -- not met; context recall falls more than 2
  points except on C dev. Security 0; S4 equivalence test added.

### P13.4 — Lexical candidate quality and ranking (G5)

- **Objective.** Better first-stage lexical ordering.
- **Scope.** One change at a time, each measured on dev then held-out: title/heading
  lexemes counted once per document (score-side if possible, else `tsv` change →
  re-index, not re-embed); account term weighted below content terms; coverage
  normalized by question length; `simple`-config title vector only if the stemmer
  conflation is shown. `RetrievalConfig.version` bumps per change.
- **Non-goals.** Changing chunking or embedding input (that is a re-embed; D-45).
- **Prerequisites.** P13.1–P13.2.
- **Acceptance.** A: R@10 and MRR up on held-out with no category down > 2 points; C
  not worse; security 0; `EXPLAIN` of the lexical statement within the §13 budget.
- **Security regressions.** D-10: account terms remain query-time and visible-only;
  the tsv still carries no account names.
- **Resources.** Re-index of the eval corpus (seconds); no re-embed.
- **Status: delivered 2026-10-27, no change adopted** (§22; evaluation-plan §18). No
  variant met the held-out rule; `retrieval-6` stays; the variants remain evaluation
  tunables (`lattice eval retrieval --set`).

### P13.5 — Fusion, selection and multi-document evidence (G4, part of G2)

- **Objective.** Keep what channels found and cover more distinct records.
- **Scope.** Per-channel RRF weights or query-shape weights (identifier queries, short
  keyword queries); a guaranteed share of the pool for lexical-only chunks when
  semantic ran (C′ crowd-out); document-diverse selection (record coverage before depth)
  replacing the fixed ≤3 cap; evidence limit vs prompt budget re-examined (retrieval
  returns 12 to Ask, the eval asks for 20); rerank_v1 weights tuned on dev only or
  confirmed as is.
- **Non-goals.** A model reranker (§2).
- **Prerequisites.** P13.4; D-38 (C and C′ are the deciding configurations).
- **Acceptance.** C: R@10 and context recall up on held-out; ≥3-target items improve;
  supersession and potential-conflict categories not down (the cross-encoder failed
  here); A not down > 2 points per category; security 0.
- **Security regressions.** Confidential lexical-only evidence gains rank but stays
  subject to the egress level when the prompt is built (S6).
- **Resources.** C query-side runs only (corpus reused).
- **Status: delivered 2026-10-27, no change adopted** (§23; evaluation-plan §19). The
  dev choice (lexical ×1.25 in fusion) lost C R@10 on held-out; rerank_v1 confirmed.

### P13.6 — Multi-hop evidence expansion (G2)

- **Objective.** Reach evidence that does not resemble the question ("What commitments
  have we made to Acme?" → the SCIM decision record).
- **Scope (only if D-40 is approved).** One bounded, deterministic second pass: from the
  top-k *permitted* evidence, (a) record references (identifiers, explicit links,
  readable supersession neighbours) and (b) salient terms (pseudo-relevance feedback)
  build a second exact/lexical query; semantic only if the expansion's classification
  (max of its source evidence) is within the provider's level; results fused with a
  lower weight; every statement carries the predicate; E4 over every candidate;
  expansion recorded in the trace as IDs and counts.
- **Non-goals.** LLM decomposition; more than one hop; following URLs.
- **Prerequisites.** P13.5; D-40.
- **Acceptance.** Multi-hop targets (Phase 5's "no channel within 50" class) found @10
  for ≥ 30% of them on held-out; no category down > 2 points; injection fixture shows a
  planted identifier reaches only readable records and never an `answered` claim
  without valid citations; `expansion_*` security metrics 0.
- **Security regressions.** S6 (egress of evidence-derived text) and S9 (injection
  steering) are the new risks; both have dedicated tests.
- **Resources.** One extra SQL round trip per question (budget §13).
- **Status: D-40 approved 2026-10-27; built as designed, not adopted** (§28;
  evaluation-plan §23). No variant improved dev in A or C; held-out not used. Off in the
  product; an evaluation tunable with its security tests.

### P13.7 — Context construction and grounded-answer verification (G2, G8 answer side)

- **Objective.** Give the model the right evidence in a usable order, and prove the
  validator catches unsupported answers.
- **Scope.** `answer-prompt-2` (versioned): evidence grouped by record with neighbour
  context, explicit supersession and readable-link attributes, question facets,
  partial/unknown guidance; adversarial **validator suite** with scripted drafts (EC-4)
  measuring rejection precision/recall; citation completeness check (a direct answer
  that cites none of the decisive records becomes `partial`, deterministic where labels
  allow); budget re-tuned on prompt size.
- **Non-goals.** Relaxing validation (S8); choosing D-4.
- **Prerequisites.** P13.3, P13.5 (P13.6 if approved).
- **Acceptance.** Validator suite: 100% of authored unsupported/foreign/number cases
  rejected, ≤ 5% of authored valid claims rejected; context recall ≥ baseline; prompt
  ≤ 24,000 evidence characters; all fake-pipeline checks ≥ baseline; security 0.
  Real answer quality only under D-43.
- **Security regressions.** Prompt changes never add access metadata, IDs, hidden
  accounts or other tenants; nonce delimiters and injection tests unchanged.
- **Resources.** Zero cost (scripted provider).
- **Status: delivered 2026-10-27** (§25; evaluation-plan §20). Validator suite 31/31
  invalid rejected, 0/30 valid rejected after two in-rule fixes; `answer-prompt-2`;
  citation completeness measured in the trace, not enforced; answer A identical.

### P13.8 — Conflicts and temporal reasoning (G6, G7)

- **Objective.** Find disagreements deterministically and answer "current"/"as of"
  questions honestly.
- **Scope.** Deterministic conflict *candidate* pre-pass over the selected evidence
  (same visible account or record family + shared topic terms + differing dates or
  numbers), passed to the model as a hint and verified exactly as today (never shown
  as resolved); conflict coverage guarantees both sides reach the context; in-text date
  expressions as narrowing scope (never widening) with a visible note; staleness
  thresholds per authority as configuration.
- **Non-goals.** Declaring winners; inferring supersession from dates (S7).
- **Prerequisites.** P13.5, P13.7.
- **Acceptance.** Conflict coverage on held-out ≥ 0.8; supersession found 100% where
  both sides are retrieved and 0 unfounded; staleness check ≥ baseline; security 0.
- **Security regressions.** The pre-pass reads permitted evidence only (S4); date
  filters only narrow.
- **Resources.** Negligible.
- **Status: delivered 2026-10-27, no behaviour change** (§26; evaluation-plan §21).
  Supersession meets its target already; conflict coverage (dev 0.57, held-out 0.53) is
  bound by retrieval; the candidate pre-pass fired on 86% of unlabelled runs and was
  rejected; in-text date narrowing not built (dates are often content).

### P13.9 — Regression, performance, optional real-model evaluation and closure

- **Objective.** Show the phase improved quality without regressions and close it.
- **Scope.** Final A/B/C runs on dev and held-out; per-item comparison to the P13.0
  baseline; latency and prompt-size budgets (§13); full backend suite, golden suites,
  mutation check of new security checks; optional D run and judge calibration only if
  D-43/D-44 approved; docs (retrieval-design as built, evaluation-plan results, ADR for
  any lasting design change); closure report.
- **Non-goals.** New features.
- **Prerequisites.** P13.1–P13.8 (P13.6 only if approved).
- **Acceptance.** §16.
- **Resources.** One full backend suite (~17 min, alone), one C run, CI once at the
  closure push.
- **Status: engineering closure 2026-10-27** (§27; evaluation-plan §22). Completion
  criteria 1, 2, 4, 5 met; 3 not met (no G2–G6 gain survived held-out); 6–8 wait on the
  owner (D-41 review, D-43, the closure push).

## 12. Priorities and dependencies

```
P13.1 eval foundation ──► P13.2 golden growth/splits ──► P13.3 relevance gate & abstention
                                         │
                                         └──► P13.4 lexical ──► P13.5 fusion/selection ──► P13.6 multi-hop (D-40)
                                                                        │                         │
                                                                        └──► P13.7 context & validator ◄┘
                                                                                     │
                                                                                     └──► P13.8 conflicts/temporal ──► P13.9 closure
```

Priority by impact (§7): P13.3 (G1) is the first behaviour change because a wrong
confident answer is worse than a missed document; P13.4–P13.6 address G2–G5; P13.7–P13.8
the answer side. P13.3 and P13.4 touch different modules and could be reviewed in either
order after P13.2.

## 13. Performance constraints

| Budget | Current (M1, local DB) | Phase 13 limit |
| ------ | ---------------------- | -------------- |
| Retrieval (no external embedding), p95 | Each statement < 7 ms (Phase 5 `EXPLAIN ANALYZE`); whole Ask p95 40 ms (A) / 92 ms (B) with fake generation | ≤ 150 ms p95 on the golden set (retrieval-design §12) |
| SQL statements per question | ≈ 10 (accounts, egress settings and records, exact, lexical, semantic, E4 ×2, hydrate, classifications) | ≤ +2 (expansion) |
| Prompt evidence | ≤ 12 items, ≤ 24,000 chars | unchanged unless P13.7 shows a gain; never above |
| Generation calls per Ask | ≤ 2 | unchanged |
| Model calls added by retrieval | 0 (besides one query embedding) | 0 |
| New indexes / migrations | — | only with an `EXPLAIN` showing need; any `tsv` change ships with a re-index procedure |

## 14. Resource and cost

| Work | Cost | Machine load (M1, 8 GB) |
| ---- | ---- | ----------------------- |
| A/B evals (CI and local) | ₹0 | seconds; one PostgreSQL at a time |
| C (local mxbai) corpus embedding | ₹0 | ~6 min, ~1.5 GB RSS; alone, never with the test suite; dump the eval DB (~10 MB) and restore instead of re-embedding until chunking or embedding input changes |
| C query side | ₹0 | ~30 s for 89 items |
| Full backend suite | ₹0 | ~17 min; alone |
| CI | GitHub Actions minutes | one push at Phase 13 closure (as in Phases 11–12); +≤1 min per run for the A compare |
| D (BYOK) | owner's key and quota | synthetic data only; estimate before running: ~96 runs × (~2k input + ~0.5k output tokens) ≈ 200k input / 50k output tokens per full answer eval, ~2× with the judge |
| Ollama / local LLM | — | **not used** |

## 15. Decisions (approved by the product owner, 2026-10-21)

The P13.0 recommendations were reviewed; these are the binding decisions.

| ID | Question | Decision |
| -- | -------- | -------- |
| **D-38** | Reference real configuration for Phase 13 decisions | **Approved:** one isolated local `mxbai-embed-large-v1` embedding reference per corpus version (C, and C′ from the same vectors where the egress shape needs it), memory-controlled (run alone on the M1, never with the test suite), dumped and reused until the corpus, chunker or embedding input changes. **No local LLM generation.** |
| **D-39** | Abstention trade-off for the deterministic gate | **Provisional acceptance targets:** expected-abstain catch rate ≥ 60%, false abstention ≤ 5% of answerable items. Calibrated on the **dev split only**; held-out results reported with denominators and uncertainty |
| **D-40** | May retrieval use identifiers and terms from permitted retrieved text for one bounded second pass? | **Approved in principle, not implementation authorization:** one bounded, deterministic second-hop design, to be reviewed at P13.6 before any code. Every candidate is authorized inside SQL; evidence-derived text inherits its sensitivity; no unapproved external egress |
| **D-41** | Who reviews new golden labels | **Engineering prepares labels; the product owner reviews held-out items in batches** before they become binding. Unreviewed items are never marked `owner_reviewed` |
| **D-42** | What Phase 13 must achieve against R@10 ≥ 0.90 | **0.90 remains a product objective, not a mandatory Phase 13 closure score.** Closure requires genuine held-out improvements without hidden regressions |
| **D-43** | Any real-model answer evaluation in Phase 13? | **No real-generation-model evaluation without a separate explicit approval and a cost estimate** |
| **D-44** | Judge calibration | **Deferred** unless D-43 is approved |
| **D-45** | May Phase 13 change chunking or embedding input? | **No by default.** Requires measured gains and explicit approval before imposing re-embedding costs |

## 16. Completion criteria (Phase 13)

1. Every security metric 0 in every configuration and split run, at every checkpoint
   (EC-5); new statements covered by SQL-structure and property tests (S1–S10).
2. Held-out results reported for A and C (if D-38) against the P13.0 baseline per
   item; no category R@10 drop > 2 points unexplained.
3. G1 closed to D-39's targets; G2–G5 each improved on held-out (context recall and
   ≥3-target recall reported); G6 conflict coverage ≥ 0.8; G7 staleness ≥ baseline.
4. Validator adversarial suite at its acceptance level; claim-level fail-closed
   unchanged.
5. Latency and prompt budgets (§13) met.
6. Golden set ≥ 150 items with provenance and splits; owner review per D-41.
7. Real answer quality either measured under D-43 (with calibrated judge per D-44) or
   explicitly recorded as **not measured**.
8. Docs as built: retrieval-design, evaluation-plan, this plan's Part III, ADRs for
   lasting changes; full backend suite, web untouched or green, CI green once at closure.

## 17. Mismatches found in P13.0 (to fix in P13.1, not here)

| Where | Mismatch |
| ----- | -------- |
| `retrieval/lexical.py` module docstring | Says the query is parsed with `websearch_to_tsquery`; the default mode is `coverage` (any lexeme) since retrieval-2 — **fixed in P13.1** (docstring only) |
| evaluation-plan §5 | Lists Precision@10, document-level recall, context recall and conflict coverage as metrics; none is computed by the harness — **fixed in P13.1**: §5 marks what is built; all but Precision@10 are computed |
| `answering/service.py` relevance floor vs implementation-plan §8 correction log | The floor's "lexical match" assumed all-terms matching; with any-lexeme matching and the account term it no longer filters (G1) |
| `tests/retrieval/golden/test_golden_retrieval.py` | Comment says fake R@10 "measured 0.512"; CI on x86 measures 0.517 (§6.1) — **fixed in P13.1** (both values, and per-item baselines added) |

Status lines made stale by the Phase 12 merge (implementation-plan "Current state" and
roadmap rows 12–13, README, phase-12-plan header) are corrected in the P13.0 commit.

## 18. Carried forward (not Phase 13)

D-4 default generation provider (open; D-43 is evaluation only); external staging
(Phase 15 gate); ANN index (only above the §7.6 threshold, Phase 14); timing side
channels (T16, accepted); account timeline workspace (post-v1).

## 19. P13.1 delivery record (2026-10-21)

Measurement only. Production retrieval, ranking, relevance gate, answering, providers and
authorization are unchanged; the only edit under `backend/src/lattice/retrieval/` is the
`lexical.py` module docstring (§17). Full results: evaluation-plan §15.

| Item | Result |
| ---- | ------ |
| Metrics added | Document recall@k; nDCG@10 (informational); exact-identifier hit; slices by number of targets; rerank ablation (`--no-rerank`); retrieval stage latency; context recall; conflict coverage; abstention catch rate and false abstention with denominators and Wilson intervals; status confusion matrix; prompt size and budget use; withheld evidence by reason. Precision@10 reported as not computed |
| Baseline reproduced | A: R@10 0.6679, MRR 0.6405, identical per item on aarch64 and x86-64; answer A: context recall 0.701, catch 0/13, false abstention 0/71 |
| B across platforms | Explained (float32 distance rounding + exact ties in fake vectors); 22 items rank differently, 1 target outcome; one exact baseline per architecture, `--cross-platform` lists them |
| Committed baselines | `evals/baselines/retrieval-A.json`, `retrieval-B-aarch64.json`, `retrieval-B-x86_64.json`, `answer-A.json` |
| Compare | `lattice eval compare` / `--compare`: refuses incomparable runs (exit 2), blocks on security (exit 1), fails unexplained drift (exit 1) and regressions under declared changes (exit 1); never overwrites a baseline silently |
| CI | B compared with the x86-64 baseline; A compared exactly; ≈ +16 s (M1 measurement) |
| Security | Every security metric 0 in A, B (both architectures), the ablations and answer A; baselines and compare output hold keys, ranks and codes only (a golden test checks no question text appears) |

New findings recorded for later checkpoints (no behaviour changed): the prompt's 12-item
limit binds in 78/89 prompts while evidence uses ~14% of the character budget (P13.7);
potential-conflict coverage is 3/16 (P13.8); rerank_v1 adds MRR (+0.058 in A) but almost
no R@10 (+0.003; P13.5); ≥3-target items reach document R@10 0.621 but part R@10 0.437 (G2).

**P13.2 prerequisites.** P13.1 approved; D-41 batch-review procedure agreed (engineering
prepares, the owner reviews held-out batches; nothing marked `owner_reviewed` before
review); the baselines are regenerated with `eval baseline --replace --reason ...` once
the golden set grows (golden version changes, so compare refuses old-vs-new unless
`--allow-golden-change`); new items keep the security fields (`must_not_retrieve`,
canaries) and the Veridian twins; a C reference (D-38) is not needed for P13.2 itself but
is for P13.3's semantic floor.

## 20. P13.2 delivery record (2026-10-24)

Dataset and evaluation infrastructure only. Production retrieval, ranking, relevance
gate, answering, providers, ingestion, chunking and authorization are unchanged; no
tunable was touched; no corpus record was added. Full results: evaluation-plan §16; rules
and procedures: evaluation-plan §3.1; runbook "Grow or change the golden set", "Apply the
product owner's review", "Reproduce configuration B on x86-64", "Configuration C".

| Item | Result |
| ---- | ------ |
| Golden set | 159 items (89 Phase 3 + 70 new), 171 runs, 124 with targets; every §10 class has ≥ 4 new items (lint-enforced ≥ 4 per class overall) |
| New fields | `label_source`, `split`, `paraphrase_group`, `classes`, `exhaustive` + `exhaustive_review`, `owner_review` |
| Splits | dev 103 / held-out 56 (35.2%), 89 units, group-aware and stratified; every category and class on both sides; no unit or near-duplicate straddles; 19 target records shared across splits (reported) |
| Provenance | authored_phase3 89, authored_phase13 70, owner_reviewed **0**. Phase 3 labels frozen at their 22fd800 digests (89/89 matched); held-out labels frozen before tuning |
| Owner review (D-41) | Packet `docs/golden-review/` (H1 14, H2 11, H3 15, H4 16 = 56 items: 34 Phase 3, 22 new), all pending |
| Exhaustive | 8 items (3 dev, 5 held-out), engineering-reviewed; P@10 A 0.613, B 0.550, C 0.675 (ideal 0.825) |
| Lint | Provenance and freezes, splits, groups, near-duplicates, owner-review records, classes, exhaustive reviews, contradictory constraints, egress pairs, scope dates; 18 new mutations |
| Baselines | A, B aarch64, B x86-64, answer A regenerated (`--replace`, reasons, P13.1 digests recorded); previous items: 0 item-level findings in each; new `retrieval-C-aarch64.json` |
| C (D-38) | Local mxbai approved restricted, whole corpus embedded once (6 min 54 s, watchdog), snapshot dumped and restore-verified; R@10 0.702 (Phase 3 items 0.695 = Phase 5) |
| Security | Every metric 0 in A, B (both architectures), C and answer A; a new golden test hides each new strong trap and proves the persona's evidence identical (S4) |

**Decisions or blockers before P13.3.** (1) The owner review of the 56 held-out items
(D-41): until approved, held-out results are reported but gate nothing. (2) Two findings
need a decision (evaluation-plan §16): the Orbital name in the public beta plan vs
`visibility-orbital-maya`, and three account-scoped Phase 3 items whose relevant evidence
the scope excludes. (3) P13.3 itself needs explicit approval; it should calibrate on dev
only and use the C snapshot for the semantic floor.

## 21. P13.3 delivery record (2026-10-27)

Answering only. Retrieval, ranking, providers, ingestion, chunking and authorization are
unchanged (retrieval A, B and C compare identical with their baselines); the answer
pipeline's relevance floor is replaced by the relevance gate. Full results and the
calibration: evaluation-plan §17.

| Item | Result |
| ---- | ------ |
| Gate (`lattice.answering.gate`, `gate_v1`) | Per item from permitted signals: identifier, weighted content-term coverage ≥ 0.50, semantic ≥ the model's calibrated floor are decisive. No decisive item → `insufficient_evidence` with no model call (G1); every decisive item held back by egress → `evidence_only` (G9); otherwise the old floor's items plus semantic items above the floor reach the prompt (G3). Ambiguous account mentions are clarified before gating |
| Weights | `ln((N+1)/(n+1)) + 1` over the asker's permitted chunks (`lattice.retrieval.terms`, one statement with the authorization predicate); content terms exclude stopwords, the visible accounts named (one account term instead), a short English framing-word list and hyphenated compounds (`lattice.retrieval.lexical`) |
| Semantic floor | `mxbai-embed-large-v1@b33106f585b9`: 0.76 (dev, C). No other model or revision has one |
| Calibration | Dev split only, A and C; four variants compared (evaluation-plan §17). The held-out split was evaluated once with an earlier variant before the final one: disclosed there |
| Results | Dev: A 11/17 caught, 4/81 refused; C 11/17, 2/81. Held-out: A 4/8, 6/45; C 4/8, 3/45. `evidence_only` 0/10 → 7/10. Context recall A 0.711 → 0.657, C 0.727 → 0.700; every lost point is a refused run |
| Trace | `retrieval.relevance_gate`: version, threshold, floor, number of content terms, total weight, decision, and per item the chunk ID, code, matched-term count, coverage and similarity -- never the terms (S10) |
| Tests | Gate behaviour and S4 equivalence (`tests/answering/test_gate.py`), SQL structure of the two new statement shapes, `eval compare` declaring a gate version older baselines lack, golden answer pins; validation tests reworded to evidence wording (the paraphrase case is now a gate test) |
| Security | Every metric 0 in A, B and C, dev and held-out |
| Cost | +1 SQL statement per Ask (a pass over the asker's permitted chunks: a Phase 14 bound is needed for large corpora); A whole-Ask p50 34 → 38 ms (M1) |

**Decisions for the product owner before P13.4.**
1. **Accept the trade-off or change it.** Dev meets D-39; held-out does not (4/8 caught;
   refusals 6/45 in A, 3/45 in C), and context recall falls more than 2 points except on
   C dev (all of it from refused runs). Options: (a) accept `gate_v1` as is; (b) gate only
   where a calibrated semantic floor exists (A keeps the P13.2 behaviour: no G1 gain
   without embeddings); (c) a lower threshold when no floor exists, re-chosen on dev
   (less caught in A). Any re-tuning uses dev only; held-out has now been seen, which the
   record would state.
2. The owner review of the 56 held-out items (D-41) is still pending, so none of the
   held-out figures above is binding.
3. The two refusal patterns (paraphrase without a shared specific word; account-centred
   questions about large accounts) are candidates for P13.5 (fusion/selection), not for
   further gate tuning.

**Owner decision (2026-10-27): option (a).** `gate_v1` is accepted as committed (cb82ce2),
unchanged, in every configuration -- with or without a calibrated semantic floor. The
recorded trade-off:

- D-39's provisional targets are met on dev (A and C) and **not met on held-out** (4/8
  caught; 6/45 refused in A, 3/45 in C). The product accepts more refused answerable
  questions than D-39 allows in exchange for stopping most unanswerable ones before any
  model call; a refusal still returns the asker's evidence list.
- Context recall falls by more than the 2-point regression rule (EC-6) in A (dev 4.5,
  held-out 7.1 points) and on C held-out (4.4); this is accepted and explained: every lost
  point is a refused run, and no run the gate passes lost a target.
- Configurations without embeddings (A) carry the larger cost (6/45 held-out refusals).
- The held-out figures stay non-binding until the D-41 review of the 56 held-out items.
- The held-out split was seen once with an earlier variant (evaluation-plan §17); any
  later gate change is calibrated on dev only and states that.
- Not re-tuned here: the two refusal patterns go to P13.5 as candidates. P13.4 is not
  authorized.

## 22. P13.4 delivery record (2026-10-27)

Authorized with the rest of Phase 13 ("finish P13", 2026-10-27). Retrieval behaviour is
**unchanged**: no lexical ranking variant met the acceptance rule, so the product keeps
`retrieval-6` and every committed baseline. Full record: evaluation-plan §18.

| Item | Result |
| ---- | ------ |
| Variants (`RetrievalConfig`, off by default) | `lexical_term_weights` (count / idf), `lexical_counted_terms` (all / content), `lexical_title_factor`, `lexical_account_factor`; the weighted statement scores each matched lexeme by its weight, a title-only match by the title factor (`'lexeme':D` matches the text), the named visible account by its factor |
| Statistics | `retrieval.terms.question_statistics`: the question's lexemes (content first, ≤ 48) with their permitted-chunk counts, one pass over the reader's permitted chunks; run only when a weighted variant is configured |
| Evaluation | `lattice eval retrieval --set NAME=VALUE` (repeatable, evaluation only; the version string names the overrides) |
| Dev choice | content terms only + account factor 0.5: dev R@10 0.722 → 0.739, MRR 0.656 → 0.679, no dev category down |
| Held-out | R@10 0.598 → 0.578, MRR 0.514 → 0.461; cross_org, stale, cross_document down > 2 points → **not adopted**. Every single change flat or worse on held-out |
| Security | Every metric 0; SQL-structure suite covers both new statement shapes; unreadable documents change no statistic (S1, S4); D-10 unchanged |
| Not done | `simple` title vector (no conflation found); C runs (nothing adopted); question-length normalization is a rescaling of the fixed bonuses (evaluation-plan §18) |

Carried to P13.5: most remaining A misses are targets no channel returns or targets lost
at selection, not lexical order.

## 23. P13.5 delivery record (2026-10-27)

Retrieval behaviour is **unchanged**. Full record: evaluation-plan §19.

| Item | Result |
| ---- | ------ |
| Variants (evaluation tunables, off by default) | `rrf_weight_exact` / `_lexical` / `_semantic` (each channel's RRF contribution × its weight); the existing `max_chunks_per_document`, `fused_pool`, `rrf_k` and `--no-rerank` |
| Evaluation | `lattice eval answer --set NAME=VALUE`; answer reports record `retrieval_config_version`, which `eval compare` treats as a declared configuration |
| Dev choice (C) | lexical ×1.25: R@10 0.739 → 0.756, MRR 0.641 → 0.651, no category down |
| Held-out (C) | R@10 0.649 → 0.626 (one lookup rank 10 → 11), MRR and ≥3-target flat → **not adopted** |
| Selection | One or two chunks per document first raises document recall and lowers chunk recall (targets are often several chunks of one document): rejected. Pool 80: no effect. rerank_v1: kept (buys MRR) |
| Prompt budget | 12 items unchanged; ~7 points of C recall sit at ranks 13–20 (evaluation-plan §19) |
| Not run | C′ (the crowd-out mechanism did not survive C); context recall (nothing to adopt) |
| Security | Every metric 0 in every run |

The finding for the owner: on this golden set, ranking changes move 1–2 points on dev and
do not hold on held-out. What remains is evidence no channel returns (P13.6, D-40) and the
size of the held-out split (44 items with targets).

## 24. P13.6 second-hop design (reviewed under D-40; built and measured in §28)

D-40 approved one bounded, deterministic second pass *in principle* and required its
design to be reviewed before any code. This is that design; the owner approved it on
2026-10-27 and it was built and measured (§28).

### 24.1 What the misses say (C snapshot, all 124 items with targets)

Of the 107 targets C misses in the top 10, **33 are other chunks of a record already in
the top 10** (A: 41 of 117): HAL-4821 comments 3, 5, 6 and 7 when the ticket's opening is
found; release-notes sections; refund-policy-v2 §3; HAL-4877 comments 3 and 5; the
scim-ga-decision paragraph after the one found. 73 targets are never in the top 20; the
rest are cross-record hops ("What commitments have we made to Acme?" → the SCIM decision
record and HAL-4821 comment 3, neither of which mentions Acme or a commitment) and
vocabulary gaps ("Directory Sync" ↔ "SCIM").

### 24.2 Proposed design: `expansion_v1`

One extra statement per request, after selection and before hydration, inside the same
snapshot. Inputs are the **top `k_seed = 5` selected candidates** -- chunks the reader may
read (they passed the SQL predicate and E4). Three sources, at most `k_expand = 10`
candidates in all:

| Source | What it adds | From |
| ------ | ------------ | ---- |
| (a) Record neighbours | the chunks of the seed documents adjacent to a seed chunk (ordinal ± 1), and chunks of a seed document that contain a question content term | seed document IDs (no text) |
| (b) References | records whose display reference is an identifier (`HAL-1234`, `INC-2026-031`, ...) written in a seed chunk's text and not in the question; at most 5 identifiers | permitted evidence text, parsed by the existing identifier grammar |
| (c) Readable links | the readable supersession neighbours of seed documents (already computed by hydration, D-14) | metadata |

Pseudo-relevance terms (salient words of the seeds as a new lexical query) are **left
out** of v1: they need text from evidence to become a query, they are the injection path
S9 worries about most, and the misses above are mostly (a). They can be a v2 after v1 is
measured.

The statement is one `UNION ALL` of the three, each branch with the tenant, lifecycle and
`readable_documents_predicate` conditions in its own WHERE (S1), ranked (a) by seed rank
then ordinal distance, (b) by identifier order, (c) by seed rank. Expansion candidates
join fusion as a fourth list with RRF weight 0.5 (they never outrank what the question
found on its own), then rerank and selection as today. E4 runs over them with every
other candidate (S2).

### 24.3 Security

| Risk | Control | Test |
| ---- | ------- | ---- |
| S1 retrieve-then-filter | every branch carries the predicate | SQL-structure suite gains the expansion statement |
| S4 an unreadable document changes the outcome | seeds are permitted rows only; the statement selects permitted rows only | same result with and without a hidden document that names a seed's identifier |
| S6 egress of evidence-derived text | nothing is embedded or sent anywhere: (a)–(c) are SQL over IDs and identifiers | `expansion_egress_violations` = 0 by construction; test asserts no provider call |
| S9 injected identifiers steer retrieval | an identifier planted in a readable chunk can only reach a record the reader may read (the predicate), at most 5, at weight 0.5; claims still need valid citations | planted-identifier fixture: a readable ticket names a confidential record and a non-existent one; neither appears for a reader without access; the answer validator rejects claims citing nothing |
| S5 hidden accounts | no account input; account metadata stays the visible join | existing golden `hidden_accounts` |
| S10 traces | trace holds counts and chunk IDs per source, never identifiers or text | trace test |

### 24.4 Cost and acceptance

+1 statement (budget §13: ≤ +2), index-served (document ID, `display_ref`, ordinal); no
model call. Acceptance stays as the plan states (§11 P13.6): multi-hop targets found @10
for ≥ 30% of them on held-out, no category down more than 2 points, expansion security
metrics 0. Given P13.4–P13.5, the owner should expect the held-out effect to be judged on
44 items; (a) is the part with the clearest mechanism.

### 24.5 Decisions for the owner

1. Approve `expansion_v1` as above, or narrow it (for example (a) only).
2. Confirm pseudo-relevance terms stay out of Phase 13.
3. Confirm the fourth-list weight 0.5 is a starting value to be chosen on dev only.

## 25. P13.7 delivery record (2026-10-27)

Answering only. Full record: evaluation-plan §20.

| Item | Result |
| ---- | ------ |
| Validator adversarial suite | 65 authored claims: 31/31 invalid rejected with the expected code, 0/30 valid rejected, 4 "beyond the contract" cases documented as passing (entailment is the judge's, D-44) |
| Validator fixes (within the documented rules) | word forms compared after one common suffix ("moved" shares "moves"); curly outer quotes stripped like straight ones. Before: 3/30 valid rejected |
| `answer-prompt-2` | a record's chunks adjacent; supersession between records in the prompt named by handle; one instruction line. Version in every trace |
| Citation completeness | not enforced: as a status rule it mislabelled 8/119 expected-answered fake runs and its real effect needs D-43; the trace records `decisive_in_prompt` / `decisive_cited` |
| Prompt budget | unchanged (12 items, 24,000 characters); the item limit binds in 116/129 prompts, characters never pass 25%: raising items is the owner's decision (§13) |
| Answer A | identical to `answer-A.json`; every security metric 0 |
| Not measured | real answer quality (D-43) |

## 26. P13.8 delivery record (2026-10-27)

No behaviour change. Full record: evaluation-plan §21.

| Item | Result |
| ---- | ------ |
| Supersession | Already deterministic from explicit links: found 13/14 (the miss has neither side retrieved), 0 unfounded in 171 runs -- the target is met |
| Conflict coverage | dev 12/21, held-out 8/15 (target ≥ 0.8 on held-out: **not met**). Of 16 short runs: 8 side records never retrieved, 6 another chunk of the record retrieved (P13.6 §24 (a)), 2 gate refusals |
| Candidate pre-pass | built and measured on dev: finds 6/12 labelled pairs, proposes a pair on 57/66 prompted runs without a labelled conflict → rejected, code removed |
| In-text dates as scope | not built: dates in questions are often content ("October or December", "after January 2027", "this year" for a change since 2025); narrowing would remove what the question asks about |
| Staleness | 5/8; the misses come from the fake draft's citations, not thresholds; unchanged |
| Security | every metric 0 |

## 27. Phase 13 closure report (P13.9, 2026-10-27)

**Verdict: engineering is complete as far as it can go without the owner; the phase does
not meet its quality criterion (§16.3).** The one behaviour change that survived held-out
evaluation is P13.3's relevance gate, accepted with its trade-off. P13.4 (lexical), P13.5
(fusion and selection) and P13.8 (conflict candidates) each produced dev gains or
signals that did not hold on held-out or did not discriminate, and were not adopted.
P13.6 was approved under D-40, built and measured: no variant improved dev (§28). P13.7 delivered the validator suite,
two in-rule validator fixes and `answer-prompt-2`. Nothing is pushed.

### Final measurements (HEAD, isolated databases, M1)

| Check | Result |
| ----- | ------ |
| Retrieval A vs `retrieval-A.json` | identical (0 item-level findings) |
| Retrieval B (aarch64) vs `retrieval-B-aarch64.json` | identical |
| Retrieval C vs `retrieval-C-aarch64.json` | identical (P13.4 run; retrieval code unchanged in effect since: RRF weights default 1.0) |
| Answer A vs `answer-A.json` | identical (171 runs) |
| Security metrics | 0 in every run of every configuration and split, every checkpoint |
| Latency (A, local DB) | retrieval p50 19 ms / p95 32 ms (budget 150 ms); whole Ask with the fake model p50 37 ms / p95 54 ms |
| SQL statements per Ask | unchanged since P13.3 (+1 over P13.0: the gate's statistics); the P13.4 statistics statement runs only for evaluation variants |
| Prompt | ≤ 12 items, ≤ 6,022 evidence characters (budget 24,000) |
| Mutation checks (not committed) | predicate removed from `question_statistics` → 3 tests fail; `unsupported_number` removed from the validator → 7 suite cases fail |
| Full backend suite | 3,459 passed (17 min, isolated test database) |
| Web | untouched (no API or OpenAPI change) |

### Completion criteria (§16)

| # | Criterion | Status |
| - | --------- | ------ |
| 1 | Security metrics 0 everywhere; new statements covered by SQL-structure and property tests | **Met** (new statements: SQL-structure suite and an unreadable-document statistics test; mutation-checked) |
| 2 | Held-out results for A and C against the P13.0 baseline per item; no unexplained category drop > 2 points | **Met**: retrieval is identical to the P13.0-era baselines; the answer changes are P13.3's, explained and accepted |
| 3 | G1 to D-39; G2–G5 improved on held-out; G6 conflict coverage ≥ 0.8; G7 staleness ≥ baseline | **Not met.** G1: dev yes, held-out no (owner-accepted, §21). G2–G5: no change survived held-out (§22–23). G6: held-out coverage 0.53, bound by retrieval (§26). G7: 5/8 at HEAD; P13.0's 6/8 was over a different set of runs (P13.2 grew the golden set), and P13.4–P13.8 changed no answer outcome |
| 4 | Validator adversarial suite at its acceptance level; claim-level fail-closed unchanged | **Met**: 31/31 invalid rejected, 0/30 valid rejected (§25) |
| 5 | Latency and prompt budgets (§13) | **Met** |
| 6 | Golden ≥ 150 with provenance and splits; owner review per D-41 | Set: met (159). **Owner review of the 56 held-out items pending**, so no held-out figure is binding |
| 7 | Real answer quality measured under D-43 or recorded as not measured | **Not measured** (D-43 not approved; reference hardware cannot host a local model) |
| 8 | Docs as built; full suite; web untouched or green; CI green once at closure | Docs and suite: this checkpoint. **CI and the push wait for the owner's approval** |

### What the phase learned

- With 80 dev and 44 held-out items, one item moving is 1.3 and 2.3 points. Every
  ranking change tried moved dev by 1–2 points and did not hold; the evaluation
  foundation (P13.1–P13.2) is what made that visible instead of shipping noise.
- The largest retrievable gap that does not depend on ordering is evidence no channel
  returns, a third of it other chunks of a record already found (P13.6 §24 (a)); it is
  also what bounds conflict coverage (§26).
- The validator's deterministic floor is now measured: it rejects every authored invalid
  claim and no authored valid one; entailment remains outside it by design.

### Decisions for the owner

1. ~~D-40 / P13.6~~ -- approved and built 2026-10-27; not adopted (§28).
2. **D-41:** review the 56 held-out items (docs/golden-review/ H1–H4); until then no
   held-out number is binding. A larger held-out split would make future ranking
   decisions measurable.
3. **Prompt item budget (§13):** the 12-item limit binds in 116/129 prompts while
   characters stay under 25%; raising it needs a real-model measurement (D-43).
4. **Closure:** accept Phase 13 with criterion 3 unmet (as recorded) and authorize the
   push, branch CI and merge.

## 28. P13.6 delivery record (2026-10-27)

D-40 approved by the owner 2026-10-27 ("build expansion_v1 as designed in §24, measure on
dev then held-out"). Full record: evaluation-plan §23.

| Item | Result |
| ---- | ------ |
| Built | `lattice.retrieval.expansion` (three branches, one statement, ≤ 10 candidates from 5 seeds, ≤ 5 referenced records); `expansion` channel in fusion; `RetrievalConfig.expansion` (off), `k_seed`, `k_expand`, `max_references`, `rrf_weight_expansion`, `expansion_new_only`; trace count `expansion` when enabled |
| Dev (A, C) | no variant better than the product; the written design (all candidates, weight 0.5) A R@10 0.722 → 0.538; new chunks only: no change up to weight 0.75, worse above; nudging found chunks worse at every weight |
| Why | the hop reaches 3/66 (A) and 2/61 (C) dev misses as new evidence; the same-record targets are already retrieved and lose on order |
| Held-out | not used (nothing to confirm) |
| Deviations tried | new chunks only; candidates ranked by their seed's rank. Neither helped |
| Security | SQL-structure per branch (chunk and seed predicates), planted identifier (S9), unreadable record changes nothing (S4), no provider (S6), trace IDs/counts (S10); mutation-checked |
| Cost when enabled | +1 statement, p50 9 ms / p95 19 ms (A) |
| Product | unchanged; baselines unchanged; full backend suite 3,465 passed |

With P13.6 measured, every retrieval checkpoint of Phase 13 has been tried; the evidence
says the remaining gap on this corpus is order among already-retrieved chunks of shared
topics, which neither weighting nor a second hop fixed. Owner decisions remaining: §27
items 2–4.
