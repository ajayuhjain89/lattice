"""Gemini API embeddings over plain HTTP (ADR-010): ``gemini-embedding-2`` at 1024
dimensions by default. Optional, external, organization BYOK.

One POST per batch to ``/v1beta/models/{model}:batchEmbedContents`` -- one
``EmbedContentRequest`` per text, so each text gets its own vector (several parts in
one request would be *aggregated* into one embedding). ``embedContentConfig`` carries
``outputDimensionality`` (and ``taskType`` for ``gemini-embedding-001``). Facts from the
official documentation, checked 2026-09-28:

* ``gemini-embedding-2`` (stable, April 2026): 8,192 input tokens; output dimensions
  128-3,072 (recommended 768 / 1,536 / 3,072; 1,024 is within the supported range);
  truncated dimensions are normalized by the service. Task semantics are **text
  prefixes**, ``task_type`` is not supported:
  query ``task: search result | query: {text}``, document
  ``title: {title} | text: {text}`` (``title: none`` without a title).
* ``gemini-embedding-001`` (legacy, text only): 2,048 input tokens; ``taskType``
  ``RETRIEVAL_QUERY`` / ``RETRIEVAL_DOCUMENT``; dimensions other than 3,072 are *not*
  normalized by the service. The two models' spaces are incompatible.
* The reference states no per-batch maximum; the service rejects more than 100
  requests per batch (observed and widely reported), so ``max_batch = 100``.

Lattice passes the document's embedding input (kind, title, heading path, text) as the
document text with ``title: none``: the provider cannot see document structure, and
the title is already inside the text. Every vector is L2-normalized here as well
(idempotent for gemini-embedding-2, required for 001).

* **Credential**: ``LATTICE_GEMINI_API_KEY`` or Google's ``GEMINI_API_KEY``, held as a
  ``SecretStr``, sent only in the ``x-goog-api-key`` header, never logged, never in an
  error, never in ``repr``. A credential is not an approval: the egress policy decides
  what may be sent (``lattice.providers.egress``).
* **Data use**: on the *unpaid* tier Google uses submitted content to improve its
  products and human reviewers may read it; on the paid tier it does not (Gemini API
  Additional Terms, 2026-04-28). Lattice cannot see which tier a key belongs to, so the
  operator declares it (``LATTICE_GEMINI_DATA_USE``, default ``unpaid``). Unpaid means
  ``training_opt_out = False``: the egress policy then sends content only from
  ``synthetic`` organizations, and the factory refuses it in production.
* **Retries**: 5xx and timeouts retried with exponential backoff; 429 (quota) retried
  at most ``max_rate_limit_retries`` times after the server's ``retryDelay`` (capped),
  then ``embedding_failed:rate_limited``. A 429 naming a *per-day* quota (observed on
  the unpaid tier: ``EmbedContentRequestsPerDayPerProjectPerModel-FreeTier``, 1,000
  per day, counted per embedded text) is not retried at all:
  ``embedding_failed:quota_exhausted``, and nothing more is sent by this process.
  After a rate-limit failure the provider refuses further
  calls without sending anything until the capped delay has passed (a circuit
  breaker): a sync's remaining batches fail fast as ``failed`` chunks, to be retried by
  the next sync, instead of hammering a limited quota. Other errors are not retried.
  Errors carry a fixed code, never the response body.
"""

import asyncio
import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx
from pydantic import SecretStr

from lattice.providers.egress import Deployment
from lattice.providers.embedding import EmbeddingError, Purpose

DEFAULT_MODEL: Final = "gemini-embedding-2"
DEFAULT_BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta"
MAX_BATCH: Final = 100
DataUse = Literal["unpaid", "paid"]
_RETRYABLE: Final = frozenset({500, 502, 503, 504})
_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")


@dataclass(frozen=True, slots=True)
class _Semantics:
    """How a model expresses query vs document retrieval."""

    query_prefix: str | None = None
    document_prefix: str | None = None
    query_task: str | None = None
    document_task: str | None = None


_MODELS: Final[dict[str, _Semantics]] = {
    "gemini-embedding-2": _Semantics(
        query_prefix="task: search result | query: ",
        document_prefix="title: none | text: ",
    ),
    "gemini-embedding-001": _Semantics(
        query_task="RETRIEVAL_QUERY", document_task="RETRIEVAL_DOCUMENT"
    ),
}
SUPPORTED_MODELS: Final = tuple(sorted(_MODELS))


class GeminiEmbeddingProvider:
    provider = "gemini"
    deployment: Deployment = "external_api"
    max_batch = MAX_BATCH

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str = DEFAULT_MODEL,
        dimensions: int = 1024,
        data_use: DataUse = "unpaid",
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        max_rate_limit_retries: int = 2,
        backoff_seconds: float = 2.0,
        max_retry_delay_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.get_secret_value():
            raise ValueError("a Gemini API key is required")
        if model not in _MODELS:
            raise ValueError(f"unsupported Gemini embedding model (supported: {SUPPORTED_MODELS})")
        if not 128 <= dimensions <= 3072:
            raise ValueError("Gemini output dimensionality must be within 128-3072")
        self.model = model
        self.dimensions = dimensions
        self.data_use = data_use
        # Unpaid tier: Google may use the content to improve its products.
        self.training_opt_out = data_use == "paid"
        self.retention_mode = (
            "abuse_monitoring_limited" if data_use == "paid" else "unpaid_product_improvement"
        )
        self._semantics = _MODELS[model]
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._max_rate_limit_retries = max_rate_limit_retries
        self._backoff = backoff_seconds
        self._max_retry_delay = max_retry_delay_seconds
        self._transport = transport
        # Operational counters (evaluation reports): requests sent, rate-limit answers.
        self.requests = 0
        self.rate_limited = 0
        self._blocked_until = 0.0  # monotonic; the circuit breaker after quota failures

    def __repr__(self) -> str:  # never the key
        return (
            f"GeminiEmbeddingProvider(model={self.model!r}, dimensions={self.dimensions}, "
            f"data_use={self.data_use!r})"
        )

    def _request(self, text: str, purpose: Purpose) -> dict[str, Any]:
        s = self._semantics
        prefix = s.query_prefix if purpose == "query" else s.document_prefix
        task = s.query_task if purpose == "query" else s.document_task
        config: dict[str, Any] = {"outputDimensionality": self.dimensions}
        if task is not None:
            config["taskType"] = task
        return {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": (prefix or "") + text}]},
            "embedContentConfig": config,
        }

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > self.max_batch:
            raise ValueError(f"at most {self.max_batch} texts per call")
        if time.monotonic() < self._blocked_until:
            code = "quota_exhausted" if math.isinf(self._blocked_until) else "rate_limited"
            raise EmbeddingError(f"embedding_failed:{code}")  # nothing sent
        payload = {"requests": [self._request(t, purpose) for t in texts]}
        headers = {"x-goog-api-key": self._api_key.get_secret_value()}
        url = f"/models/{self.model}:batchEmbedContents"
        attempt = 0
        rate_limited = 0
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        ) as client:
            while True:
                attempt += 1
                delay = self._backoff * 2 ** (attempt - 1)
                self.requests += 1
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except httpx.TimeoutException:
                    code = "embedding_failed:timeout"
                except httpx.HTTPError:
                    code = "embedding_failed:network"
                else:
                    status = response.status_code
                    if status == 200:
                        return self._vectors(response, len(texts))
                    if status in (401, 403):
                        raise EmbeddingError("embedding_failed:unauthorized")
                    if status == 429:
                        self.rate_limited += 1
                        rate_limited += 1
                        if self._daily_quota(response):
                            # A per-day quota: waiting minutes cannot help. Stop sending
                            # for the rest of this process.
                            self._blocked_until = math.inf
                            raise EmbeddingError("embedding_failed:quota_exhausted")
                        if rate_limited > self._max_rate_limit_retries:
                            self._blocked_until = time.monotonic() + self._max_retry_delay
                            raise EmbeddingError("embedding_failed:rate_limited")
                        attempt -= 1  # quota waits have their own budget
                        await asyncio.sleep(self._retry_delay(response))
                        continue
                    if status not in _RETRYABLE:
                        raise EmbeddingError("embedding_failed:rejected")
                    code = "embedding_failed:provider_error"
                if attempt >= self._max_attempts:
                    raise EmbeddingError(code)
                await asyncio.sleep(delay)

    @staticmethod
    def _daily_quota(response: httpx.Response) -> bool:
        """Does the 429 name a per-day quota (``QuotaFailure.violations[].quotaId``, e.g.
        ``EmbedContentRequestsPerDayPerProjectPerModel-FreeTier``)?"""
        try:
            details = response.json()["error"]["details"]
            return any(
                "PerDay" in str(v.get("quotaId", ""))
                for d in details
                for v in d.get("violations", [])
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return False

    def _retry_delay(self, response: httpx.Response) -> float:
        """The server's ``RetryInfo.retryDelay`` (e.g. ``"17s"``), else ``Retry-After``,
        else the backoff -- capped, so a long quota window fails instead of waiting."""
        seconds = self._backoff
        try:
            for detail in response.json()["error"]["details"]:
                match = _DELAY.match(str(detail.get("retryDelay", "")))
                if match:
                    seconds = float(match.group(1))
        except (ValueError, KeyError, TypeError, AttributeError):
            header = response.headers.get("retry-after", "")
            if header.isdigit():
                seconds = float(header)
        return min(max(seconds, 0.0), self._max_retry_delay)

    def _vectors(self, response: httpx.Response, expected: int) -> list[list[float]]:
        try:
            body: Any = response.json()
            vectors = [[float(v) for v in item["values"]] for item in body["embeddings"]]
        except (ValueError, KeyError, TypeError):
            raise EmbeddingError("embedding_failed:malformed_response") from None
        if len(vectors) != expected:
            raise EmbeddingError("embedding_failed:malformed_response")
        if any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingError("embedding_failed:wrong_dimensions")
        out: list[list[float]] = []
        for vector in vectors:
            norm = math.sqrt(sum(v * v for v in vector))
            if not math.isfinite(norm) or norm == 0.0:
                raise EmbeddingError("embedding_failed:malformed_response")
            out.append([v / norm for v in vector])
        return out
