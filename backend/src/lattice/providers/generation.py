"""The generation provider boundary (docs/architecture.md §3, retrieval-design §8).

``GenerationProvider`` is as narrow as ``EmbeddingProvider``: fixed instructions, one
prompt and a JSON schema in; one JSON document (as text) out. An adapter knows nothing
about organizations, users, documents or authorization. Whether it may be called at all
is decided outside it -- by ``lattice.providers.egress`` over the organization's
recorded configuration -- and exactly what it receives is decided by
``lattice.answering`` (authorized evidence only, filtered by the egress level). An
adapter has no tools, no retrieval and no network access other than its own endpoint.

Implementations:

* ``FakeGenerationProvider`` -- deterministic, network-free: it quotes the first
  sentence of the best-ranked evidence block(s) of the prompt. A test double for CI and
  development; it says nothing about answer quality. Declared ``external_api`` (like
  the fake embedding provider) so every egress test exercises the external rule.
  Refused in production.
* ``ScriptedGenerationProvider`` -- returns scripted outputs or raises scripted
  errors, to test invalid handles, fabricated quotes, malformed JSON, timeouts.
* ``RecordingGenerationProvider`` -- keeps every request before delegating, so security
  tests and the evaluation can inspect exactly what would have left Lattice.
* ``lattice.providers.ollama_generation.OllamaGenerationProvider`` -- a local
  open-weight model served by Ollama (ADR-012).
* ``lattice.providers.gemini_generation.GeminiGenerationProvider`` -- Gemini API, BYOK.

Errors are ``GenerationError`` with a fixed code, never provider text.
"""

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from lattice.providers.egress import Deployment


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    system: str  # fixed, versioned instructions (trusted)
    prompt: str  # the delimited question and evidence (untrusted data inside)
    schema: dict[str, Any]  # the JSON schema the output must follow
    max_output_tokens: int = 2048


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class GenerationError(Exception):
    """The provider produced no usable output. ``code`` is one of
    ``generation_failed:{timeout, network, unauthorized, rate_limited,
    quota_exhausted, not_found, provider_error, refused, truncated,
    malformed_response}``; the message is the code, never a response body."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GenerationProvider(Protocol):
    @property
    def provider(self) -> str:
        """``model_providers.provider`` (``fake``, ``ollama``, ``gemini``, ...)."""
        ...

    @property
    def model(self) -> str: ...

    @property
    def deployment(self) -> Deployment:
        """The trust class this implementation actually runs as (D-15)."""
        ...

    @property
    def retention_mode(self) -> str: ...

    @property
    def training_opt_out(self) -> bool: ...

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


# --- fake -------------------------------------------------------------------------------

# The prompt contract of ``lattice.answering.prompt`` (tested there): per-request
# nonce-suffixed delimiters, one question block, one block per evidence item.
_QUESTION = re.compile(r"<question-(?P<n>[0-9a-f]{12})>\n(?P<q>.*?)\n</question-(?P=n)>", re.S)
_EVIDENCE = re.compile(
    r'<evidence-(?P<n>[0-9a-f]{12}) id="(?P<h>E\d+)" rank="(?P<r>\d+)"[^>\n]*>\n'
    r"(?P<t>.*?)\n</evidence-(?P=n)>",
    re.S,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
# The fake's stand-in for the model's D-11 rubric: requests to act, not to know.
_ACTION = re.compile(
    r"^\s*(please\s+)?(draft|write|send|email|reply|update|change|delete|remove|create|"
    r"schedule|assign|close|reopen|grant|give\s+me\s+access|share|post|book|order|pay|"
    r"approve|show\s+me\s+the\s+questions)\b",
    re.I,
)


def _first_sentence(text: str) -> str | None:
    for part in _SENTENCE_END.split(text):
        sentence = part.strip()
        if len(sentence.split()) >= 3 and len(sentence) <= 300:
            return sentence
    return None


@dataclass
class FakeGenerationProvider:
    """Deterministic drafts from the prompt itself: the first sentence of the best-ranked
    evidence is quoted verbatim as the direct answer, the next block's as support.
    ``delay_seconds`` makes each call wait first, to exercise long Asks through the
    ingress (the reference stack's verification profile; never in production)."""

    provider: str = "fake"
    model: str = "fake-extractive-v1"
    deployment: Deployment = "external_api"
    retention_mode: str = "none"
    training_opt_out: bool = True
    delay_seconds: float = 0.0

    def draft(self, prompt: str) -> dict[str, Any]:
        question = _QUESTION.search(prompt)
        if question is not None and _ACTION.search(question["q"]):
            return {
                "status": "unsupported_request",
                "claims": [],
                "conflicts": [],
                "unknowns": [],
                "clarifying_question": None,
            }
        blocks = sorted(
            ((int(m["r"]), m["h"], m["t"]) for m in _EVIDENCE.finditer(prompt)),
            key=lambda b: b[0],
        )
        claims: list[dict[str, Any]] = []
        for _, handle, text in blocks:
            sentence = _first_sentence(text)
            if sentence is None:
                continue
            role = "direct_answer" if not claims else "supporting"
            claims.append(
                {
                    "text": sentence,
                    "role": role,
                    "citations": [{"handle": handle, "quote": sentence}],
                }
            )
            if len(claims) == 2:
                break
        return {
            "status": "answered" if claims else "insufficient_evidence",
            "claims": claims,
            "conflicts": [],
            "unknowns": [],
            "clarifying_question": None,
        }

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        text = json.dumps(self.draft(request.prompt))
        return GenerationResult(
            text=text, input_tokens=len(request.prompt) // 4, output_tokens=len(text) // 4
        )


@dataclass
class ScriptedGenerationProvider:
    """Returns ``outputs`` in order (a dict is serialized, a string returned as is, an
    exception raised); the last one repeats. For adversarial and failure tests."""

    outputs: Sequence[dict[str, Any] | str | BaseException]
    provider: str = "fake"
    model: str = "fake-extractive-v1"
    deployment: Deployment = "external_api"
    retention_mode: str = "none"
    training_opt_out: bool = True
    calls: int = 0

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if isinstance(output, BaseException):
            raise output
        return GenerationResult(output if isinstance(output, str) else json.dumps(output))


@dataclass
class RecordingGenerationProvider:
    """Records every request (system, prompt, schema) before delegating."""

    inner: GenerationProvider = field(default_factory=FakeGenerationProvider)
    requests: list[GenerationRequest] = field(default_factory=list)

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def deployment(self) -> Deployment:
        return self.inner.deployment

    @property
    def retention_mode(self) -> str:
        return self.inner.retention_mode

    @property
    def training_opt_out(self) -> bool:
        return self.inner.training_opt_out

    @property
    def payloads(self) -> list[str]:
        """Everything that left Lattice, as text."""
        return [f"{r.system}\n{r.prompt}\n{json.dumps(r.schema)}" for r in self.requests]

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return await self.inner.generate(request)
