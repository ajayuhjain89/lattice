"""Gemini API answer generation over plain HTTP (Phase 6; BYOK, ADR-011, ADR-012).

One ``POST /v1beta/models/{model}:generateContent`` per generation call:

* ``systemInstruction`` carries Lattice's fixed instructions; one user turn carries the
  delimited question and evidence;
* ``generationConfig``: ``responseMimeType = application/json`` with
  ``responseJsonSchema`` (structured output), ``temperature = 0``, ``maxOutputTokens``.
  Thinking is left at the model's default (``minimal`` for Flash-Lite);
* no ``tools``: the model cannot fetch, search or act.

Default model: ``gemini-3.5-flash-lite`` -- the current stable, lowest-cost Gemini text
model ("fastest, most cost-effective 3.5 model"), checked against the official model
list on 2026-09-29. Model codes change; the model is configuration
(``model_providers.model``), never a constant in answering.

Facts used from the official API reference (checked 2026-09-29): the response text is
``candidates[0].content.parts[*].text`` (parts marked ``thought`` are skipped);
``finishReason`` ``STOP`` is success, ``MAX_TOKENS`` a truncated answer, anything else
(``SAFETY``, ``RECITATION``, ``PROHIBITED_CONTENT``, ...) a refusal; a
``promptFeedback.blockReason`` is a refusal; ``usageMetadata.promptTokenCount`` /
``candidatesTokenCount`` are the token counts.

* **Credential**: resolved per organization from its secret reference (never a global
  key), held as ``SecretStr``, sent only in the ``x-goog-api-key`` header; never
  logged, never in an error or ``repr``.
* **Data use**: declared with the credential (``unpaid`` / ``paid``, ADR-010). Unpaid
  means ``training_opt_out = False``: the egress policy then allows synthetic
  organizations only, and the factory refuses the key in production.
* **Retries**: none for quota (429 ⇒ ``rate_limited``, or ``quota_exhausted`` when the
  error names a per-day quota, after which this instance sends nothing more); one retry
  for 5xx or a timeout. Other statuses fail with a fixed code; bodies are never read
  into errors.
"""

import asyncio
import math
from typing import Any, Final, Literal

import httpx
from pydantic import SecretStr

from lattice.providers.egress import Deployment
from lattice.providers.generation import GenerationError, GenerationRequest, GenerationResult

DEFAULT_MODEL: Final = "gemini-3.5-flash-lite"
DEFAULT_BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta"
DataUse = Literal["unpaid", "paid"]
_RETRYABLE: Final = frozenset({500, 502, 503, 504})


class GeminiGenerationProvider:
    provider = "gemini"
    deployment: Deployment = "external_api"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str = DEFAULT_MODEL,
        data_use: DataUse = "unpaid",
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        max_attempts: int = 2,
        backoff_seconds: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.get_secret_value():
            raise ValueError("a Gemini API key is required")
        self.model = model
        self.data_use = data_use
        self.training_opt_out = data_use == "paid"
        self.retention_mode = (
            "abuse_monitoring_limited" if data_use == "paid" else "unpaid_product_improvement"
        )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._transport = transport
        self._quota_exhausted = False
        self.requests = 0  # operational counter (evaluation reports)

    def __repr__(self) -> str:  # never the key
        return f"GeminiGenerationProvider(model={self.model!r}, data_use={self.data_use!r})"

    def payload(self, request: GenerationRequest) -> dict[str, Any]:
        return {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": request.schema,
                "temperature": 0,
                "maxOutputTokens": request.max_output_tokens,
            },
        }

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if self._quota_exhausted:
            raise GenerationError("generation_failed:quota_exhausted")  # nothing sent
        headers = {"x-goog-api-key": self._api_key.get_secret_value()}
        url = f"/models/{self.model}:generateContent"
        body = self.payload(request)
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        ) as client:
            for attempt in range(1, self._max_attempts + 1):
                self.requests += 1
                try:
                    response = await client.post(url, json=body, headers=headers)
                except httpx.TimeoutException:
                    code = "generation_failed:timeout"
                except httpx.HTTPError:
                    code = "generation_failed:network"
                else:
                    status = response.status_code
                    if status == 200:
                        return self._result(response)
                    if status in (401, 403):
                        raise GenerationError("generation_failed:unauthorized")
                    if status == 404:
                        raise GenerationError("generation_failed:not_found")
                    if status == 429:
                        if self._daily_quota(response):
                            self._quota_exhausted = True
                            raise GenerationError("generation_failed:quota_exhausted")
                        raise GenerationError("generation_failed:rate_limited")
                    if status not in _RETRYABLE:
                        raise GenerationError("generation_failed:provider_error")
                    code = "generation_failed:provider_error"
                if attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff * attempt)
        raise GenerationError(code)

    @staticmethod
    def _daily_quota(response: httpx.Response) -> bool:
        try:
            details = response.json()["error"]["details"]
            return any(
                "PerDay" in str(v.get("quotaId", ""))
                for d in details
                for v in d.get("violations", [])
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return False

    @staticmethod
    def _result(response: httpx.Response) -> GenerationResult:
        try:
            body: Any = response.json()
        except ValueError:
            raise GenerationError("generation_failed:malformed_response") from None
        if not isinstance(body, dict):
            raise GenerationError("generation_failed:malformed_response")
        feedback = body.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise GenerationError("generation_failed:refused")
        try:
            candidate = body["candidates"][0]
            finish = candidate.get("finishReason", "STOP")
            parts = candidate.get("content", {}).get("parts", [])
            text = "".join(
                str(p["text"])
                for p in parts
                if isinstance(p, dict) and "text" in p
                if not p.get("thought")
            )
        except (KeyError, IndexError, TypeError, AttributeError):
            raise GenerationError("generation_failed:malformed_response") from None
        if finish == "MAX_TOKENS":
            raise GenerationError("generation_failed:truncated")
        if finish != "STOP":
            raise GenerationError("generation_failed:refused")
        if not text.strip():
            raise GenerationError("generation_failed:malformed_response")
        raw_usage = body.get("usageMetadata")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        return GenerationResult(
            text=text,
            input_tokens=_count(usage.get("promptTokenCount")),
            output_tokens=_count(usage.get("candidatesTokenCount")),
        )


def _count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, float) and math.isfinite(value) and value >= 0:
        return int(value)
    return None
