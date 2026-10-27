# Phase 13 follow-ups: roadmap to finish what Phase 13 left open

Status: **proposed 2026-10-28, not approved.** Phase 13 was accepted and merged
(26bbd08) with completion criterion 3 unmet ([phase-13-plan.md](phase-13-plan.md) §27).
This document lists everything Phase 13 left incomplete, why, and an ordered plan to
close it. Nothing here starts without the product owner's go-ahead, checkpoint by
checkpoint, as in every phase.

## 1. What is incomplete

| # | Item | Phase 13 state | Why it is open | Blocks |
| - | ---- | -------------- | -------------- | ------ |
| O1 | Owner review of the 56 held-out items (D-41) | Packet ready (docs/golden-review H1–H4) | Owner time | Every held-out figure is non-binding until done |
| O2 | Held-out split too small to decide anything | 44 retrieval items with targets, 61 answer runs | One item = 2.3 points of R@10; every P13.4–P13.6 dev gain (1–2 points) was inside that noise | Any future ranking or gate decision |
| O3 | Held-out already seen | Seen for P13.3 (once, disclosed), the P13.4 single changes and each dev choice | Inherent to checkpoint-by-checkpoint reporting | Testing the dev-only candidate (lexical ×1.5, RRF k 30 on C) honestly |
| O4 | G1 relevance gate misses D-39 on held-out | Dev: 11/17 caught, ≤ 5% refused. Held-out: 4/8 caught, 6/45 (A) and 3/45 (C) refused | Paraphrase without a shared specific word; account questions about large accounts | Criterion 3 (G1) |
| O5 | G2–G5 retrieval quality did not improve on held-out | Retrieval unchanged since P13.0; lexical, fusion, selection and second-hop variants kept as evaluation tunables, off | The remaining loss is *order* among already-retrieved chunks of shared topics; neither weighting nor a second hop fixed it | Criterion 3 (G2–G5); R@10 ≥ 0.90 product objective (D-42) |
| O6 | G6 conflict coverage 0.53 on held-out (target 0.8) | Supersession already exact; candidate pre-pass rejected (fired on 86% of unlabelled runs) | Missing sides are not retrieved (8/16) or another chunk of the record was (6/16) | Criterion 3 (G6) |
| O7 | Real answer quality never measured (D-43) | Fake extractive generator only | No approved key or quota; the M1 8 GB cannot host a local model | Prompt budget (O8), citation completeness (O9), conflict hints, answer-prompt-2's effect, judge calibration (D-44) |
| O8 | 12-item prompt budget binds in 116/129 prompts | Characters never above 25% of 24,000 | §13 forbids raising it without a measured gain; ~7 points of C's recall sit at ranks 13–20 | Context recall |
| O9 | Citation completeness is measured, not enforced | Trace counts `decisive_in_prompt` / `decisive_cited` | As a rule it mislabelled 8/119 fake runs; real effect unknown | Answer status honesty |
| O10 | Term statistics scan the reader's whole corpus | +1 statement per Ask, a few ms at 1,300 chunks | Linear in corpus size | Phase 14 scale |
| O11 | Local Node 23.7 cannot run the web unit tests after undici 8.12 | CI (Node 22) is fine; local runs used Node 24.13 | undici needs zlib zstd (Node ≥ 22.19 / ≥ 23.8) | Developer setup only |

## 2. Principles carried over

- Tune on **dev** only; test a dev choice on held-out **once**; a variant found after a
  held-out look waits for a fresh held-out.
- No change ships without a held-out improvement that is larger than the split's noise.
- Security metrics 0 in every run; new statements carry the predicate in their own WHERE.
- ₹0 by default (zero paid infrastructure); any spend is the owner's explicit decision with
  a cost estimate (D-43).
- One heavy task at a time on the M1 8 GB; never a local LLM.

## 3. Roadmap

Six checkpoints, F1–F6. F1–F2 are prerequisites for every quality decision; F3 and F4 are
independent of each other; F5 needs F2 (and F4 for answer-side items); F6 closes.

```
F1 owner review (D-41) ──► F2 golden growth + sealed split ──► F5 retrieval and gate retry ──► F6 closure
                                                         └──► F4 real-model answer eval (D-43) ──┘
F3 hygiene (independent: term statistics bound, Node note, dev-only candidate parked)
```

### F1 — Owner review of the held-out labels (O1)

- **Owner:** review H1–H4 (56 items) in docs/golden-review; mark each item agreed or
  corrected.
- **Engineering:** apply corrections with `lattice dataset freeze-labels --reason` (never
  silently), mark reviewed items `owner_reviewed`, regenerate every baseline with
  `--replace --reason` (A, B on both architectures, C, answer A).
- **Then:** re-read the P13.3–P13.6 held-out results against the reviewed labels (report
  only; nothing is re-chosen).
- **Done when:** all 56 items reviewed; baselines regenerated; held-out figures binding.
- **Cost:** owner time (~4 batches); ₹0 compute.

### F2 — Golden growth and a sealed split (O2, O3)

- **Goal:** a held-out split where one item is ≤ 1 point, and a split nobody has looked at.
- **Scope:** author ~120 new items (label_source `authored_f2`), group-aware splits so the
  new held-out has **≥ 100 items with targets**; a new **sealed** split (~40 items with
  targets) evaluated only at a checkpoint's final decision, never during tuning; exhaustive
  labels where cheap; owner review in batches (D-41 procedure).
- **Emphasis:** the categories Phase 13 could not move: ≥ 3-target synthesis,
  account-centred questions about large accounts, paraphrase without shared words, conflict
  pairs with both sides labelled, abstention probes.
- **Done when:** held-out ≥ 100 with targets, sealed split frozen, owner review complete,
  baselines regenerated.
- **Cost:** engineering authoring + owner review; ₹0 compute. Corpus unchanged (no
  re-embed) unless new items need new documents -- then one C re-embed (~6 min, alone).

### F3 — Hygiene (O10, O11, the parked candidate)

- Bound `term_statistics` / `question_statistics` for large corpora: an `EXPLAIN`-backed
  plan (per-lexeme counts from the GIN index, or a cap with a documented fallback) with the
  S4 equivalence test kept. Measured on a synthetic 10× corpus.
- Document the Node requirement (≥ 22.19 with zlib zstd; 23.0–23.7 do not have it) in the
  README setup and `engines`.
- Park the dev-only candidate (C: lexical ×1.5, RRF k 30) for F5's sealed test.
- **Cost:** ₹0.

### F4 — Real-model answer evaluation (O7, O8, O9) — needs D-43

- **Decision first:** which model and who pays. Options, cheapest first:
  1. Gemini free tier, synthetic organizations only (`--allow-external`, data_use unpaid):
     ₹0 but quota-bound (the earlier key's quota was exhausted; a new key or a later day);
  2. an owner-supplied paid key: estimate ~200k input / 50k output tokens per full answer
     run, ×2 with the judge, ×(number of variants);
  3. a local model on hardware other than the M1 8 GB.
- **Scope once approved:** D configuration run on dev; judge calibration on a hand-labelled
  subset (D-44); then, each measured on dev and confirmed once on held-out:
  answer-prompt-2 vs -1; prompt item budget 12 vs 16 vs 20 (O8); citation completeness as a
  status rule (O9); the conflict candidate hint with a precision floor.
- **Done when:** real answer quality reported with denominators; each of O8/O9 decided on
  evidence.
- **Cost:** per the chosen option; estimate approved before any call.

### F5 — Retrieval and gate retry on the larger split (O4, O5, O6) — after F2

The Phase 13 evidence says the loss is ordering among same-topic chunks, not reach. F5
tries the approaches Phase 13 did not, each needing its own decision where noted:

| Step | Approach | Decision needed |
| ---- | -------- | --------------- |
| F5.1 | Re-test the strongest Phase 13 dev candidates (lexical ×1.25–1.5, RRF k 30; gate variants (b)/(c) of §21) on the new dev, then the sealed split once | none |
| F5.2 | Record-aware ranking: score documents first (best chunk + coverage of the question across its chunks), then order chunks within the top documents | none (deterministic; replaces a ranking stage, so A/B/C baselines change) |
| F5.3 | Embedding input with title and heading context for every chunk | **D-45** (re-embed: ~6 min local per corpus version; external providers re-embed at their cost) |
| F5.4 | Another local embedding model already cached (Snowflake arctic, Alibaba gte) as C2, compared with mxbai on dev | D-38 amendment (a second reference model) |
| F5.5 | Gate: recalibrate on the new dev for the two refusal patterns (paraphrase, large accounts); confirm on sealed | none (bumps `GateConfig.version`) |
| F5.6 | Conflict coverage: re-measure after F5.2–F5.3 (it is bound by retrieval) | none |

- **Acceptance (unchanged from §16.3):** held-out (≥ 100 items) R@10 and context recall up
  beyond one-item noise with no category down > 2 points; G1 to D-39 on held-out; G6 ≥ 0.8;
  sealed split confirms; security 0.
- **Not in scope:** a model reranker (Phase 13 non-goal; the cross-encoder was rejected in
  Phase 5) unless the owner reopens it.

### F6 — Closure

- Final A/B/C and answer runs on dev, held-out and sealed; per-item comparison with the
  Phase 13 baselines; latency and prompt budgets; full backend suite; web untouched or
  green; criteria 3 and 7 of phase-13-plan §16 re-assessed; one closure push, branch CI,
  merge on the owner's approval.

## 4. Decisions for the owner

| Decision | When | Options |
| -------- | ---- | ------- |
| Start F1 (your review) | now | review H1–H4 / delegate a subset |
| F2 size and the sealed split | after F1 | ~120 items as proposed / smaller |
| D-43: real-model evaluation and budget | before F4 | Gemini free tier (synthetic only) / paid key with estimate / other hardware / not now |
| D-45: embedding input change | before F5.3 | approve re-embed / not |
| D-38 amendment: second local model | before F5.4 | approve / not |
| Where this track lives | now | a follow-up track alongside Phase 14 / folded into Phase 14 / before Phase 14 |

## 5. Suggested order and effort

1. F1 (owner) and F3 (engineering) in parallel — days, ₹0.
2. F2 — the largest engineering item (authoring ~120 items + review batches).
3. F4 if D-43 is approved — can run while F2's review batches are pending.
4. F5 after F2, one step at a time, each stopping for approval.
5. F6.

Without F1 and F2, no later checkpoint can show a real improvement: they are the
critical path.
