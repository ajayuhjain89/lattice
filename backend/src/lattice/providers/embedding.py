"""The embedding provider boundary (docs/architecture.md §3, ADR-008).

``EmbeddingProvider`` is deliberately narrow: text in, vectors out. A provider knows
nothing about organizations, users, documents or authorization, so what leaves Lattice
is exactly the list of strings the caller passes -- and the caller (ingestion) decides
that list under the AI egress policy (``lattice.providers.egress``).

Implementations here:

* ``FakeEmbeddingProvider`` -- deterministic hashed bag-of-words vectors, for tests,
  CI and local development without a network. Treated as an *external* provider
  (``deployment = "external_api"``) so the egress policy is exercised exactly as in
  production. Refused when ``LATTICE_ENV=production``.
* ``RecordingEmbeddingProvider`` -- wraps another provider and keeps every payload, so
  security tests can inspect exactly what would have left Lattice.
* ``lattice.providers.openai_embedding.OpenAIEmbeddingProvider`` -- the real provider.
"""

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

Purpose = Literal["document", "query"]
Deployment = Literal["external_api", "private"]

_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class EmbeddingError(Exception):
    """The provider could not embed a batch. ``code`` is a fixed identifier; the
    message never contains the input or the provider's response body."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EmbeddingProvider(Protocol):
    @property
    def provider(self) -> str:
        """``openai``, ``fake``, ... -- the ``model_providers.provider`` value."""
        ...

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    @property
    def deployment(self) -> Deployment: ...

    @property
    def retention_mode(self) -> str: ...

    @property
    def training_opt_out(self) -> bool: ...

    @property
    def max_batch(self) -> int:
        """Texts per request."""
        ...

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        """One vector per text, in order. ``purpose`` lets providers that distinguish
        queries from documents (e.g. Voyage ``input_type``) do so; others ignore it."""
        ...


class FakeEmbeddingProvider:
    """Deterministic, network-free vectors: each lowercase word (and hyphenated
    identifier) is hashed to a signed position; the sum is L2-normalized. Texts
    sharing words are similar, so semantic tests behave roughly lexically."""

    provider = "fake"
    model = "fake-hashed-bow-v1"
    deployment: Deployment = "external_api"
    retention_mode = "none"
    training_opt_out = True
    max_batch = 64

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions

    def vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        values[0] = 1.0  # never the zero vector
        for word in _WORD.findall(text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            values[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in values))
        return [v / norm for v in values]

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        return [self.vector(t) for t in texts]


@dataclass
class EmbeddingCall:
    texts: tuple[str, ...]
    purpose: Purpose


@dataclass
class RecordingEmbeddingProvider:
    """Records every payload before delegating. ``fail`` makes every call raise, to
    test provider failures."""

    inner: EmbeddingProvider = field(default_factory=FakeEmbeddingProvider)
    fail: bool = False
    calls: list[EmbeddingCall] = field(default_factory=list)

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def dimensions(self) -> int:
        return self.inner.dimensions

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
    def max_batch(self) -> int:
        return self.inner.max_batch

    @property
    def texts(self) -> list[str]:
        return [t for call in self.calls for t in call.texts]

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        self.calls.append(EmbeddingCall(tuple(texts), purpose))
        if self.fail:
            raise EmbeddingError("embedding_failed:provider_error")
        return await self.inner.embed(texts, purpose)
