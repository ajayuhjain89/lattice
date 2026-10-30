# Phase 13 follow-ups: roadmap to finish what Phase 13 left open

Status: **proposed 2026-10-28; F1 (owner review) and F3 (hygiene) authorized the same
day; F3 delivered (§6). F2 authorized 2026-10-29; the owner accepted the recommended answer
to every F2 question the same day (§7.7); F2a-F2c delivered (§8, §9): 146 items authored,
split and frozen; F2d (owner review of H5-H9 and S1-S4) is next.** Phase 13 was accepted and merged
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
| O10 | Term statistics scan the reader's whole corpus | +1 statement per Ask, a few ms at 1,300 chunks; measured at 10× and 50× in F3 (§6) | Linear in the reader's permitted documents: the authorization predicate itself | Phase 14 scale (design options in §6) |
| O11 | Local Node 23.7 cannot run the web unit tests after undici 8.12 | **Done in F3**: `engines` `^22.19.0 \|\| >=24`, README prerequisite | undici needs zlib zstd (Node ≥ 22.19 / ≥ 23.8) | — |
| O12 | The answer pipeline misses some action requests (D-11) | **Done (§20)**: the detector recognises D-11's record and calendar verbs (`request-kind-2`); both refusal items now get `unsupported_request`, and no other status changed | — | — |
| O13 | Configuration C's query embeddings depend on which questions share a batch | **Done in F5.0** (§16): the evaluation asks a local model one question per call, as the product does. Found at F2e (§13): the local model padded each batch of questions to its longest member | External providers keep their batches (per-request quotas); the fake model's vectors are per text anyway | — |

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
| F5.3 | Embedding input with title and heading context for every chunk (corrected in §14.1: `embed-input-1` already carries them) | **D-45** (re-embed: ~6 min local per corpus version; external providers re-embed at their cost) |
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

## 6. F3 delivery record (2026-10-28)

### Term statistics at scale (O10)

Measured on an isolated copy of the A evaluation database (port 5440, project
`lattice-f3scale`), multiplied with `backend/scripts/scale_corpus.sql` (new IDs per copy,
grants copied, supersession links dropped on copies), timed with
`backend/scripts/statistics_bench.py`: the gate's `term_statistics` for 8 Halden askers ×
4 questions, 3 timed repetitions each, M1, local Docker.

| Corpus | Chunks (Halden documents) | Permitted chunks per asker | `term_statistics` p50 / p95 |
| ------ | ------------------------: | -------------------------: | --------------------------: |
| reference (1×) | 1,325 (343) | ~200–1,060 | a few ms (P13.3) |
| 10× | 13,250 (3,430) | 2,000–10,620 | 15.9 / 21.4 ms |
| 50× | 66,250 (17,150) | 10,000–52,800 | 59.4 / 91.5 ms |

The cost is linear, and the plan shows where it goes: a bitmap scan of the organization's
documents with the authorization predicate evaluated on every one, hashed and joined to
the chunks. Two bounded forms were built and measured -- and **rejected**:

| Variant | 10× p50 / p95 | 50× p50 / p95 | Why it does not bound |
| ------- | ------------: | ------------: | --------------------- |
| at most 20,000 permitted chunks, the first in chunk-ID order (a uniform sample above the cap) | 24.6 / 32.5 | 86.1 / 154.0 | the planner sorts every permitted chunk before the limit (no index-ordered walk) |
| each count stops at a cap (20,000 chunks; 2,000 per lexeme, found through the GIN index) | — | 123.2 / 163.8 | every capped count evaluates the authorization predicate over all the reader's documents again |

The work that grows is the authorization predicate over the reader's documents, which
any count of *permitted* chunks needs (S4: an unreadable document must not move a weight).
The single pass in `main` already evaluates it once, so it stays unchanged. It is inside
the retrieval budget (150 ms p95, phase-13-plan §13) up to about **50× the reference
corpus per organization (~66k chunks, ~17k documents)**.

Options for Phase 14 (scale), both S4-safe, neither built:

1. **Statistics per access class.** Maintain, at ingestion, chunk and per-lexeme counts
   per access class of document (organization, sensitivity, `policy` mode, department
   set, account); a reader's statistics are the sum over the classes the reader may read,
   plus one count over the reader's `grants_only` documents (few). Exact; cost grows with
   the number of classes, not documents. Needs a migration and an ingestion change.
2. **Per-reader cache** keyed by membership, `authz_updated_at`, policy version and a
   corpus generation counter: one full pass per reader per change. Simple; invalidation
   on every document or grant change makes it weak for busy organizations.

### Node requirement (O11)

`apps/web/package.json` `engines` is now `^22.19.0 || >=24` and the README states the
requirement: undici 8.12 (via jsdom, unit tests only) uses the zlib zstd API that Node
23 gained in 23.8. CI and the production image use Node 22. Web unit tests: 278 passed.

### Parked candidate

The dev-only candidate from P13.5 (configuration C: `rrf_weight_lexical=1.5`, `rrf_k=30`,
dev R@10 0.760 / MRR 0.674) waits for F5.1, where it is tested on the new dev split and
the sealed split once (it must not be tested on the old held-out again).

## 7. F2 plan (proposed 2026-10-29)

The owner authorized F2 on 2026-10-29. This section is its plan. The owner accepted the
recommended answer to each question in §7.7 the same day.

### 7.1 Starting point (measured at 8db520d)

`lattice dataset splits`: 159 items, dev 103 / held-out 56, 89 split units. Held-out has
**44 items with targets** (12 have none), so one item is 2.3 points of R@10. 56 held-out
items are pending owner review (H1–H4, F1).

Not all of the corpus is targeted yet. These records are not a target of any golden item:

| Organization | Documents | Tickets | Chat records |
| ------------ | --------: | ------: | -----------: |
| Halden | 15 of 48 | 130 of 148 | 120 of 148 |
| Veridian | 2 of 7 | 29 of 37 | 10 of 13 |

The untargeted Halden documents include three Acme Corp account documents (QBR, Q3
success plan, FY27 renewal plan), the Acme Health and Orbital Freight briefs, the
handbook, the SSO and support-escalation runbooks, the discounting guidelines, the
incident-communication policy, the audit-log streaming PRD and the weekly support report.
One storyline (`acme-name-collision`) has no targeted record. That is enough material
for ~130 new information needs, so **the corpus does not need to change**. That keeps
the Phase 13 baselines comparable item by item and avoids a re-embed.

### 7.2 Size and allocation

| Split | New items | Of which with targets | Split after F2 | With targets after F2 |
| ----- | --------: | --------------------: | -------------: | --------------------: |
| dev | 22 | ~18 | 125 | ~98 |
| held-out | 62 | ≥ 58 | 118 | **≥ 102** (1 item ≈ 1.0 point) |
| sealed (new) | 46 | ≥ 40 | 46 | **≥ 40** |
| **Total** | **130** | | 289 | |

The roadmap's ~120 becomes 130 because F5.5 (gate recalibration) and F5.1 need new
*dev* items in the weak categories, not only held-out ones. The authoring margin also
lets the minimums survive group-aware assignment. With 40 items, the sealed split is a
confirmation (direction and no regression); the held-out split is the precise
measurement. Held-out is reported in two slices: the 56 already-seen items (Phase 13)
and the 62 fresh ones (F2).

### 7.3 What the new items cover

| Theme | Items | Why |
| ----- | ----: | --- |
| Synthesis needing ≥ 3 target records (cross_document, cross_source) | 28 | The ≥ 3-target recall (0.437 at P13.0) is the weakest stratum |
| Account-centred questions about large accounts (Acme Corp, Kestrel Bank) | 18 | Gate refusals on held-out (O4); same-account chunk ordering (O5) |
| Paraphrase without a shared specific word: 10 pairs, keyword wording + no-shared-word wording | 20 | Gate misses on paraphrase (O4). A pair is one split unit |
| Conflict and supersession with both sides labelled | 16 | Conflict coverage (O6) needs both sides as targets |
| Abstention probes: insufficient evidence, near-miss topic, refusal | 18 | G1 needs abstention items in every split |
| Single-record lookups over untargeted records | 18 | Fresh information needs; precision on short answers |
| Security probes: inaccessible strong match, egress, Veridian cross-tenant, injection | 12 | Security metrics must be 0 in every split, sealed included |

Cross-cutting rules:
- ≥ 35% of new items carry security traps.
- Every Halden persona gets ≥ 8 new items. Arjun, Lena and Sofia (≤ 3 held-out items
  each today) get ≥ 10 each. Veridian gets ≥ 12.
- Every evaluation class keeps ≥ `CLASS_MINIMUM` items per split, sealed included.
- Every category has ≥ 2 items in sealed.

### 7.4 Steps

**F2a — tooling** (engineering; stop for review after it):
1. `Split` gains `sealed` and `LabelSource` gains `authored_f2`.
2. Sealed items live in **`evals/golden-sealed/`**, outside the default golden directory.
   Every default eval run, baseline, `compare` and CI golden suite therefore never loads
   them. `lattice dataset lint|splits|freeze-labels|review-packet` load both directories,
   so the straddle and near-duplicate checks cover all three splits.
3. `lattice eval retrieval|answer --sealed --reason "..."` evaluates sealed items only.
   It refuses without a reason, writes to `evals/reports/sealed/` (never a baseline), and
   appends date, commit, configuration versions, reason and item count to
   `evals/golden/provenance/sealed-access.json`. Each sealed look is on record.
4. `lattice dataset splits --propose --shares dev=…,held_out=…,sealed=…` fills items
   without a split. It uses the same stratified, group-aware, deterministic objective,
   now over three splits, and still never moves a committed split. A new item that joins
   an existing unit inherits that unit's split, so a paraphrase of a dev item goes to dev.
5. `label-freeze.json` gains a `sealed` section with the held-out rules: a frozen item
   cannot move out of its split, and a label change needs `--reason`.
6. The splits report gains decisive-record overlap: held-out and sealed target records
   that are also dev targets. Eval split tables gain the held-out seen and fresh slices.
7. Review packets: H5… for new held-out items, S1… for sealed items. A packet shows
   labels, never results.
8. Tests for each of the above, the runbook row "Grow or change the golden set" updated,
   and one full backend suite, alone, in the background.

**F2b — authoring** (engineering; 5 batches of ~26 items by theme, each committed locally
after `lattice dataset lint` passes):
- Items are authored with no `split`. The proposer assigns splits afterwards, so the
  author does not choose where an item lands.
- Labels come from the corpus and `ground-truth.yaml` only. **No retrieval or answer run
  touches a new item before its split is assigned and frozen.** After the freeze, only
  dev items are run during F2.
- A new item's decisive facts must not be an existing dev item's decisive facts, unless
  the new item declares that item's `paraphrase_group` (and then inherits dev).
- Labels are exhaustive where that is cheap (narrow term sets): ≥ 20 new exhaustive
  items, mostly in held-out and sealed.
- Scope-all personas write "Acme Corp", not a bare "Acme", unless the ambiguity is the
  point (P13.2 pitfall).

**F2c — splits and freeze** (stop for the owner after it):
1. Run `splits --propose --write` once and check the minimums in §7.2–7.3. If anything
   falls short, author top-up items. Nothing is reshuffled.
2. `freeze-labels --reason` for every new held-out and sealed item.
3. Build the review packets and commit.

**F2d — owner review** (D-41): about 9 batches, H5–H9 (~62 items) and S1–S4 (~46). The
owner marks each item approved or corrected. Engineering applies corrections with
`freeze-labels --reason` and sets `owner_reviewed` on approved items.

**F2e — baselines** (engineering; stop after it):
1. On the isolated A/B database, `compare --allow-golden-change` must show 0 item-level
   findings for existing items.
2. Regenerate with `--replace --reason`, one heavy run at a time: retrieval A, B aarch64,
   B x86_64 (emulated database, `lattice-p13fx86`), C (local mxbai under the watchdog)
   and answer A. These cover dev and held-out only.
3. Report the new dev and held-out baseline figures, with the seen and fresh slices.
   They are F5's reference.
4. **There is no sealed run in F2.** The first sealed run is F5's final decision, with
   the baseline and the candidate in the same run.

### 7.5 Interaction with F1

If the F1 corrections arrive during F2, engineering applies them when they arrive
(`freeze-labels --reason`, `owner_reviewed`) and re-reports P13.3–P13.6 on the 56 items
by ID. The owner then chooses (Q6) between one baseline regeneration at F2e covering both
checkpoints, or the separate F1 regeneration that F1 defines.

### 7.6 Limitation (to be recorded in evaluation-plan)

Engineering authors every new item, sealed ones included, so the author knows the sealed
questions. Mitigations:
- The proposer assigns splits after authoring.
- After the freeze, engineering never opens `evals/golden-sealed/` except through the
  lint, freeze and review tools, until F5's final run.
- Every sealed evaluation is logged.

### 7.7 Questions for the owner

| # | Question | Options (recommendation first) |
| - | -------- | ------------------------------ |
| Q1 | Size and allocation | 130 new: dev 22 / held-out 62 / sealed 46 · 120 (dev 12) · smaller (held-out stays below 100 with targets) |
| Q2 | The 56 existing held-out items | stay in held-out, reported as a "seen" slice · demote to dev (bigger dev, but a fresh held-out of ~62 is below 100) |
| Q3 | Sealed mechanics | separate directory + `--sealed --reason` + access log · same files with `split: sealed` and a default filter |
| Q4 | Corpus | unchanged · a small extension for fresh sealed material (one C re-embed; every baseline shifts) |
| Q5 | Owner review scope | all new held-out and sealed items, ~9 batches · sealed in full plus a held-out sample |
| Q6 | Baseline regeneration with F1 | once at F2e if F1 corrections arrive first · twice, as F1 defines |

**Owner answers (2026-10-29): the first option for every question.** Q1: 130 new items
(dev 22, held-out 62, sealed 46). Q2: the 56 held-out items stay in held-out, reported as
the seen slice. Q3: a separate sealed directory, `--sealed --reason` and an access log.
Q4: the corpus is unchanged. Q5: the owner reviews every new held-out and sealed item.
Q6: if the F1 corrections arrive first, one baseline regeneration at F2e covers F1 and F2.

**Cost:** ₹0. With the corpus unchanged there is no re-embed. Heavy runs happen only in
F2a (one backend suite) and F2e (five baseline runs, one at a time). **Effort:** F2a about
one session; F2b about five; F2c half a session; F2d is owner time (9 batches); F2e about
one session.

## 8. F2a delivery record (2026-10-29)

Tooling for the sealed split and the follow-up authoring flow (§7.4 F2a), as planned;
no golden item, label, corpus or retrieval behaviour changed. Reference: evaluation-plan
§3.2; runbook "Grow the golden set with a sealed split (F2)".

| Piece | Where |
| ----- | ----- |
| `split: sealed`, `label_source: authored_f2`, `cohort: f2` (metadata: no digest change; S review batches for sealed items) | `lattice.dataset.manifests` |
| `evals/golden-sealed/` and `evals/golden-staging/`, loaded only by the dataset tools (`load_all`); the lint keeps every item in the directory of its split | `lattice.dataset.lint` |
| `splits --propose --shares … --write`: shares over the new items, stratified and group-aware; committed units keep their split; H/S batches numbered after the existing ones; items moved to their split's directory | `lattice.dataset.splits`, `lattice.cli_dataset` |
| Sealed freeze (`label-freeze.json` schema 2; schema 1 still read) | `lattice.dataset.provenance` |
| S batches in the review packet (H1–H4 unchanged byte for byte) | `lattice.dataset.review` |
| `eval retrieval|answer --sealed --reason`, access log before the run, reports under `evals/reports/sealed/`; runners refuse items without a split; a sealed report is never a baseline | `lattice.evals.sealed`, `lattice.evals.baseline` |
| Held-out results per cohort (`by_held_out_cohort`: `phase13` seen, `f2` fresh) | `lattice.evals.retrieval`, `lattice.evals.answer` |
| Tests (13) | `backend/tests/dataset/test_golden_sealed.py` |

**The golden fingerprint did not move.** It hashes every field of every item, so a new
field would have changed it for all 159 items and made every baseline compare (CI
included) refuse. Fields added after the first baselines are therefore hashed only once
set (`_LATE_FIELDS`). The fingerprint stays `d5369dd9…`, as in the committed baselines,
and a test pins it.

Verification: `lattice dataset lint` OK (159 items, dev 103 / held-out 56);
`review-packet --check` OK; ruff and mypy (396 files) clean; full backend suite 3,478 passed (3,465 + 13 new; 19 min, isolated database on port 5440, project `lattice-p13fix`).

Next (F2b, needs the owner's go-ahead): author the 130 items in `evals/golden-staging/`
in five themed batches, each linted and committed locally, with no evaluation run.

## 9. F2b and F2c delivery record (2026-10-29)

**F2b, authoring.** 146 items (`authored_f2`, `cohort: f2`) were written in
`evals/golden-staging/` in five themed batches plus a top-up batch, and each was linted and
committed locally: 0c9792e, 353cc7e, fdb0622, 591a9be, 607971c and 2dd138b. Labels come from the corpus and `ground-truth.yaml` only. **No retrieval or
answer run touched any of them** before the split was assigned and the labels frozen. A
scratch helper derived each item's classes from what its labels exercise, and listed every
exhaustive candidate for review. Engineering read all 20 candidate lists; no verdict was
changed.

| Batch | Items | Content |
| ----- | ----: | ------- |
| 1 | 31 | 13 lookups over records no item targeted before; 5 abstention probes; 6 action requests; 6 ambiguous "Acme" questions and one scope contrast |
| 2 | 25 | Synthesis needing three or more records (account histories, people across accounts, roadmap against delivery, incident policy against the record) |
| 3 | 25 | 18 Acme Corp and Kestrel Bank questions; 7 abstention probes whose answer sits only in unreadable records |
| 4 | 26 | 10 paraphrase pairs (keyword wording, and wording sharing no specific word with the evidence); 6 conflict or supersession items |
| 5 | 23 | 10 conflict or supersession items; 1 stale item; 12 security probes (8 in Veridian, against Halden records with the same IDs or names) |
| Top-up | 16 | 4 stale, 4 egress pairs, 3 identifier lookups, a scoped and a multi-account item, 3 chat syntheses |

The conflict items include two inconsistencies found in the corpus while authoring:
- **Orbital discount approver:** the Orbital brief routes a new-business discount to the
  renewal approver.
- **The October date:** Support's August "expected in October" came from a Slack thread,
  which the commitments policy forbids.

**F2c, splits and freeze.**
- **Splits:** `splits --propose --shares dev=0.17,held_out=0.48,sealed=0.35 --write`
  assigned dev 25, held-out 70 and sealed 51, and moved the sealed items to
  `evals/golden-sealed/`.
- **Freeze:** `freeze-labels` froze the 70 held-out and 51 sealed labels before any run.
- **Review packets:** `review-packet` added H5–H9 and S1–S4 (12–15 items each). H1–H4
  are byte-for-byte unchanged.

| Measure | Minimum (§7.2–7.3) | After F2 |
| ------- | ------------------ | -------- |
| Held-out items with targets | ≥ 100 | **102** (of 126; 1 item ≈ 1.0 point) |
| Sealed items with targets | ≥ 40 | **42** (of 51) |
| Every category in sealed | ≥ 2 | met (14 categories) |
| Halden personas / Arjun, Lena, Sofia / Veridian | ≥ 8 / ≥ 10 / ≥ 12 | met (lowest Halden persona 12; Arjun 15, Lena 14, Sofia 14; Veridian 13) |
| New items with security traps | ≥ 35% | **34%** (50 of 146): not met; no top-up item carries one |
| Exhaustive new items | ≥ 20 | 20 (dev 6, held-out 7, sealed 7) |
| Evaluation classes per split, sealed included | ≥ 4 | **not met in sealed** for paraphrase 3, keyword_vs_natural 3, contradictory_evidence 3, egress_withheld 3, identifier_variant 2, ambiguous_account 2, multi_account_comparison 2, scoped_retrieval 2, injection_misleading 2, hidden_account_collision 1 |

**Deviations from §7, for the owner:**
1. **146 items, not 130.** The first 130 left held-out at 94 items with targets and sealed
   at 36, with no stale or AI-egress item in sealed. As §7.4 provides, 16 top-ups were
   authored; nothing was reshuffled. The shares are unchanged.
2. **Theme mix.** Six action requests and six ambiguous items were added, so that every
   category could reach sealed. Pure synthesis is 25 items rather than 28. Items needing
   three or more records total 40 across all batches.
3. **Class minimum in sealed (above) not met.** Meeting it would take roughly 25 more
   multi-class items. The lint does not require it, and the sealed split's role is
   confirmation (§7.2). Owner's call whether to top up further.
4. **Questions reworded before the freeze** to clear the lint's near-duplicate check:
   - The Veridian renewal question, before assignment.
   - The Veridian PTO question, after assignment and before the freeze. This was the only
     edit to a sealed file outside the dataset tools.
5. **Two planned items dropped** because they would have duplicated another new item's
   information need. Two pairs of new items were grouped so each pair stays in one split
   unit: the billing-migration status probes, and the Acme health question asked by Tom
   and by Grace.

**Tests.** Updated for the post-F2 state:
- `test_golden_provenance`: the P13.2 held-out share, new F2 minimums, and batches per
  review round.
- `test_dataset_lint`: two minimum mutations also neutralise the F2 files.
- `test_golden_sealed`: fixtures copy the sealed directory; the fingerprint pin applies to
  the pre-F2 items.
- `provenance.update` checks the sealed freeze only when sealed items were loaded, as the
  lint does.
- Dataset tests: 98 passed.

**Full backend suite (rerun 2026-10-29, isolated database on port 5440):** 3,592 passed
and 5 failed, in 22 minutes. An earlier run was cut short when Docker Desktop stopped at
44%. Every failure is accounted for:
- **Baseline compares (4).** The answer-A, retrieval-A and retrieval-B baseline
  reproductions and the compare-regression test report INCOMPARABLE, because the golden
  set changed from d5369dd975fb (159 items) to f6838d1b8a92 (254 items). This is expected
  until F2e regenerates the baselines.
- **D-11 (1, open item O12).** `test_every_golden_item_passes_the_security_gate`: two new
  action requests, for example "Book a call with Priscilla Vance for Monday.", return
  `evidence_only` instead of `unsupported_request`. Every security metric in that test was
  0. On 2026-10-29 the owner chose to record the gap rather than fix it now.
- **Withheld evidence.** The check that withheld evidence was a strong match passes with
  the new items included.

**Limitation (evaluation-plan §3.2).** Engineering wrote the sealed questions. From this
commit on, engineering opens `evals/golden-sealed/` and `docs/golden-review/S*.md` only
through the dataset tools, until F5's final decision.

**Next (F2d, owner):** review H5–H9 (70 held-out) and S1–S4 (51 sealed), as well as F1's
H1–H4. Decisions are recorded with `record-review` (§10). After that comes F2e:
baselines, and once only, if the F1 corrections arrive first (Q6).

## 10. Recording the owner's review (2026-10-29)

No dataset command recorded an approval, and applying sealed decisions by hand would have
meant editing sealed files outside the dataset tools. The owner authorized a tool for it
on 2026-10-29: `lattice dataset record-review FILE [--phase3-correction] [--dry-run]`
(`lattice.dataset.decisions`; evaluation-plan §3.1, §3.2).

**The decisions file.**

```yaml
reviewed_on: 2026-10-30
approve: [H5, H6]                  # batches approved in full, apart from the items below
items:
  <item id>:
    decision: correct              # approve | correct | corrections_requested
    note: what was wrong, and the record that shows it
    add: {expected.relevant: ["doc:x#2"]}       # also set: and remove:
```

- **Held-out decisions** (F1 and H5–H9) go in `docs/golden-review/decisions/`.
- **Sealed decisions** (S1–S4) that correct or query a label go in
  `evals/golden-sealed/decisions/`; the tool refuses them anywhere else. Engineering
  runs that file through the tool without opening it. Approving a sealed batch in full
  carries no labels, so `approve: [S1]` may sit in the held-out file.

**What it does.**
- An approval sets `owner_review` to `approved` with the date and `label_source` to
  `owner_reviewed`. The `cohort` stays, so the seen/fresh held-out slices survive review.
- A correction changes only the keys it names, in the files' own style, approves the
  item with the note in `owner_review.notes`, and records the new digest in the label
  freeze with one change record per item. For a sealed item that reason names the batch
  only, because the freeze lives outside the sealed directory. A Phase 3 item needs
  `--phase3-correction`, and its correction needs its own commit.
- `corrections_requested` records the note and changes no label.
- **Safety.** Nothing is written unless every decision applies and, on a temporary copy,
  every item reloads as planned and the whole lint passes. Rerunning the same file
  changes nothing. The output counts sealed items per batch and never names one or
  prints a label.

**Tests.** `tests/dataset/test_owner_review.py` (17), on copies of the golden directories:
- Batches and items are recorded, and only the review lines and the corrected keys change.
- A rerun is a no-op.
- Every kind of bad decision, and a correction the lint rejects, writes nothing.
- A Phase 3 correction needs the flag.
- Sealed corrections are refused outside the sealed decisions directory and are never
  printed.

All 115 dataset tests pass, and `ruff` and `mypy` are clean.

**Known effect of approvals, for when they land.** An approved item leaves the
`authored_phase3` and `authored_phase13` label sources. Some code keys on those values:
- the golden retrieval test's Phase 3 set and its `by_label_source` counts;
- `test_phase3_labels_are_frozen_as_they_were`;
- the packet's Phase 3 and Phase 13 columns;
- the reports' `by_label_source` breakdown.

The commit that records the first approvals updates the tests to identify Phase 3 items
by the Phase 3 freeze instead. The 56 seen held-out items are identified by `cohort`
being unset, not by label source. Approvals also move the golden fingerprint, which F2e
expects.

## 11. Owner decisions (2026-10-29)

After F2c the owner accepted engineering's recommendation on four open points:

| Point | Decision |
| ----- | -------- |
| Sealed shortfalls (§9: security share 34%, ten classes below 4 in sealed) | **Accepted as disclosed; no top-up.** The sealed split confirms, it does not measure precisely (§7.2) |
| O12, the D-11 gap | **Parked; fixed as an F5 step**, shaped on dev and confirmed once on held-out. The failing items are F2 held-out or sealed items, so a fix made from them now would tune on them. The F2e baselines record the gap as current behaviour; `test_every_golden_item_passes_the_security_gate` fails until then |
| Where this track lives | **Alongside Phase 14.** After F2e, one PR merges `phase/13-followups` (F3, F2, the review records, the new baselines) into main, so Phase 14 starts from them. F4–F6 continue as a separate track, one approved step at a time. F5 steps merge one by one, because Phase 14 also touches retrieval (O10) |
| D-43, real-model answer evaluation | **Gemini free tier, synthetic organizations only**, subject to the estimate in §12, which must be approved before any call |

### 11.1 Owner decisions (2026-10-30)

The owner asked for every remaining step except F4 to go ahead, and chose:

| Point | Decision |
| ----- | -------- |
| F1/F2d review | **Stays the owner's (D-41).** Engineering pre-checks the held-out labels (H1–H9 only; S1–S4 stay unread) and flags suspect labels with the record that shows it. Approval is the owner's alone; no item is marked `owner_reviewed` by engineering |
| F2e timing | **Revises Q6: regenerate the baselines now, and once more after the review's corrections.** Both regenerations are recorded with reasons, and the compare check on pre-F2 items runs first |
| F5 pacing | **Stop after each step.** Each step is built and measured on dev, then reported; the held-out look and the next step wait for the owner. F5.3 and F5.4 still need D-45 and the D-38 amendment |

## 12. F4 estimate (proposed 2026-10-29; no call made)

**What is measured.** Real answer quality: status accuracy, citations, context recall,
abstention and conflict reporting. Generation is `gemini-3.5-flash-lite` (the adapter's
default, unpaid `data_use`), and retrieval is configuration A, which spends no embedding
quota and loads no local model. Results describe an unpaid external provider under the
egress policy. Synthetic data is acceptable for unpaid use (`data_use_acceptable`), but the
egress level still withholds what the policy withholds, so this is not a best-case product
figure.

**Sizes.** These are measured on the committed golden set and the latest answer report:
- **Runs.** A full pass is 136 dev and 135 held-out runs (items × egress configurations).
- **Model calls.** Phase 13 called the model on 78% of dev runs and 70% of held-out runs;
  the rest stop at a gate. That is about 106 calls per dev pass and 95 per held-out pass.
- **Prompt size, at today's 12-item budget.** Median 5,964 characters, p90 7,528, maximum
  8,913. That is about 1.8k input tokens per call by our own count; Gemini's tokenizer will
  differ.
- **Output.** Assumed at most about 500 tokens per call. The cap is 2,048.

**Run plan.** Dev decides; held-out is looked at once per choice.

| Stage | Runs | Model calls |
| ----- | ---- | ----------: |
| 0. Smoke test: 5 dev items (key, pacing, parsing) | — | 5 |
| 1. Reference on dev, then the same run again to measure run-to-run noise (temperature 0 is not guaranteed deterministic) | 2 dev | ~212 |
| 2. Variants on dev: answer-prompt-1 against -2; prompt budget 16 and 20 against 12 (O8); the conflict-candidate hint | 4 dev | ~424 |
| 2b. Citation completeness as a status rule (O9): derived from stage 1's traces | 0 (1 dev run if it cannot be derived) | 0 (≤ 106) |
| 3. Held-out once, after F1/F2d: reference and chosen candidate in one look | 2 held-out | ~190 |
| **Total** | | **~830** (≤ ~1,130 if O9 needs a run and the choices need separate held-out looks) |

**Tokens.** About 1.7M input and at most 0.4M output; the upper bound is about 2.4M input
and 0.6M output. Budget-20 prompts are the largest, at roughly 2.8k tokens each.

**Cost.** ₹0 on the free tier. The local machine carries one isolated database and HTTP
calls; no model runs locally.

**Time.** Google publishes no free-tier numbers. Its rate-limit page says to "view your
active rate limits in AI Studio". The limits apply per project, RPD resets at midnight
Pacific, and "actual capacity may vary". With the project's requests per day R and per
minute M:
- calendar days ≈ total calls / R;
- one dev pass ≈ 106 / M minutes.

As an illustration only (these are not Google's numbers), R = 1,000 gives about one day,
R = 250 about four days, and R = 100 about nine to twelve days. Phase 5's embedding key
hit a per-day free-tier quota of about 1,000 texts (evaluation-plan §10), so quotas are
real and project-specific.

**Engineering needed before the first call.** All of it is local and ₹0, and each item
stops for review:
- **E1, pacing.** `eval answer --rpm N` spaces the calls. A per-minute 429 waits and retries
  once, in evaluation only. Today a 429 fails the item, and a per-day quota stops the run.
- **E2, resume.** `--resume REPORT` runs only the items without a result and merges them
  into one report that records it spanned several days. A quota stop then loses nothing.
- **E3, evaluation-only switches.** The prompt version (1 or 2), the evidence budget (12,
  16 or 20) and the conflict hint (on or off). These are constants today
  (`PROMPT_VERSION`, `MAX_PROMPT_EVIDENCE`); the product defaults do not change.
- **E4, preflight.** A `models.get` check that spends no quota, as Phase 5 did.

**The judge (D-44) needs a decision.** `--judge` is refused for external providers by
design, because the authored facts may be above the external egress level. The options:
- **(a) No model judge in F4 (recommended).** The deterministic checks score every run.
  The owner hand-labels about 40 stage-1 dev answers for correctness, and that subset
  calibrates what the checks miss.
- **(b) Allow an external judge for synthetic organizations only.** This is a code change
  and a policy decision.

**Data and terms (Gemini API Additional Terms, unpaid services, updated 2026-04-28).**
- Prompts and responses may be used to improve Google's products and train its models, and
  human reviewers may read them.
- Do not submit sensitive, confidential or personal information. Only the synthetic
  organizations are sent; the CLI refuses anything else.
- Users must be 18 or older, in an available region. The owner confirms in AI Studio that
  the region is available.
- The key is created by the owner in AI Studio and set as `LATTICE_GEMINI_API_KEY` in the
  local environment file. It is never committed or printed.

**Order.**
1. E1–E4.
2. Stages 0–2 on dev, which can run while the review is pending: dev labels are not under
   review.
3. Stage 3 after F1/F2d.
4. Report with denominators, deciding O8 and O9 on that evidence.

**Approvals needed:**
1. This estimate.
2. E1–E4.
3. Judge option (a) or (b).
4. The key and the project's RPM/RPD for `gemini-3.5-flash-lite`, read in AI Studio.

### 12.1 E1–E4 delivery (2026-10-30)

The owner approved the estimate, judge option (a) and E1–E4 on 2026-10-29. No Gemini
call has been made, the preflight included.

- **E1, pacing** (`lattice.evals.answer.Pacer`).
  - `--rpm N` spaces calls. A per-minute 429 waits 60 s and is retried once.
  - A per-day quota latches for the whole run, and the run stops after that item. The
    adapter's own latch lives on one provider instance, and the evaluation builds one per
    Ask, so without this every remaining item would still have sent one request.
  - A Gemini run is always paced; other providers are paced only with `--rpm`.
- **E2, resume** (`--resume REPORT`).
  - The configuration must match: corpus, golden items, generation, gate, retrieval and
    prompt configuration.
  - Kept: every run with a status that is not `failed`/`provider_unavailable`.
  - Asked again: errors, provider failures and runs never reached.
  - Results come out in run order. The report records `resumed_from` and `pacing`
    (retries, seconds waited, `stopped`, `not_run`). A stopped run exits 3 and prints the
    resume command.
- **E3, prompt switches** (`PromptConfig`).
  - `--prompt-version answer-prompt-1|2` and `--prompt-evidence N` (1–40).
  - `answer-prompt-1` is rebuilt from P13.6. Its instructions match the P13.6 text byte for
    byte; checked against the file at `2ec9324^` when it was built.
  - The product's path passes no configuration and is unchanged.
  - The report records `prompt_config`. `eval compare` declares it only when it is not
    the product's, so default runs still compare with baselines written before the field.
- **E4, preflight.**
  - The Phase 12 `ProviderTester.gemini` (`GET /models/{model}`, public targets only)
    runs before a Gemini evaluation sends anything; `--preflight-only` stops there.
  - Without a key it returns `credential_missing` and sends nothing.

**Change to the estimate.** The conflict-candidate hint (stage 2's fourth variant) has no
code. It was built in P13.7, measured as firing on 86% of dev prompts without a labelled
conflict, and removed (evaluation-plan §20). A hint "with a precision floor" needs a new
design and a decision of its own. Without it, stage 2 is three dev runs (~318 calls), and
the plan is **~725 calls** (≤ ~1,020).

**Verification.**
- **New tests:**
  - `tests/evals/test_quota_bound.py`: pacing, the single retry, the per-day latch,
    pass-through of other failures, what a resume keeps and refuses, and the preflight
    (only a GET of the model; nothing without a key).
  - `tests/answering/golden/test_quota_bound_run.py`: over the real corpus, a run stopped
    by a per-day quota sends nothing more, and its resume ends with the uninterrupted
    run's outcomes.
  - Prompt-version and budget tests, and the compare-key test.
- **Results:**
  - Evaluation, answering, provider and CLI tests: 513 passed.
  - Golden answer package: 6 passed and 2 failed, both known before this change. One is
    O12. The other is the answer-A reproduction, INCOMPARABLE because of the golden-set
    change only.
- **Identity check.** A fake answer run (configuration A) compared with `answer-A.json`
  using `--allow-golden-change` showed:
  - the 171 pre-F2 runs unchanged, with **0 item-level findings**;
  - every security metric 0;
  - one declared change, `retrieval_config_version: None -> retrieval-6`. It predates
    this work: the baseline was written before P13.5 added that field.

  This check was verification only; no baseline was written, and F2e still needs its own
  go-ahead.
- `ruff` and `mypy` are clean.

**Next for F4, from the owner:**
1. Create a key in AI Studio and set `LATTICE_GEMINI_API_KEY` in the local environment
   file.
2. Read the project's RPM and RPD for `gemini-3.5-flash-lite` there.
3. Confirm the region is available.

Then, on the owner's go-ahead: `--preflight-only`, then stage 0 (5 dev items).

## 13. F2e delivery record (2026-10-30)

Run before the owner's review, per the revised Q6 (§11.1). There will be one more
regeneration after the review's corrections. One database at a time on port 5440:
`lattice-p13fix` (aarch64), `lattice-p13fx86` (amd64 under emulation) and
`lattice-p13fc` (C). The local model ran under a watchdog: the lowest free memory was
22%, and the largest swap growth in one run was 1.6 GB.

**Pre-F2 items unchanged.** Each configuration was compared with its P13 baseline using
`--allow-golden-change`:

| Configuration | Item-level findings on pre-F2 items | Security |
| ------------- | ----------------------------------: | -------: |
| Retrieval A | 0 | 0 |
| Retrieval B aarch64 | 0 | 0 |
| Retrieval B x86-64 | 0 | 0 |
| Answer A (171 pre-F2 runs of 271) | 0 | 0 |
| Retrieval C | 1: `cross-org-refund-window-felix`, ranking changed beyond the stored top 10; no target moved | 0 |

**The C finding, explained (open item O13).**
- **Deterministic.** Two full C runs are identical.
- **Not the code.** The P13.4, P13.9 and P13.6 code (git worktrees) gives the same result.
- **Not the database.** A fresh restore of the C snapshot gives the same result.
- **Not the model or the OS.** The model files verify, and the OS was last updated on
  30 September, before the baseline.
- **The cause is the evaluation's question batches.** Questions are embedded 32 at a time
  and padded to the longest in the batch. The 95 new items change which questions share a
  batch, which nudges the vector's numerics and reorders ties below rank 10.
- **Confirmed.** Running the 159 pre-F2 items alone, so the batches are the old ones,
  reproduces the old C baseline exactly (IDENTICAL, 0 findings).

**Baselines regenerated** (`--replace`, with reasons): `retrieval-A.json`,
`retrieval-B-aarch64.json`, `retrieval-B-x86_64.json`, `retrieval-C-aarch64.json` (254
items each) and `answer-A.json` (271 runs). Golden version `f6838d1b8a92`; corpus
`7f914a8ce283`, or `277fb9ff8982` for C's local vectors. No sealed item was run.

**New figures (R@10; items with targets in parentheses).** "Seen" is the 56 held-out items
from Phase 13 (44 with targets); "fresh" is the 70 F2 held-out items (58 with targets).

| Configuration | Dev (100) | Held-out (102) | Seen (44) | Fresh (58) | Overall MRR |
| ------------- | --------: | -------------: | --------: | ---------: | ----------: |
| A | 0.745 | 0.653 | 0.598 | 0.695 | 0.634 |
| B aarch64 | 0.574 | 0.576 | 0.477 | 0.652 | 0.502 |
| B x86-64 | 0.574 | 0.579 | 0.484 | 0.652 | 0.502 |
| C | 0.761 | 0.726 | 0.649 | 0.784 | 0.652 |

Answer A (fake generator: plumbing, not quality), 271 runs:

| Measure | Dev | Held-out |
| ------- | --: | -------: |
| Status accuracy | 0.853 | 0.733 |
| Context recall | 0.737 | 0.572 |

Held-out context recall by slice: fresh 0.595 (74 runs), seen 0.541 (61 runs). The gate
stopped 18 of 34 expected-abstain runs before the model and refused 26 of 207 answerable
runs.

**Reading.** The fresh held-out items score higher than the seen ones in every
configuration: R@10 is 0.784 against 0.649 on C. The seen slice is the hard Phase 13
residue that P13.3–P13.6 failed to move. The fresh slice includes the lookups that F2
added. Do not read the gap as improvement.

**Tests updated with the measured values.**
- The answer-A pins: 18 of 34 caught, 26 of 207 refused, context recall 0.6527.
- The B cross-architecture set: still the one Phase 13 outcome, plus three F2 rank
  changes within one k bucket. 64 items now rank differently.

Retrieval golden, answer golden, eval and dataset packages: 527 passed and 1 failed. The
failure is O12, as recorded.

**Full backend suite after F2e** (isolated database on port 5440, 2026-10-30): 3,634 passed
and 1 failed, in 19.6 minutes. The failure is O12
(`test_every_golden_item_passes_the_security_gate`). The four baseline-compare failures
recorded at F2c (§9) are gone. Docker stayed up throughout, with the lowest free memory
at 29%.

## 14. F5 plan (proposed 2026-10-30)

F5 starts from the F2e baselines (§13). Every step follows the same protocol, and stops
for the owner after each (§11.1):

**Protocol.**
1. **Dev decides.** 100 dev items have targets; one item is about 1 point of R@10. A change
   qualifies when:
   - dev R@10 (or the step's own metric) rises by at least 2 points, in both A and C where
     both apply;
   - no category falls by more than 2 points (or more than one item, in categories with
     fewer than 10 items);
   - every security metric is 0.
2. **Held-out once per choice.** A qualifying change is reported on held-out, which has
   102 items with targets, in the seen and fresh slices. It is adopted only if held-out
   moves in the same direction beyond one-item noise. The look is recorded whatever the
   result.
3. **Sealed once, at F5's final decision.** `--sealed --reason` runs the reference and the
   final candidate in the same run, and the run is logged. It confirms the direction and
   no regression; it chooses nothing.
4. **Adoption** bumps the configuration version and regenerates the affected baselines with
   reasons.

**Steps, in order:**

| Step | What | Decision needed | Notes |
| ---- | ---- | --------------- | ----- |
| F5.0 | **O13: embed evaluation questions one at a time** | none (evaluation only) | The product embeds one question per Ask, so batched pre-embedding in the evaluation measures something the product never does. One question per call removes the subset dependence. C's numbers move slightly once: a declared change, and `retrieval-C-aarch64.json` is regenerated. Cost: C runs take longer (254 model calls instead of 8) |
| F5.1 | **Parked candidate**: `rrf_weight_lexical=1.5`, `rrf_k=30` (`--set`) | none | Screened on the new dev in A and C; then one held-out look (its first on these items) |
| F5.2 | **Record-aware ranking**: score documents (best chunk plus coverage of the question across its chunks), then order the chunks within the top documents | none | A new ranking stage (`rerank_v2`), so A, B and C baselines change if adopted. Design note first, then build, then dev |
| O12 | **D-11 detector gap** | none | The verbs come from D-11's definition (act on a calendar, ticket or record: book, open, file, set up, add, invite, post, submit, …), not from the two failing items' wording. Unit tests in `tests/answering/test_request_kind.py` hold authored phrasings, and a no-false-positive check runs over every dev question. Then the golden security test confirms on all items. Answer baselines change for the refusal runs only |
| F5.5 | ~~**Gate recalibration**~~ **Dropped (D-46, §19).** for the two refusal patterns (paraphrase without a shared specific word; large-account questions) | none | Uses the F2 dev items in those themes; bumps `GateConfig.version`; answer A and C |
| F5.6 | **Conflict coverage** (folded into F6's final runs, §19), re-measured after F5.2 (it is bound by retrieval) | none | Measurement only, unless a step above changes it |
| F5.3 | ~~**Chunking or embedding-input change**~~ **Dropped (D-46, §19).**, chosen by a dev diagnostic first | **D-45**, only once the diagnostic names a change (brief below) | The diagnostic costs nothing; a change re-embeds the C corpus |
| F5.4 | ~~**A second local embedding model**~~ **Dropped (D-46, §19).** as C2 | **D-38 amendment** (brief below) | Compared with mxbai on dev |
| Final | Reference against the adopted candidate on held-out, then one sealed look | owner go-ahead | Then F6 |

### 14.1 Brief: D-45, a chunking or embedding-input change

- **A correction to F5.3.** F5.3 assumed chunks are embedded without their title and
  heading context. They are not: `embed-input-1` (`lattice.ingestion.embedding_input`)
  already embeds the kind label, the document title and the chunk's heading path in front
  of every chunk's text. Adding them again would change nothing.
- **What remains open under D-45:** what else a chunk's input could carry, or how chunks
  are cut. Candidates:
  - the document's opening lines as a short context line;
  - the record's date, for temporal questions;
  - smaller or overlapping chunks.
  Which one could help depends on why C's misses are missed.
- **Proposed first step (no decision needed, ₹0).** A diagnostic on the dev misses of C.
  For each missed target chunk, record whether its embedding input lacks something that
  the question's decisive terms need: the subject only named in another chunk, a date, or
  a split across chunks. It measures nothing new and changes nothing.
- **Then D-45, if the diagnostic names a change.** Approve one re-embed for evaluation,
  with the product default following only if it qualifies under §14's protocol.
  - Cost: one local re-embed of the 1,325 chunks under the watchdog (about 7 minutes and
    up to about 1.5 GB of swap growth at P13.2), and a new C snapshot.
  - The corpus fingerprint changes, so the C baseline and, through the fake vectors, both
    B baselines are regenerated. A is unaffected unless chunking changes.

### 14.2 Brief: D-38 amendment, a second local reference model

- **What.** One more cached model is evaluated as C2 beside mxbai: Snowflake arctic-embed
  or Alibaba gte, both already in `~/.cache/lattice/models`. Only one runs at a time.
- **Cost (₹0).**
  - One corpus embed per model, about 7 minutes under the watchdog, and a snapshot per
    model.
  - A C2 baseline, used as a reference and not gated in CI.
  - Licence and model-card checks before any adoption.
- **Risk.** Memory on the M1 8 GB: one model at a time, never alongside tests.
- **Choice:**
  - (a) approve C2 for evaluation, with mxbai staying the product default unless C2
    qualifies on dev and held-out;
  - (b) not now.

## 15. F5.1 delivery record (2026-10-30): the parked candidate does not qualify

The parked candidate was `--set rrf_weight_lexical=1.5 --set rrf_k=30` (P13.5, dev only).
It was screened on the **128 dev items alone** (100 with targets), reference and candidate
over the same selection. Both runs therefore embed the same question batches (O13).

| Configuration | R@10 reference → candidate | MRR reference → candidate | Categories down more than 2 points | Targets gained / lost at 10 |
| ------------- | -------------------------- | ------------------------- | ---------------------------------- | --------------------------- |
| A | 0.745 → 0.748 (+0.3) | 0.660 → 0.668 | cross_org −5.0, cross_document −3.0 | 3 / 2 |
| C | 0.761 → 0.778 (+1.7) | 0.652 → 0.679 | none (cross_document −1.5) | 7 / 4 |

Security was 0 in all four runs. The watchdog recorded the lowest free memory at 28% and
the largest swap growth at 1.4 GB.

**Verdict under the §14 protocol: does not qualify.**
- The gain is below 2 points in both configurations.
- A loses more than 2 points in two categories.
- The C gain (+1.7) is under two items' worth, and its movement is churn: 7 targets gained
  and 4 lost.

**No held-out look was made, and nothing changes:** `retrieval-6` stays, and the
tunables stay evaluation-only. The candidate's held-out set remains unseen, so it can
still be re-tested later.

**Next (needs the owner's go-ahead):** F5.0, per-question query embedding in evaluation
(O13). Then F5.2, record-aware ranking: a design note first.

## 16. F5.0 delivery record (2026-10-30): one question per call (O13)

**Change.** `PrefetchedQueries` (`lattice.evals.retrieval`) now asks a local model one
question per call, as the product does once per Ask. External providers keep their
batches, which their per-request quotas need. The fake model (B) is unaffected: its
vectors depend on the text alone. Retrieval reports record `query_embedding`
(`per_question` or `batched`). A baseline records it only when it is `per_question`, so
a batched run still compares with the baselines from before the field. Code only in
evaluation; the product is unchanged.

**Tests.**
- `test_baseline.py`: only `per_question` is a declared change.
- `test_golden_retrieval.py`:
  - only a local provider is asked one question per call;
  - with a stand-in model whose vectors depend on their batch, a 4-item run ranks
    exactly as a 12-item run when questions go one per call;
  - the batched control does not, which shows the test catches O13.

**Measurement on C (mxbai, the C snapshot, aarch64).**
- **All 254 items:** 226 calls, one per distinct question (8 before). `eval compare`
  against the F2e baseline reads CHANGED under the declared `query_embedding` change, with
  no regression. Every metric is identical (R@10 dev 0.761, held-out 0.726). Two rankings
  changed:
  - `f2-xorg-veridian-quarry-refund-ines`: two non-target chunks swap places at ranks 8
    and 9;
  - `para-sla-p2-sofia`: below the stored top 10.
- **The 159 pre-F2 items alone:** every ranking is identical to the full run. The subset
  dependence is gone.
- Security 0 in both runs. The watchdog stopped nothing; its memory figures were not kept.

**Baseline regenerated:** `retrieval-C-aarch64.json` (`--replace`, with the reason). A, both
B and answer A are unchanged.

**Checks.** Retrieval, eval and local-provider tests: 622 passed. mypy and ruff are clean.

**Consequence for F5.** C screens on a subset (dev only, `--item`) now rank each item as
the full run does, so a dev screen and a held-out look use the same vectors. F5.1's
verdict stands: its runs embedded the same batches on both sides, and its gain was below
the bar in A, which has no embeddings.

**Next (needs the owner's go-ahead):** F5.2, record-aware ranking, starting with a design
note (§17).

## 17. F5.2 design note (proposed 2026-10-30): record-aware ranking, `rerank_v2`

Status: **proposed; nothing built.** Building starts on the owner's go-ahead. No decision
record is needed: the change is deterministic, adds no model or provider, and is adopted
only under §14's protocol.

### 17.1 What the dev misses say

These figures come from the existing reports, on **dev items only**: configuration A from
F2e, and configuration C from F5.0. There are 100 dev items with targets, holding 212
targets. "Record" means a document, ticket or chat thread.

| Where a target missed at 10 is | A | C |
| ------------------------------ | -: | -: |
| Missed at 10, all causes | 76 | 70 |
| Its record is in the top 10 through another chunk (a sibling outranks it) | 21 | 20 |
| Its record is not in the top 10, but the target is in the ranked pool (ranks 11–40) | 37 | 35 |
| Its record is not in the top 10, and the target is not in the pool | 18 | 15 |

- **The middle row is F5.2's target.** About half the misses are already retrieved and
  ranked. They lose to chunks of other records, and nothing in the ranking asks whether a
  record as a whole answers the question.
- **The second row is a different problem:** ordering within one record. F5.2 addresses it
  only as a variant (17.3 c).
- **The last row is reach.** F5.2 cannot change it; that is F5.3 and F5.4 territory.
- **Targets spread over several records.** Of the 100 items, 46 have one target record, 25
  have two and 29 have three or more. Any record-first ordering must not crowd out a second
  or third record.
- **The top 10 already mixes records:** on average 6.8 distinct records, and at most 2.7
  chunks of the most frequent one.

### 17.2 What Phase 13 already ruled out (and why this differs)

- **≤ 1 chunk per document first** (P13.5, evaluation-plan §19). Document R@10 rose, but
  chunk R@10 fell 7 to 12 points, because targets are often several chunks of one record.
  F5.2 does not cap; it reorders.
- **Nudging a record's neighbours** (P13.6, §23). Even at weight 0.1 it displaced
  targets: a record's other chunks are, on average, less relevant than the chunk the
  question matched. That nudge followed adjacency and links. F5.2 lifts a record only for
  the *question's own terms* its pooled chunks cover together, which no single chunk does.
- **Lexical weighting** (P13.4, §18). It did not transfer from dev to held-out. F5.2
  leaves each channel's order unchanged and works after fusion.

### 17.3 Design

`rerank_v2` runs after `rerank_v1`. It works only on the fused pool of 40 candidates the
channels already returned, so it adds and removes nothing.

1. **Which question terms each pooled chunk covers.** One statement over the pool's chunk
   IDs returns, per chunk, the question's *content* lexemes that chunk contains. It reuses
   `content_terms_matched` (`lattice.retrieval.lexical`), the P13.3 definition: framing
   words and the names of the visible accounts named are excluded, because an account
   name appears in every chunk of that account. Chunks found only by semantic search get
   their lexemes too. The statement carries the shared tenant, lifecycle and authorization
   conditions, like every retrieval statement, even though the pool was already
   authorized.
2. **Record coverage.**
   - Let *Q* be the question's content lexemes and *best(d)* the highest `rerank_v1`
     score among record *d*'s pooled chunks.
   - Let *cov_best(d)* be the share of *Q* covered by that best chunk, and *cov(d)* the
     share covered by the union of *d*'s pooled chunks.
   - The record's **complementary coverage** is *gain(d) = cov(d) − cov_best(d)*: what
     the record's other chunks add to its best one. It is 0 for a record with one pooled
     chunk, and 0 for a question with fewer than two content lexemes.
3. **Variants**, each an evaluation tunable (`rerank_version=rerank_v2`,
   `record_mode=…`, `record_weight=…`):
   - **(a) Soft lift (recommended first).** Every chunk of *d* is multiplied by
     *(1 + w · gain(d))*, then the pool is re-sorted. The order within a record is kept,
     and other records' chunks interleave by score. The factor is bounded (*w* ≤ 0.5, at
     most 1.5 times), so it reorders like `rerank_v1` and never buries a strong single
     chunk.
   - **(b) Record first (the F5 plan's literal wording).** Records are ordered by
     *best(d) · (1 + w · gain(d))*, then their chunks are emitted round-robin: each
     record's best chunk, then each one's second, and so on. A strict grouping (all of
     record 1, then record 2) is not offered, because 54 of the 100 dev items need two or
     more records.
   - **(c) Within-record order.** Within a record, a chunk that adds a question lexeme its
     higher-ranked siblings lack moves ahead of one that adds none. This is aimed at the 20
     sibling-outranked misses. It combines with (a) or (b) and is screened separately.
4. **Selection** (`select_evidence`) is unchanged: deduplication by content hash, at most 3
   chunks per record first, the rest deferred, and the limit.

### 17.4 Security

- **Order only.** Like `rerank_v1`, it reorders candidates that were already authorized.
  Nothing is added to the pool, and E4 runs on the same set (S1, S2).
- **The new statement** reads only chunk IDs from the pool, repeats the predicate, and
  returns lexemes of the question, never chunk text. It joins the SQL-structure suite
  (predicate, tenant, lifecycle), with a mutation check: removing the predicate must fail
  the tests.
- **No hidden account.** The excluded account names are those of *visible* accounts only,
  as in P13.3 (D-8, D-10).
- **Trace:** per record, counts and factor values only: no lexemes and no text (S10).
- **Evaluation:** every security metric must be 0 in every run.

### 17.5 Cost

- One extra SQL statement per Ask, over at most 40 rows and at most 32 lexemes, as with
  the gate's statistics: a few milliseconds, measured at the build step. The product pays
  it only if F5.2 is adopted.
- No model calls and no re-embedding. Screening the variants on dev in A and C is a few
  minutes of local runs, one at a time, under the watchdog.

### 17.6 How it is decided (the §14 protocol)

1. **Build** behind the tunables, off by default. With the code present, A, B, C and
   answer A must still compare identical with their baselines.
2. **Dev screening**, A then C, on the 128 dev items with `--item`, reference and
   candidate on the same selection:
   - (a) with *w* ∈ {0.15, 0.3, 0.5};
   - (b) with the best *w* from (a);
   - (c) on top of the better of the two.

   The variant is chosen on dev only. It qualifies only with **at least +2 points of R@10
   in both A and C, no category down more than 2 points, and security 0.** Churn (targets
   gained and lost) is reported beside the net figure.
3. **One held-out look** for the qualifying choice only, reported in the seen and fresh
   slices, with context recall from answer A. It is adopted only if held-out moves the same
   way beyond one-item noise.
4. **Adoption** means `rerank_v2` and `retrieval-7` become the product default, and the A,
   B (both architectures), C and answer A baselines are regenerated with reasons. The
   retrieval design (§6) and the evaluation plan are updated.
5. **If nothing qualifies,** the code stays as an evaluation tunable, as P13.4 to P13.6
   did, and the result is recorded.

### 17.7 Honest expectation

- **The ceiling is the 35 to 37 targets in the pool whose record misses the top 10.**
  Moving one of them in pushes another chunk out, which may itself be a target, so the net
  gain is smaller.
- **Phase 13 fell short of the same +2-point bar** with ordering changes that each looked
  plausible on paper. Complementary coverage is a new signal, not a reweighting, but it
  may still be noise at 100 dev items. The likeliest place for it to help is cross_document
  and cross_source; the likeliest place for it to hurt is lookup, where one chunk answers
  the question.
- **Effect on the gate.** The relevance gate (P13.3) judges each returned item by its own
  signals, so a different top 12 can change its decisions. Answer A re-measures them at
  step 3.

## 18. F5.2 delivery record (2026-10-30): rerank_v2 built, does not qualify

**Built as §17 designed**, behind evaluation tunables, off:
- `lattice.retrieval.records`: the coverage statement, record gains, and the soft,
  interleave and within-record orders;
- `RetrievalConfig.record_mode`, `record_weight` (at most 0.5) and `record_within`.
  `--set` names `rerank_v2` when any of them is on;
- the trace gains `record_gain` per reranked chunk and a `records_lifted` count: numbers
  only.

**Dev screening (128 dev items), A then C:** 11 runs each, the reference included.
Details are in evaluation-plan §24.
- **Best in A:** soft, w 0.5: +1.4 points (3 targets gained, 1 lost), MRR −3.3.
- **Best in C:** within-record order (alone or with soft, w 0.15 or 0.3): +0.5 points
  (1 target gained).
- **Interleave:** −7.3 in A and −11.6 in C.
- Security was 0 in all 22 runs. The watchdog recorded the lowest free memory at 26% and
  the largest swap growth at 1.3 GB, on the full C run.

**Verdict: no variant qualifies.** No variant gains +2 points in both A and C, and the
gains in A cost MRR. **No held-out look was made**, so the held-out items stay unseen for
this change. `retrieval-6` and `rerank_v1` stay the product's.

**Why the §17.1 ceiling was not reached.** 35 C targets sit in the pool while their record
misses the top 10, but complementary coverage reaches about one record per question. The
question's content lexemes are few, and a record's best chunk usually holds them. The
evidence that would lift those targets is not lexical coverage. The misses are ordering
among chunks that share the topic, as Phase 13 found, and no deterministic signal tried
so far separates them.

**Checks.**
- New tests: `test_record_rank.py` (7 tests), the coverage statement in
  `test_sql_structure.py`, `test_sql_capture.py` (6 items) and a golden test over every
  item.
- Retrieval, eval and answer-golden packages: 622 passed and 1 failed. The failure is O12,
  as recorded.
- A, B and answer A compare identical with the code present. C over all 254 items
  compares IDENTICAL.
- mypy and ruff are clean.

**Next (needs the owner's go-ahead):** O12, the D-11 detector gap, shaped on dev (§14).
F5.5 (gate recalibration) and F5.6 (conflict re-measure) follow. F5.3 waits on its free
diagnostic, then D-45.

## 19. Re-scope (2026-10-30, D-46): a personal v1

**The owner's decision.** Lattice is a personal project, not built for enterprise use, and
there is no infrastructure budget. v1 is therefore a **personal release** verified on the
local production-shaped stack (implementation-plan D-46):
- **Phase 14 becomes the personal v1 release:** an honest README (what Lattice does, its
  measured quality, what was never validated), one-command local setup and a demo
  walkthrough.
- **Phase 15 is removed.** External staging validation (D-22) is no longer a release gate;
  it stays listed as never validated, and Lattice is never described as
  production-proven. Enterprise-scale hardening and executable organization purge
  (D-37A) are post-v1.

**What changes for this track (the lean path):**

| Item | Before | Now |
| ---- | ------ | --- |
| O12, the D-11 detector gap | F5 step | **Finish** (in progress) |
| F5.3 chunking or embedding input (D-45) | after a diagnostic | **Dropped.** No ranking change has cleared the bar in Phase 13 or F5 |
| F5.4 second local model (D-38 amendment) | needs approval | **Dropped** |
| F5.5 gate recalibration | F5 step | **Dropped.** The gate stays `gate_v1`, as accepted in P13.3 |
| F5.6 conflict coverage | measurement | **Folded into F6:** its final answer runs report conflict coverage |
| F4 real-model answers (Gemini free tier) | on hold for the owner's key | **Optional.** Only if the owner provides a key; the E1–E4 tooling stays ready |
| F1 / F2d label review (126 held-out, 51 sealed) | owner review of every batch | **The owner chooses one** (the last open item before F6): (a) a light pass over the batches, recorded with `record-review`; or (b) no owner review: the labels stay `authored_*`, and every held-out and sealed figure is reported as engineering-labelled, not owner-reviewed |
| F6 closure | after F5 | **Next after O12 and the review choice:** final A, B, C and answer-A runs on dev and held-out, one logged sealed look, the full backend suite, then one push and merge on the owner's approval |

Unchanged: tune on dev only, security 0 in every run, never push or merge without the
owner's go-ahead, and zero paid infrastructure.

## 20. O12 delivery record (2026-10-30): the D-11 detector gap closed

**Change** (`lattice.answering.request_kind`, version `request-kind-2`).
- **New verbs.** The detector now recognises imperatives that act on a calendar, ticket
  or record, taken from D-11's definition: book, open, file, raise, log, submit, post,
  add, set (up), arrange, mark, move, link, attach, archive, merge and issue. It also
  covers sharing with someone else; inviting, pinging or adding a person; and "remind
  me to …".
- **Guard against nouns.** Each verb counts only with an object an imperative takes: an
  article, a pronoun or a ticket key. "Open" counts only with one that creates something
  ("Open a ticket", never "Open the …"). This keeps "Open issues?", "Book of business?",
  "Mark Chen's renewal?", "Share the latest on Acme" and "Remind me what …" answerable.
- **Declared version.** Answer reports and baselines record `request_kind_version`, so
  the change is a declared one for `eval compare`.

**How it was shaped (dev only).**
- `tests/answering/test_request_kind.py` holds 23 authored action phrasings and 23 authored
  noun-first questions that must stay answerable.
- A new test classifies **every dev question** exactly as labelled. The dev split had one
  miss of this kind: "Open a Jira ticket …".
- The held-out item that found the gap was not used to write the patterns.

**Confirmation on all items: answer A, 271 runs.**
- `eval compare` reads CHANGED under the declared change, with no regression. Only two
  runs changed, both refusal items, now `unsupported_request` as expected:
  - `f2-refusal-open-jira-sofia` (dev): was answered by the model;
  - `f2-refusal-book-call-grace` (held-out): was `evidence_only`.

  This is the held-out item's one look.
- Status accuracy rose from 0.793 to 0.801. Context recall (0.6527) and false abstention
  (26 of 207) are unchanged, so no answerable question became a refusal. Security 0.
- `answer-A.json` is regenerated with the reason. The retrieval baselines are untouched,
  because the detector does not affect retrieval.
- `test_every_golden_item_passes_the_security_gate` now passes.

**Next:** the owner's choice on the label review (§19), then F6.

