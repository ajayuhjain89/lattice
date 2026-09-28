"""The Gemini embedding adapter (ADR-010) against a mock HTTP transport -- never the
real service.

Request shape per model family (text prefixes for ``gemini-embedding-2``, task types
for ``gemini-embedding-001``), one request per text so vectors are not aggregated,
``outputDimensionality`` 1024, ordering and validation, bounded retries (a limited
quota is not hammered), fixed error codes, the key only in its header, and the data-use
declaration that keeps an unpaid key away from production and real data.
"""

import json
import logging
import math
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from lattice.config import ConfigurationError, Environment, ProviderSettings
from lattice.providers.embedding import EmbeddingError
from lattice.providers.factory import build_embedding_provider
from lattice.providers.gemini_embedding import GeminiEmbeddingProvider

KEY = "AIza-test-not-a-real-key-DO-NOT-LEAK"
SECRET_INPUT = "Project Tern acquisition terms"
URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:batchEmbedContents"
)


def _ok(request: httpx.Request, dims: int = 1024) -> httpx.Response:
    body = json.loads(request.content)
    embeddings = [
        {"values": [float(i + 1)] + [0.0] * (dims - 1)} for i in range(len(body["requests"]))
    ]
    return httpx.Response(200, json={"embeddings": embeddings})


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any
) -> tuple[GeminiEmbeddingProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = GeminiEmbeddingProvider(
        SecretStr(KEY), backoff_seconds=0, transport=httpx.MockTransport(record), **kwargs
    )
    return provider, seen


async def test_request_shape_query_and_document_semantics() -> None:
    provider, seen = _provider(_ok)
    vectors = await provider.embed(["alpha", "beta"], "document")
    assert [v[0] for v in vectors] == [1.0, 1.0]  # normalized, in order
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == URL
    assert request.headers["x-goog-api-key"] == KEY
    assert "authorization" not in request.headers
    assert KEY not in str(request.url)  # never in the URL (logs, proxies)
    assert json.loads(request.content) == {
        "requests": [
            {
                "model": "models/gemini-embedding-2",
                "content": {"parts": [{"text": f"title: none | text: {text}"}]},
                "embedContentConfig": {"outputDimensionality": 1024},
            }
            for text in ("alpha", "beta")
        ]
    }
    await provider.embed(["who owns SCIM?"], "query")
    (query,) = json.loads(seen[1].content)["requests"]
    assert query["content"]["parts"] == [{"text": "task: search result | query: who owns SCIM?"}]
    assert "taskType" not in query["embedContentConfig"]  # unsupported on gemini-embedding-2


async def test_gemini_embedding_001_uses_task_types_and_is_normalized_here() -> None:
    def unnormalized(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["requests"])
        return httpx.Response(200, json={"embeddings": [{"values": [3.0, 4.0] + [0.0] * 1022}] * n})

    provider, seen = _provider(unnormalized, model="gemini-embedding-001")
    (vector,) = await provider.embed(["alpha"], "query")
    assert vector[:2] == [0.6, 0.8]
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0)
    (request,) = json.loads(seen[0].content)["requests"]
    assert request["content"]["parts"] == [{"text": "alpha"}]
    assert request["embedContentConfig"] == {
        "outputDimensionality": 1024,
        "taskType": "RETRIEVAL_QUERY",
    }
    await provider.embed(["alpha"], "document")
    doc = json.loads(seen[1].content)["requests"][0]["embedContentConfig"]["taskType"]
    assert doc == "RETRIEVAL_DOCUMENT"
    assert str(seen[0].url).endswith("/models/gemini-embedding-001:batchEmbedContents")


async def test_batching_ordering_and_empty_input() -> None:
    def distinct(request: httpx.Request) -> httpx.Response:
        texts = [r["content"]["parts"][0]["text"] for r in json.loads(request.content)["requests"]]
        return httpx.Response(
            200,
            json={
                "embeddings": [
                    {"values": [0.0] * i + [1.0] + [0.0] * (1023 - i)} for i in range(len(texts))
                ]
            },
        )

    provider, seen = _provider(distinct)
    assert provider.max_batch == 100
    vectors = await provider.embed([f"t{i}" for i in range(100)], "document")
    assert [v.index(1.0) for v in vectors] == list(range(100))
    assert len(seen) == 1
    assert await provider.embed([], "document") == []
    assert len(seen) == 1
    with pytest.raises(ValueError, match="at most"):
        await provider.embed(["x"] * 101, "document")


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, json={"embeddings": [{"values": [0.1] * 768}]}), "wrong_dimensions"),
        (httpx.Response(200, json={"embeddings": []}), "malformed_response"),
        (httpx.Response(200, json={"nothing": True}), "malformed_response"),
        (httpx.Response(200, text="not json"), "malformed_response"),
        (
            httpx.Response(200, json={"embeddings": [{"values": [0.0] * 1024}]}),
            "malformed_response",
        ),
        (httpx.Response(401, json={"error": {"message": SECRET_INPUT}}), "unauthorized"),
        (httpx.Response(403, json={"error": {"message": SECRET_INPUT}}), "unauthorized"),
        (httpx.Response(400, json={"error": {"message": SECRET_INPUT}}), "rejected"),
    ],
)
async def test_failures_map_to_fixed_codes_without_retry(
    response: httpx.Response, code: str
) -> None:
    provider, seen = _provider(lambda request: response)
    with pytest.raises(EmbeddingError) as err:
        await provider.embed([SECRET_INPUT], "document")
    assert err.value.code == f"embedding_failed:{code}"
    assert SECRET_INPUT not in str(err.value)
    assert KEY not in str(err.value)
    assert len(seen) == 1  # not retried


async def test_server_errors_and_timeouts_are_retried_with_bounds() -> None:
    answers = [httpx.Response(503), httpx.Response(500)]

    def flaky(request: httpx.Request) -> httpx.Response:
        return answers.pop(0) if answers else _ok(request)

    provider, seen = _provider(flaky, max_attempts=3)
    assert len(await provider.embed(["a"], "document")) == 1
    assert len(seen) == 3

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    provider, seen = _provider(down, max_attempts=3)
    with pytest.raises(EmbeddingError) as err:
        await provider.embed(["a"], "document")
    assert err.value.code == "embedding_failed:timeout"
    assert len(seen) == 3


async def test_rate_limits_wait_the_server_hint_then_give_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("lattice.providers.gemini_embedding.asyncio.sleep", sleep)
    quota = httpx.Response(
        429,
        json={
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}
                ],
            }
        },
    )
    provider, seen = _provider(lambda request: quota, max_rate_limit_retries=2)
    with pytest.raises(EmbeddingError) as err:
        await provider.embed(["a"], "document")
    assert err.value.code == "embedding_failed:rate_limited"
    assert len(seen) == 3  # one call + two bounded retries, no more
    assert waits == [17.0, 17.0]
    assert provider.rate_limited == 3
    # Circuit breaker: after giving up, the next call fails without a request.
    with pytest.raises(EmbeddingError) as err:
        await provider.embed(["b"], "document")
    assert err.value.code == "embedding_failed:rate_limited"
    assert len(seen) == 3
    # A long quota window is capped: the call fails instead of waiting for hours.
    long = httpx.Response(429, json={"error": {"details": [{"retryDelay": "3600s"}]}})
    provider, _ = _provider(lambda request: long, max_rate_limit_retries=1)
    waits.clear()
    with pytest.raises(EmbeddingError):
        await provider.embed(["a"], "document")
    assert waits == [60.0]
    # A rate limit that clears is recovered from.
    answers = [quota]
    provider, seen = _provider(lambda r: answers.pop(0) if answers else _ok(r))
    assert len(await provider.embed(["a"], "document")) == 1


async def test_a_daily_quota_is_not_retried_and_stops_all_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("lattice.providers.gemini_embedding.asyncio.sleep", sleep)
    daily = httpx.Response(
        429,
        json={
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {
                                "quotaMetric": "generativelanguage.googleapis.com/"
                                "embed_content_free_tier_requests",
                                "quotaId": "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier",
                                "quotaValue": "1000",
                            }
                        ],
                    },
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "57s"},
                ],
            }
        },
    )
    provider, seen = _provider(lambda request: daily)
    with pytest.raises(EmbeddingError) as err:
        await provider.embed(["a"], "document")
    assert err.value.code == "embedding_failed:quota_exhausted"
    assert len(seen) == 1  # no retry: minutes of waiting cannot help a daily quota
    assert waits == []
    for _ in range(3):
        with pytest.raises(EmbeddingError) as err:
            await provider.embed(["b"], "document")
        assert err.value.code == "embedding_failed:quota_exhausted"
    assert len(seen) == 1  # nothing more was sent


async def test_the_key_never_reaches_logs_errors_or_repr(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    provider, _ = _provider(lambda request: httpx.Response(500, text=KEY), max_attempts=2)
    with pytest.raises(EmbeddingError) as err:
        await provider.embed([SECRET_INPUT], "document")
    assert KEY not in repr(provider)
    assert KEY not in str(err.value)
    assert KEY not in caplog.text
    assert SECRET_INPUT not in caplog.text


def test_data_use_declaration_and_provider_identity() -> None:
    unpaid = GeminiEmbeddingProvider(SecretStr(KEY))
    assert (unpaid.provider, unpaid.model, unpaid.dimensions) == (
        "gemini",
        "gemini-embedding-2",
        1024,
    )
    assert unpaid.deployment == "external_api"
    assert unpaid.training_opt_out is False  # Google may use unpaid-tier content
    assert unpaid.retention_mode == "unpaid_product_improvement"
    paid = GeminiEmbeddingProvider(SecretStr(KEY), data_use="paid")
    assert paid.training_opt_out is True
    with pytest.raises(ValueError, match="unsupported"):
        GeminiEmbeddingProvider(SecretStr(KEY), model="text-embedding-004")
    with pytest.raises(ValueError, match="128-3072"):
        GeminiEmbeddingProvider(SecretStr(KEY), dimensions=4096)
    with pytest.raises(ValueError, match="key"):
        GeminiEmbeddingProvider(SecretStr(""))


def _settings(env: Environment, **overrides: Any) -> ProviderSettings:
    return ProviderSettings(env=env, **{"gemini_api_key": None, **overrides})


def test_factory_gemini() -> None:
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        build_embedding_provider("gemini", _settings(Environment.TEST))
    built = build_embedding_provider(
        "gemini", _settings(Environment.TEST, gemini_api_key=SecretStr(KEY))
    )
    assert isinstance(built, GeminiEmbeddingProvider)
    assert built.dimensions == 1024
    assert built.training_opt_out is False
    # An unpaid key is development/synthetic only: refused in production.
    with pytest.raises(ConfigurationError, match="synthetic development data only"):
        build_embedding_provider(
            "gemini", _settings(Environment.PRODUCTION, gemini_api_key=SecretStr(KEY))
        )
    paid = build_embedding_provider(
        "gemini",
        _settings(Environment.PRODUCTION, gemini_api_key=SecretStr(KEY), gemini_data_use="paid"),
    )
    assert isinstance(paid, GeminiEmbeddingProvider)
    assert paid.training_opt_out is True
    with pytest.raises(ConfigurationError, match="LATTICE_GEMINI_EMBEDDING_MODEL"):
        build_embedding_provider(
            "gemini",
            _settings(
                Environment.TEST,
                gemini_api_key=SecretStr(KEY),
                gemini_embedding_model="embedding-001",
            ),
        )


def test_google_variable_name_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LATTICE_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    settings = ProviderSettings(env=Environment.TEST)
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == KEY
    assert KEY not in repr(settings)
