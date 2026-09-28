"""The OpenAI embedding adapter against a mock HTTP transport (no network in tests).

Request shape, response ordering and validation, retries with backoff on rate limits
and server errors only, fixed error codes that never echo input or response bodies,
and the API key kept out of everything but the Authorization header.
"""

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from lattice.config import ConfigurationError, Environment, ProviderSettings
from lattice.providers.embedding import EmbeddingError
from lattice.providers.factory import build_embedding_provider
from lattice.providers.openai_embedding import OpenAIEmbeddingProvider

KEY = "sk-test-not-a-real-key"
SECRET_INPUT = "Project Tern acquisition terms"


def _vectors(n: int, dims: int = 4) -> list[list[float]]:
    return [[float(i)] * dims for i in range(n)]


def _ok(request: httpx.Request, dims: int = 4) -> httpx.Response:
    body = json.loads(request.content)
    data = [
        {"object": "embedding", "index": i, "embedding": [float(i)] * dims}
        for i in range(len(body["input"]))
    ]
    return httpx.Response(200, json={"object": "list", "data": list(reversed(data))})


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> tuple[OpenAIEmbeddingProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = OpenAIEmbeddingProvider(
        SecretStr(KEY),
        dimensions=4,
        backoff_seconds=0,
        transport=httpx.MockTransport(record),
        **kwargs,  # type: ignore[arg-type]
    )
    return provider, seen


async def test_request_shape_and_ordering() -> None:
    provider, seen = _provider(_ok)
    vectors = await provider.embed(["a", "b", "c"], "document")
    assert vectors == _vectors(3)  # ordered by index, not by response order
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "https://api.openai.com/v1/embeddings"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {
        "model": "text-embedding-3-small",
        "input": ["a", "b", "c"],
        "dimensions": 4,
        "encoding_format": "float",
    }
    # The query/document distinction does not change the request for OpenAI.
    await provider.embed(["a"], "query")
    assert json.loads(seen[1].content)["input"] == ["a"]


async def test_empty_input_makes_no_request_and_batches_are_bounded() -> None:
    provider, seen = _provider(_ok)
    assert await provider.embed([], "document") == []
    assert seen == []
    with pytest.raises(ValueError, match="at most"):
        await provider.embed(["x"] * (provider.max_batch + 1), "document")


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_rate_limits_and_server_errors_are_retried(status: int) -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, text=SECRET_INPUT) if calls["n"] < 3 else _ok(request)

    provider, seen = _provider(flaky)
    assert await provider.embed(["a"], "document") == _vectors(1)
    assert len(seen) == 3


@pytest.mark.parametrize(
    ("handler", "code", "attempts"),
    [
        (lambda r: httpx.Response(429, text=SECRET_INPUT), "embedding_failed:rate_limited", 4),
        (lambda r: httpx.Response(503, text=SECRET_INPUT), "embedding_failed:provider_error", 4),
        (lambda r: httpx.Response(401, text=SECRET_INPUT), "embedding_failed:unauthorized", 1),
        (lambda r: httpx.Response(400, text=SECRET_INPUT), "embedding_failed:rejected", 1),
        (lambda r: httpx.Response(200, text="not json"), "embedding_failed:malformed_response", 1),
        (
            lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]}),
            "embedding_failed:malformed_response",  # wrong dimensions
            1,
        ),
        (
            lambda r: httpx.Response(200, json={"data": []}),
            "embedding_failed:malformed_response",  # wrong count
            1,
        ),
    ],
)
async def test_failures_carry_fixed_codes_and_never_echo_content(
    handler: Callable[[httpx.Request], httpx.Response], code: str, attempts: int
) -> None:
    provider, seen = _provider(handler)
    with pytest.raises(EmbeddingError) as caught:
        await provider.embed([SECRET_INPUT], "document")
    assert caught.value.code == code
    assert len(seen) == attempts
    rendered = f"{caught.value!s} {caught.value!r} {caught.value.args}"
    assert SECRET_INPUT not in rendered
    assert KEY not in rendered


async def test_network_errors_and_timeouts_are_retried_then_reported() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider, seen = _provider(down)
    with pytest.raises(EmbeddingError) as caught:
        await provider.embed(["a"], "document")
    assert (caught.value.code, len(seen)) == ("embedding_failed:network", 4)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider, seen = _provider(slow, max_attempts=2)
    with pytest.raises(EmbeddingError) as caught:
        await provider.embed(["a"], "document")
    assert (caught.value.code, len(seen)) == ("embedding_failed:timeout", 2)


def test_the_key_is_required_and_never_shown() -> None:
    with pytest.raises(ValueError, match="key is required"):
        OpenAIEmbeddingProvider(SecretStr(""))
    provider = OpenAIEmbeddingProvider(SecretStr(KEY))
    assert KEY not in repr(provider)
    assert KEY not in str(vars(provider))  # held as a SecretStr


def test_the_factory_builds_providers_and_fails_clearly() -> None:
    dev = ProviderSettings(env=Environment.DEVELOPMENT)
    assert build_embedding_provider("none", dev) is None
    fake = build_embedding_provider("fake", dev)
    assert fake is not None
    assert (fake.provider, fake.dimensions) == ("fake", 1024)
    with pytest.raises(ConfigurationError, match="LATTICE_OPENAI_API_KEY"):
        build_embedding_provider("openai", dev)
    with pytest.raises(ConfigurationError, match="production"):
        build_embedding_provider("fake", ProviderSettings(env=Environment.PRODUCTION))
    real = build_embedding_provider(
        "openai", ProviderSettings(env=Environment.PRODUCTION, openai_api_key=SecretStr(KEY))
    )
    assert real is not None
    assert (real.provider, real.model, real.dimensions) == (
        "openai",
        "text-embedding-3-small",
        1024,
    )
    assert (real.deployment, real.training_opt_out) == ("external_api", True)
