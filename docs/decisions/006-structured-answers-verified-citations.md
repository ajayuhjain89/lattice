# ADR-006: Structured answers with quote-verified citations; no streaming of unvalidated text

Status: Proposed (Phase 0)

## Context
Citations are the trust model. A model can fabricate sources, cite the wrong evidence,
or be steered by injected text. The inspector must highlight the exact supporting
passage.

## Decision
The model returns a JSON-schema-constrained `AnswerDraft`: claims, each with citations
consisting of a request-local handle (E1…En) and a short verbatim quote. Code verifies
handles and locates each quote in its chunk (normalized match); verified offsets drive
highlighting. Validation is **claim-level and fail-closed**: if any citation of a
claim fails, the claim is rejected (one regeneration, then removed) — it is never
shown with the failed citation stripped, and no percentage threshold lets an
unverified claim remain visible. If no valid direct-answer claim remains, the answer
is withheld and the evidence list is shown. Model-proposed disagreements are shown as
*potential conflicts*; only deterministic supersession metadata can declare one
source replaced by another. Metadata shown (dates, titles, authority) comes from the
database. The UI streams progress stages and evidence, not answer
tokens.

## Alternatives considered
- **Free-text answer with `[n]` markers:** unverifiable support; no precise highlight.
- **Provider-native citation features:** good quality but provider-specific and not
  always combinable with structured output; conflicts with the provider-agnostic
  boundary (kept as an optional provider-level enhancement).
- **Token streaming for perceived speed:** would show text before validation;
  rejected for trust. Evidence-first progressive display covers perceived latency.

## Consequences
Answers appear all at once after validation (~4–6 s typical; up to two generation
calls when claims are rejected); prompts must teach concise quoting; evaluation
measures support separately (a quote can exist yet not support a claim).

## Reconsider when
A provider offers verifiable, portable citation structures with streaming, or users
show that the latency hurts adoption despite evidence-first display.
