"""Generation adapters (Gemini, Ollama), the factory and secret references -- against
mock HTTP transports only, never a real service.

Request shape (instructions, prompt, JSON schema, no tools), response parsing, fixed
error codes (never a response body), the key only in its header and never in a URL,
``repr``, error or log line, per-day quota stops further sends, the trust class of an
Ollama endpoint follows the endpoint (loopback = local), and secret references cannot
name arbitrary process variables.
"""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from lattice.answering.models import ANSWER_SCHEMA
from lattice.config import Environment
from lattice.providers.egress import ProviderRecord
from lattice.providers.gemini_generation import GeminiGenerationProvider
from lattice.providers.generation import (
    FakeGenerationProvider,
    GenerationError,
    GenerationRequest,
)
from lattice.providers.generation_factory import DefaultGenerationFactory, GenerationUnavailable
from lattice.providers.ollama_generation import (
    OllamaGenerationProvider,
    endpoint_deployment,
    validate_endpoint,
)
from lattice.providers.records import CredentialRef, GenerationConfig
from lattice.providers.secrets import EnvironmentSecretStore, SecretUnavailable, parse_reference

KEY = "AIza-generation-test-key-DO-NOT-LEAK"
REQUEST = GenerationRequest(system="SYSTEM", prompt="PROMPT", schema=ANSWER_SCHEMA)
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
)


def _gemini(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any
) -> tuple[GeminiGenerationProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = GeminiGenerationProvider(
        SecretStr(KEY), backoff_seconds=0, transport=httpx.MockTransport(record), **kwargs
    )
    return provider, seen


def _candidate(text: str, finish: str = "STOP") -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30},
    }


async def test_gemini_request_shape() -> None:
    provider, seen = _gemini(lambda r: httpx.Response(200, json=_candidate('{"a": 1}')))
    result = await provider.generate(REQUEST)
    assert result.text == '{"a": 1}'
    assert (result.input_tokens, result.output_tokens) == (120, 30)
    (request,) = seen
    assert str(request.url) == GEMINI_URL
    assert request.headers["x-goog-api-key"] == KEY
    assert KEY not in str(request.url)
    body = json.loads(request.content)
    assert body == {
        "systemInstruction": {"parts": [{"text": "SYSTEM"}]},
        "contents": [{"role": "user", "parts": [{"text": "PROMPT"}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": ANSWER_SCHEMA,
            "temperature": 0,
            "maxOutputTokens": 2048,
        },
    }
    assert "tools" not in body  # capability starvation: no tools, ever
    assert KEY not in repr(provider)


async def test_gemini_skips_thought_parts() -> None:
    body = _candidate("{}")
    body["candidates"][0]["content"]["parts"].insert(0, {"text": "thinking", "thought": True})
    provider, _ = _gemini(lambda r: httpx.Response(200, json=body))
    assert (await provider.generate(REQUEST)).text == "{}"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"error": {"message": KEY}}), "generation_failed:unauthorized"),
        (httpx.Response(403, text="forbidden"), "generation_failed:unauthorized"),
        (httpx.Response(404, text="no model"), "generation_failed:not_found"),
        (httpx.Response(429, json={"error": {"details": []}}), "generation_failed:rate_limited"),
        (httpx.Response(400, text="bad"), "generation_failed:provider_error"),
        (httpx.Response(200, text="<html>"), "generation_failed:malformed_response"),
        (httpx.Response(200, json={"candidates": []}), "generation_failed:malformed_response"),
        (httpx.Response(200, json=_candidate("{}", "MAX_TOKENS")), "generation_failed:truncated"),
        (httpx.Response(200, json=_candidate("{}", "SAFETY")), "generation_failed:refused"),
        (
            httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
            "generation_failed:refused",
        ),
        (httpx.Response(200, json=_candidate("   ")), "generation_failed:malformed_response"),
    ],
)
async def test_gemini_errors_are_fixed_codes(response: httpx.Response, code: str) -> None:
    provider, _ = _gemini(lambda r: response)
    with pytest.raises(GenerationError) as caught:
        await provider.generate(REQUEST)
    assert caught.value.code == code
    assert str(caught.value) == code  # never the body
    assert KEY not in str(caught.value)


async def test_gemini_retries_server_errors_once_then_fails() -> None:
    provider, seen = _gemini(lambda r: httpx.Response(503))
    with pytest.raises(GenerationError, match="provider_error"):
        await provider.generate(REQUEST)
    assert len(seen) == 2


async def test_gemini_timeout_and_network() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    for handler, code in ((timeout, "timeout"), (down, "network")):
        provider, _ = _gemini(handler)
        with pytest.raises(GenerationError, match=code):
            await provider.generate(REQUEST)


async def test_a_per_day_quota_stops_all_further_sends() -> None:
    quota = {
        "error": {
            "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProject-FreeTier"}]}]
        }
    }
    provider, seen = _gemini(lambda r: httpx.Response(429, json=quota))
    for _ in range(3):
        with pytest.raises(GenerationError, match="quota_exhausted"):
            await provider.generate(REQUEST)
    assert len(seen) == 1  # the quota is never probed again by this instance


async def test_the_key_never_reaches_logs(caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = _gemini(lambda r: httpx.Response(500, json={"error": {"message": KEY}}))
    with caplog.at_level(logging.DEBUG), pytest.raises(GenerationError):
        await provider.generate(REQUEST)
    assert KEY not in caplog.text


def test_gemini_data_use_declaration() -> None:
    unpaid = GeminiGenerationProvider(SecretStr(KEY))
    paid = GeminiGenerationProvider(SecretStr(KEY), data_use="paid")
    assert (unpaid.training_opt_out, paid.training_opt_out) == (False, True)
    assert unpaid.deployment == paid.deployment == "external_api"
    assert unpaid.model == "gemini-3.5-flash-lite"


# --- Ollama -----------------------------------------------------------------------------


def _ollama(
    handler: Callable[[httpx.Request], httpx.Response], endpoint: str = "http://127.0.0.1:11434"
) -> tuple[OllamaGenerationProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return (
        OllamaGenerationProvider(endpoint=endpoint, transport=httpx.MockTransport(record)),
        seen,
    )


async def test_ollama_request_shape() -> None:
    reply = {
        "message": {"role": "assistant", "content": '{"x": 1}'},
        "done_reason": "stop",
        "prompt_eval_count": 900,
        "eval_count": 80,
    }
    provider, seen = _ollama(lambda r: httpx.Response(200, json=reply))
    result = await provider.generate(REQUEST)
    assert result.text == '{"x": 1}'
    assert (result.input_tokens, result.output_tokens) == (900, 80)
    (request,) = seen
    assert str(request.url) == "http://127.0.0.1:11434/api/chat"
    body = json.loads(request.content)
    assert body["model"] == "qwen3.5:4b"
    assert body["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "PROMPT"},
    ]
    assert body["format"] == ANSWER_SCHEMA
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"]["temperature"] == 0
    assert "tools" not in body
    assert provider.deployment == "local"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(404, json={"error": "model not found"}), "not_found"),
        (httpx.Response(500, text="boom"), "provider_error"),
        (
            httpx.Response(200, json={"message": {"content": "{}"}, "done_reason": "length"}),
            "truncated",
        ),
        (httpx.Response(200, json={"message": {}}), "malformed_response"),
        (httpx.Response(200, json={"message": {"content": ""}}), "malformed_response"),
    ],
)
async def test_ollama_errors(response: httpx.Response, code: str) -> None:
    provider, _ = _ollama(lambda r: response)
    with pytest.raises(GenerationError, match=code):
        await provider.generate(REQUEST)


async def test_ollama_not_running_is_a_network_failure() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider, _ = _ollama(down)
    with pytest.raises(GenerationError, match="network"):
        await provider.generate(REQUEST)


@pytest.mark.parametrize(
    ("endpoint", "deployment"),
    [
        ("http://127.0.0.1:11434", "local"),
        ("http://localhost:11434", "local"),
        ("http://[::1]:11434", "local"),
        ("http://127.8.9.10:11434", "local"),
        ("https://llm.halden.example", "organization_controlled"),
        ("https://10.0.0.5:11434", "organization_controlled"),
    ],
)
def test_the_trust_class_follows_the_endpoint(endpoint: str, deployment: str) -> None:
    assert endpoint_deployment(validate_endpoint(endpoint)) == deployment


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://llm.halden.example",  # remote over plain HTTP
        "https://user:pass@llm.halden.example",
        "https://llm.halden.example/?x=1",
        "ftp://127.0.0.1",
        "127.0.0.1:11434",
    ],
)
def test_unsafe_endpoints_are_refused(endpoint: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        validate_endpoint(endpoint)


# --- factory ----------------------------------------------------------------------------


def _config(
    provider: str, endpoint: str | None = None, deployment: str = "external_api"
) -> GenerationConfig:
    return GenerationConfig(
        ProviderRecord(provider, "m", deployment, "internal", True, False), endpoint, True
    )


def test_factory_declarations() -> None:
    factory = DefaultGenerationFactory(Environment.TEST)
    fake = factory.declare(_config("fake"), None)
    assert (fake.deployment, fake.requires_credential) == ("external_api", False)
    local = factory.declare(_config("ollama", "http://127.0.0.1:11434", "local"), None)
    assert (local.deployment, local.requires_credential) == ("local", False)
    remote = factory.declare(_config("ollama", "https://llm.example.com"), None)
    assert remote.deployment == "organization_controlled"
    unpaid = factory.declare(_config("gemini"), None)
    assert (unpaid.training_opt_out, unpaid.requires_credential) == (False, True)
    paid = factory.declare(
        _config("gemini"), CredentialRef(uuid.uuid4(), "gemini", "env:X", "paid")
    )
    assert paid.training_opt_out is True
    with pytest.raises(GenerationUnavailable, match="provider_unsupported"):
        factory.declare(_config("openai"), None)
    with pytest.raises(GenerationUnavailable, match="endpoint_invalid"):
        factory.declare(_config("ollama", "http://llm.example.com"), None)


def test_factory_refuses_test_doubles_and_unpaid_keys_in_production() -> None:
    factory = DefaultGenerationFactory(Environment.PRODUCTION)
    with pytest.raises(GenerationUnavailable, match="refused_in_production"):
        factory.build(_config("fake"), None, None)
    with pytest.raises(GenerationUnavailable, match="refused_in_production"):
        factory.build(_config("gemini"), None, SecretStr(KEY))
    with pytest.raises(GenerationUnavailable, match="credential_unavailable"):
        DefaultGenerationFactory(Environment.TEST).build(_config("gemini"), None, None)
    assert isinstance(
        DefaultGenerationFactory(Environment.TEST).build(_config("fake"), None, None),
        FakeGenerationProvider,
    )


# --- secret references ---------------------------------------------------------------


@pytest.mark.parametrize(
    "reference",
    [
        "env:LATTICE_PROVIDER_SECRET_HALDEN_GEMINI",
        "env:LATTICE_GEMINI_API_KEY",
        "env:GEMINI_API_KEY",
        "env:LATTICE_OPENAI_API_KEY",
        "memory:halden-gemini",
    ],
)
def test_well_formed_references(reference: str) -> None:
    parse_reference(reference)


@pytest.mark.parametrize(
    "reference",
    [
        "env:LATTICE_DATABASE_URL",  # another secret of this process
        "env:LATTICE_MIGRATION_DATABASE_URL",
        "env:POSTGRES_PASSWORD",
        "env:PATH",
        "env:lattice_provider_secret_x",
        "AIzaSyD-a-raw-key-pasted-by-mistake",
        "file:/etc/passwd",
        "vault:secret/x",
        "memory:../x",
        "",
    ],
)
def test_references_to_anything_else_are_refused(reference: str) -> None:
    with pytest.raises(SecretUnavailable) as caught:
        parse_reference(reference)
    assert caught.value.code == "invalid_reference"
    assert reference not in str(caught.value) or reference == ""


def test_resolution() -> None:
    store = EnvironmentSecretStore(
        environ={"LATTICE_PROVIDER_SECRET_A": f"  {KEY} ", "LATTICE_PROVIDER_SECRET_EMPTY": " "},
        memory={"x": SecretStr(KEY)},
    )
    assert store.resolve("env:LATTICE_PROVIDER_SECRET_A").get_secret_value() == KEY
    assert store.resolve("memory:x").get_secret_value() == KEY
    for reference, code in (
        ("env:LATTICE_PROVIDER_SECRET_B", "missing"),
        ("env:LATTICE_PROVIDER_SECRET_EMPTY", "empty"),
        ("memory:y", "missing"),
    ):
        with pytest.raises(SecretUnavailable, match=code):
            store.resolve(reference)
        assert store.available(reference) is False
    assert store.available("env:LATTICE_PROVIDER_SECRET_A") is True
    no_memory = EnvironmentSecretStore(environ={})
    with pytest.raises(SecretUnavailable, match="scheme_not_allowed"):
        no_memory.resolve("memory:x")
    assert KEY not in repr(store.resolve("memory:x"))
