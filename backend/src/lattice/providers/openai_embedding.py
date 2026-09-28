"""OpenAI embeddings over plain HTTP (ADR-008): ``text-embedding-3-small`` at 1024
dimensions by default.

No vendor SDK: one POST to ``/v1/embeddings`` with ``model``, ``input`` (a batch of
strings), ``dimensions`` and ``encoding_format="float"``. Documented limits (checked
2026-09-27): at most 2,048 inputs and 300,000 tokens per request, 8,192 tokens per
input. Lattice's chunks are ≤ ~600 tokens, so batches of 128 stay far below both.

* The API key is read by this class only, from ``LATTICE_OPENAI_API_KEY``, held as a
  ``SecretStr`` and sent only in the ``Authorization`` header. It is never logged.
* Errors carry a fixed code, never the response body (which could echo input text).
* Rate limits (429) and server errors (5xx, timeouts) are retried with exponential
  backoff; other errors are not retried.
* API inputs are not used for training by default; abuse-monitoring retention is up to
  30 days unless Zero Data Retention is approved for the project (recorded in the
  ``model_providers`` row: ``retention_mode``, ``training_opt_out``).
"""

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import SecretStr

from lattice.providers.embedding import Deployment, EmbeddingError, Purpose

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
_RETRYABLE = frozenset({408, 409, 429, 500, 502, 503, 504})


class OpenAIEmbeddingProvider:
    provider = "openai"
    deployment: Deployment = "external_api"
    retention_mode = "abuse_monitoring_30d"
    training_opt_out = True  # API data is not used for training by default
    max_batch = 128

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str = DEFAULT_MODEL,
        dimensions: int = 1024,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        max_attempts: int = 4,
        backoff_seconds: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.get_secret_value():
            raise ValueError("an OpenAI API key is required")
        self.model = model
        self.dimensions = dimensions
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._transport = transport

    def __repr__(self) -> str:  # never the key
        return f"OpenAIEmbeddingProvider(model={self.model!r}, dimensions={self.dimensions})"

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        """``purpose`` is ignored: OpenAI's embedding models have no query/document
        input types."""
        if not texts:
            return []
        if len(texts) > self.max_batch:
            raise ValueError(f"at most {self.max_batch} texts per call")
        payload = {
            "model": self.model,
            "input": list(texts),
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        ) as client:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await client.post("/embeddings", json=payload, headers=headers)
                except httpx.TimeoutException:
                    code = "embedding_failed:timeout"
                except httpx.HTTPError:
                    code = "embedding_failed:network"
                else:
                    if response.status_code == 200:
                        return self._vectors(response, len(texts))
                    if response.status_code in (401, 403):
                        raise EmbeddingError("embedding_failed:unauthorized")
                    if response.status_code not in _RETRYABLE:
                        raise EmbeddingError("embedding_failed:rejected")
                    code = (
                        "embedding_failed:rate_limited"
                        if response.status_code == 429
                        else "embedding_failed:provider_error"
                    )
                if attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff * 2 ** (attempt - 1))
            raise EmbeddingError(code)
        raise AssertionError("unreachable")  # pragma: no cover

    def _vectors(self, response: httpx.Response, expected: int) -> list[list[float]]:
        try:
            body: Any = response.json()
            data = sorted(body["data"], key=lambda item: item["index"])
            vectors = [[float(v) for v in item["embedding"]] for item in data]
        except (ValueError, KeyError, TypeError):
            raise EmbeddingError("embedding_failed:malformed_response") from None
        if len(vectors) != expected or any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingError("embedding_failed:malformed_response")
        return vectors
