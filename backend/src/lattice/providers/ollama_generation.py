"""Local answer generation through Ollama (Phase 6, ADR-012).

The zero-cost generation path: an open-weight model served by Ollama on the same host,
called over its HTTP API. Default model ``qwen3.5:4b`` (Qwen3.5-4B, Apache 2.0, ~3.4 GB
download), chosen for an 8 GB Apple-silicon machine: it fits beside PostgreSQL, and
Ollama constrains decoding to the answer's JSON schema (``format``), which is what makes
a small model's structured output reliable.

One ``POST {endpoint}/api/chat`` per call: a system message (Lattice's fixed
instructions), one user message (delimited question and evidence), ``format`` = the
answer schema, ``stream = false``, ``think = false`` (Qwen3.5 thinks by default; the
answer contract has no place for a hidden reasoning trace), ``temperature = 0``, a
bounded context window and output length. No tools.

**Trust class (D-15) comes from the endpoint, not the product name.** A loopback
endpoint (``localhost``, ``127.0.0.0/8``, ``::1``) is inference inside the deployment
boundary: the adapter declares ``local``. Any other host is outside it and declares
``organization_controlled``, and must use HTTPS. The organization's record must state
the same class, or the invocation rule sends nothing
(``provider_deployment_mismatch``): calling a remote server "local" cannot relax policy.

Errors carry fixed codes (``generation_failed:network`` when Ollama is not running,
``not_found`` when the model is not pulled, ...), never response bodies. Ollama itself
and the model weights are installed by the operator (``ollama pull qwen3.5:4b``);
Lattice never downloads them.
"""

import ipaddress
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from lattice.providers.egress import Deployment
from lattice.providers.generation import GenerationError, GenerationRequest, GenerationResult

DEFAULT_MODEL: Final = "qwen3.5:4b"
DEFAULT_ENDPOINT: Final = "http://127.0.0.1:11434"


def is_loopback(endpoint: str) -> bool:
    host = (urlsplit(endpoint).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def endpoint_deployment(endpoint: str) -> Deployment:
    """The trust class an endpoint implies (the adapter's declaration)."""
    return "local" if is_loopback(endpoint) else "organization_controlled"


def validate_endpoint(endpoint: str) -> str:
    """A normalized endpoint, or ``ValueError``. Remote endpoints must use HTTPS; no
    credentials, query or fragment in the URL."""
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("endpoint must be an http(s) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("endpoint must not contain credentials, a query or a fragment")
    if parts.scheme != "https" and not is_loopback(endpoint):
        raise ValueError("a non-loopback endpoint must use https")
    return endpoint.rstrip("/")


class OllamaGenerationProvider:
    provider = "ollama"
    retention_mode = "none"  # the server keeps nothing Lattice sends (no logging by default)
    training_opt_out = True

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = 300.0,
        context_tokens: int = 12288,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.endpoint = validate_endpoint(endpoint)
        self.deployment: Deployment = endpoint_deployment(self.endpoint)
        self._timeout = timeout_seconds
        self._context = context_tokens
        self._transport = transport

    def __repr__(self) -> str:
        return f"OllamaGenerationProvider(model={self.model!r}, deployment={self.deployment!r})"

    def payload(self, request: GenerationRequest) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "format": request.schema,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0,
                "num_ctx": self._context,
                "num_predict": request.max_output_tokens,
            },
        }

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        async with httpx.AsyncClient(
            base_url=self.endpoint, timeout=self._timeout, transport=self._transport
        ) as client:
            try:
                response = await client.post("/api/chat", json=self.payload(request))
            except httpx.TimeoutException:
                raise GenerationError("generation_failed:timeout") from None
            except httpx.HTTPError:
                raise GenerationError("generation_failed:network") from None
        if response.status_code == 404:
            raise GenerationError("generation_failed:not_found")
        if response.status_code != 200:
            raise GenerationError("generation_failed:provider_error")
        try:
            body: Any = response.json()
            text = body["message"]["content"]
            reason = body.get("done_reason", "stop")
        except (ValueError, KeyError, TypeError):
            raise GenerationError("generation_failed:malformed_response") from None
        if reason == "length":
            raise GenerationError("generation_failed:truncated")
        if not isinstance(text, str) or not text.strip():
            raise GenerationError("generation_failed:malformed_response")
        return GenerationResult(
            text=text,
            input_tokens=_count(body.get("prompt_eval_count")),
            output_tokens=_count(body.get("eval_count")),
        )


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
