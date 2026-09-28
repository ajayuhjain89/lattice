# ADR-007: Single-turn Ask; no conversational memory in the first release

Status: Accepted; implemented in Phase 6 (`POST /v1/ask` holds no conversation state)

## Context
Multi-turn chat creates paths for authorization carry-over: evidence retrieved in an
earlier turn might be reused after a revocation, or leak between users through shared
state.

## Decision
Each Ask is independent: fresh authorization, fresh retrieval, no server-side
conversation state. The UI supports refinement by editing the question and adjusting
scope, not by chat history.

## Alternatives considered
Multi-turn with per-turn re-authorization and history containing only question text
(no evidence). Viable later; not needed for the core workflow.

## Consequences
Follow-ups like "what about Kestrel?" must be phrased fully (UI keeps the previous
question editable). No conversation persistence to secure or retain.

## Reconsider when
Pilot users repeatedly need follow-ups; then add multi-turn where history carries only
prior questions/answers text, every turn re-authorizes and re-retrieves, and prior
evidence is never reused without re-verification.
