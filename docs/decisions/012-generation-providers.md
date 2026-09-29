# ADR-012: Generation providers — local Ollama path, Gemini BYOK, fake for CI

Status: Accepted for Phase 6 (2026-09-29). Implemented and tested with mocks. The real
local-model evaluation was **not run** because of a hardware constraint on the reference
machine (below). D-4 (the default generation provider for a real deployment) remains
open.

## Context

Phase 6 needs a `GenerationProvider` behind the same invocation rule as embeddings
(ADR-011, D-15, D-16). Lattice must not require a paid API. The only external
credential available is an unpaid Gemini key whose quota is exhausted, and there is no
OpenAI or Anthropic key. Every CI and clean-clone run must work with no credential at
all.

## Decision

| Provider (`model_providers.provider`) | Trust class | Status | Use |
| --- | --- | --- | --- |
| `fake` (`FakeGenerationProvider`) | declared `external_api` (so every test exercises the external rule), no network | built | CI, tests, `lattice eval answer --generation fake`. Refused in production. Never an answer-quality figure |
| `ollama` (`OllamaGenerationProvider`), default model `qwen3.5:4b` | from the endpoint: loopback = `local`, anything else = `organization_controlled` (HTTPS only) | built, mock-tested | zero-cost / self-hosted generation |
| `gemini` (`GeminiGenerationProvider`), default model `gemini-3.5-flash-lite` | `external_api` | built, mock-tested | organization BYOK. An unpaid key is limited to synthetic organizations and refused in production (ADR-010 rules) |
| OpenAI, Anthropic, other endpoints | — | not built | the factory's `declare`/`build` pair is where they go (D-4) |

**Local runtime and model.** Ollama (MIT) serves the model over HTTP on the same host.
Its `format` parameter constrains decoding to the answer's JSON schema. That is the
property that makes a small model's structured output reliable. `qwen3.5:4b`
(Qwen3.5-4B, Apache 2.0, February 2026, 4.7 B parameters, Q4_K_M, about 3.4 GB) was
chosen once, without a model survey, for its license, its size and its schema-following.
Settings: `think: false` (the model thinks by default, and the answer contract has no
field for a hidden trace), `temperature: 0`, `num_ctx` 12,288, output capped at 2,048
tokens. Lattice never downloads Ollama or weights: the operator runs `ollama pull`.

**The trust class follows the endpoint, not the product name.** An adapter pointed at a
remote host declares `organization_controlled`. A record that says `local` for it then
sends nothing (`provider_deployment_mismatch`). A non-loopback endpoint must use HTTPS
and may not carry credentials, a query or a fragment.

**Gemini model.** `gemini-3.5-flash-lite` is the current stable, lowest-cost Gemini text
model on Google's model list (checked 2026-09-29; Gemini 2.x is being retired). Call:
`POST /v1beta/models/{model}:generateContent` with `systemInstruction`, one user turn,
`responseMimeType: application/json`, `responseJsonSchema`, `temperature: 0` and no
tools. The model is configuration (`model_providers.model`), never a constant in
answering. No retries on 429; a per-day quota stops the instance from sending anything
more.

**Structured output contract.** One hand-written JSON schema (`ANSWER_SCHEMA`) in the
subset every supported mode accepts: `type`, `properties`, `required`, `enum`, `items`,
`additionalProperties`. Length limits are enforced by validation, not by the schema.

## Real-model evaluation: NOT RUN — reference hardware constraint (M1 8 GB)

On 2026-09-29 a single smoke prompt (about 940 tokens) was sent to `qwen3.5:4b` on the
reference MacBook (M1, 8 GB). The runtime loaded 4.1 GB (23% CPU / 77% GPU) and processed
the prompt at about 10 tokens per second. The call hit the 300-second timeout before
producing an answer. The test suite and Docker PostgreSQL were running at the same
time, the machine reached about 14.5 GB of swap, and it stopped responding to input.
Ollama and the test run were stopped. The product owner then decided:

- the real local generation evaluation is **not run**, because of that hardware
  constraint;
- no smaller model is substituted merely to produce a number, and no local-model
  comparison is started;
- the adapter, the abstraction and the mock tests stay; the Ollama server stays stopped.

Consequence: no answer-quality figure exists for any real generation provider.
`lattice eval answer --generation fake` measures plumbing and security, not quality.
A synthetic-only Gemini evaluation (`--generation gemini --allow-external`) may be run
later if quota becomes available. It is not required for CI or for Phase 6 engineering
completion.

## Alternatives considered

- **llama.cpp in-process (`llama-cpp-python`)**: a native build and in-process memory in
  the API worker; Ollama keeps the model out of the Lattice process.
- **ONNX Runtime GenAI**: a narrower model catalogue and no schema-constrained decoding.
- **A catalogue benchmark of small models**: explicitly out of scope; the memory
  constraint above makes it moot on the reference machine.
- **Provider-native citation features**: rejected in ADR-006 (not portable).

## Reconsider when

A deployment has hardware with headroom for local inference, a customer asks for a
specific self-hosted endpoint (`organization_controlled`), or D-4 selects a default
external provider.
