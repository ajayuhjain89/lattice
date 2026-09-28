"""The local embedding provider (ADR-009): real ONNX Runtime inference on a tiny fixture
model, the provider contract around it, and the weights cache.

No production weights are downloaded: the tiny model is committed (about 200 KB) and
the download path runs against a mock transport. "No network during inference" is
enforced, not assumed: sockets and HTTP clients raise for the whole embedding call.
"""

import hashlib
import math
import socket
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from lattice.config import ConfigurationError, Environment, ProviderSettings
from lattice.providers.embedding import EmbeddingError
from lattice.providers.factory import build_embedding_provider
from lattice.providers.local_embedding import LocalEmbeddingProvider, OnnxEncoder
from lattice.providers.local_models import (
    MODELS,
    MXBAI_EMBED_LARGE_V1,
    LocalModelUnavailable,
    ModelFile,
    check_present,
    fetch,
    spec_for,
    verify,
)

from .tiny import FIXTURE, QUERY_PREFIX, TINY, install


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    install(tmp_path)
    return tmp_path


@pytest.fixture
def provider(cache: Path) -> LocalEmbeddingProvider:
    return LocalEmbeddingProvider.from_cache(TINY, cache)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@dataclass
class StubEncoder:
    """Returns scripted vectors and records every batch it was given."""

    dimensions: int = 1024
    result: Any = None
    batches: list[list[str]] = field(default_factory=list)

    def encode(self, texts: Sequence[str]) -> Any:
        self.batches.append(list(texts))
        if self.result is not None:
            return self.result(texts)
        return [[1.0 + len(t)] + [0.5] * (self.dimensions - 1) for t in texts]


# --- identity and the pinned production model ------------------------------------------


def test_the_production_model_is_pinned_completely() -> None:
    spec = spec_for("mxbai-embed-large-v1")
    assert spec is MXBAI_EMBED_LARGE_V1
    assert len(spec.revision) == 40  # a commit, never a branch
    assert spec.dimensions == 1024  # fits vector(1024) without a schema change
    assert spec.license == "Apache-2.0"
    assert spec.identity == "mxbai-embed-large-v1@b33106f585b9"
    assert set(spec.files) == {spec.onnx_file, spec.tokenizer_file}
    assert all(len(f.sha256) == 64 for f in spec.files.values())
    assert spec.query_prefix
    assert spec.document_prefix == ""
    with pytest.raises(LocalModelUnavailable) as err:
        spec_for("some-other-model")
    assert err.value.code == "local_model_unknown"


def test_identity_dimension_and_deployment(provider: LocalEmbeddingProvider) -> None:
    assert provider.provider == "local"
    assert provider.model == TINY.identity  # the embedding space includes the revision
    assert provider.dimensions == 1024
    assert provider.deployment == "local"
    assert provider.training_opt_out is True
    assert provider.retention_mode == "none"
    assert "LocalEmbeddingProvider" in repr(provider)


# --- real inference on the tiny model -------------------------------------------------


async def test_vectors_are_unit_length_ordered_and_deterministic(
    provider: LocalEmbeddingProvider,
) -> None:
    texts = ["sso redirect loop after switching idp", "renewal pricing for acme", "slack thread"]
    first = await provider.embed(texts, "document")
    again = await provider.embed(texts, "document")
    assert first == again
    assert len(first) == 3
    for vector in first:
        assert len(vector) == 1024
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-6)
    # Order is the input order, whatever the internal length-sorted batching did.
    singles = [(await provider.embed([t], "document"))[0] for t in texts]
    for batched, single in zip(first, singles, strict=True):
        assert _cosine(batched, single) > 0.999999
    assert _cosine(first[0], first[1]) < 0.99  # different texts, different vectors


async def test_query_and_document_purposes_follow_the_model_card(cache: Path) -> None:
    stub = StubEncoder()
    provider = LocalEmbeddingProvider(TINY, stub)
    await provider.embed(["who owns scim"], "query")
    await provider.embed(["who owns scim"], "document")
    assert stub.batches == [[QUERY_PREFIX + "who owns scim"], ["who owns scim"]]
    real = LocalEmbeddingProvider.from_cache(TINY, cache)
    (query,) = await real.embed(["scim provisioning date"], "query")
    (document,) = await real.embed(["scim provisioning date"], "document")
    assert query != document  # the query instruction changes the representation


async def test_batching_and_limits() -> None:
    stub = StubEncoder()
    provider = LocalEmbeddingProvider(TINY, stub, inference_batch=8)
    texts = [f"text {'x' * (i % 7)} {i}" for i in range(provider.max_batch)]
    vectors = await provider.embed(texts, "document")
    assert len(vectors) == provider.max_batch
    assert [len(b) for b in stub.batches] == [8, 8, 8, 8]
    # Each vector belongs to its own text (the stub encodes the length).
    for text, vector in zip(texts, vectors, strict=True):
        expected = LocalEmbeddingProvider(TINY, StubEncoder())._checked(
            [1.0 + len(text)] + [0.5] * 1023
        )
        assert vector == expected
    calls = len(stub.batches)
    assert await provider.embed([], "document") == []
    assert len(stub.batches) == calls  # empty input makes no inference call
    with pytest.raises(ValueError, match="at most"):
        await provider.embed(["x"] * (provider.max_batch + 1), "document")


async def test_long_inputs_are_truncated_not_rejected(cache: Path) -> None:
    encoder = OnnxEncoder(TINY, TINY.path(cache))
    provider = LocalEmbeddingProvider(TINY, encoder)
    (vector,) = await provider.embed([" ".join(["scim"] * 100)], "document")
    assert len(vector) == 1024
    assert encoder.truncated == 1


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (lambda texts: [[0.1] * 8 for _ in texts], "embedding_failed:wrong_dimensions"),
        (lambda texts: [[math.nan] * 1024 for _ in texts], "embedding_failed:malformed_response"),
        (lambda texts: [[0.0] * 1024 for _ in texts], "embedding_failed:malformed_response"),
        (lambda texts: [[0.1] * 1024], "embedding_failed:malformed_response"),  # too few
        (lambda texts: [], "embedding_failed:malformed_response"),
        (lambda texts: 1 / 0, "embedding_failed:local_inference"),
    ],
)
async def test_malformed_inference_output_is_a_typed_failure(result: Any, code: str) -> None:
    provider = LocalEmbeddingProvider(TINY, StubEncoder(result=result))
    with pytest.raises(EmbeddingError) as err:
        await provider.embed(["a", "b"], "document")
    assert err.value.code == code


def test_an_encoder_of_the_wrong_dimension_is_refused() -> None:
    with pytest.raises(LocalModelUnavailable) as err:
        LocalEmbeddingProvider(TINY, StubEncoder(dimensions=768))
    assert err.value.code == "local_model_invalid"


# --- no network, ever, during inference ------------------------------------------------


async def test_inference_makes_no_network_call(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []

    def refuse(*args: object, **kwargs: object) -> Any:
        attempts.append("network")
        raise OSError("network access during local inference")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    provider = LocalEmbeddingProvider.from_cache(TINY, cache)  # loading: files only
    vectors = await provider.embed(["support ticket", "security review"], "document")
    vectors += await provider.embed(["what is the latest status"], "query")
    assert len(vectors) == 3
    assert attempts == []


# --- the cache: missing, corrupt, invalid, runtime missing -------------------------------


def test_a_missing_model_fails_clearly_and_never_downloads(tmp_path: Path) -> None:
    with pytest.raises(LocalModelUnavailable) as err:
        LocalEmbeddingProvider.from_cache(TINY, tmp_path)
    assert err.value.code == "local_model_missing"
    assert "lattice models fetch tiny-embedding" in str(err.value)
    assert not any(tmp_path.iterdir())  # nothing was created, let alone downloaded


def test_wrong_size_and_wrong_hash_are_corrupt(cache: Path) -> None:
    path = TINY.path(cache) / "tokenizer.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(LocalModelUnavailable) as err:
        check_present(TINY, cache)
    assert err.value.code == "local_model_corrupt"
    same_size = replace(
        TINY,
        files={**TINY.files, "tokenizer.json": ModelFile(path.stat().st_size, "0" * 64)},
    )
    check_present(same_size, cache)  # sizes only
    with pytest.raises(LocalModelUnavailable) as err:
        verify(same_size, cache)
    assert err.value.code == "local_model_corrupt"


def test_foreign_files_are_an_invalid_model(tmp_path: Path) -> None:
    directory = TINY.path(tmp_path)
    directory.mkdir(parents=True)
    (directory / "tokenizer.json").write_bytes((FIXTURE / "tokenizer.json").read_bytes())
    (directory / "model.onnx").write_bytes(b"not an onnx graph")
    garbage = replace(
        TINY,
        files={
            **TINY.files,
            "model.onnx": ModelFile(17, hashlib.sha256(b"not an onnx graph").hexdigest()),
        },
    )
    with pytest.raises(LocalModelUnavailable) as err:
        LocalEmbeddingProvider.from_cache(garbage, tmp_path)
    assert err.value.code == "local_model_invalid"


def test_a_missing_runtime_is_reported(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def no_onnx(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "onnxruntime":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_onnx)
    with pytest.raises(LocalModelUnavailable) as err:
        LocalEmbeddingProvider.from_cache(TINY, cache)
    assert err.value.code == "local_runtime_missing"
    assert "uv sync --extra local" in str(err.value)


# --- fetch: explicit, pinned, verified ---------------------------------------------------


def _hub(files: dict[str, bytes], seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        name = request.url.path.split(f"/resolve/{TINY.revision}/", 1)[1]
        return httpx.Response(200, content=files[name]) if name in files else httpx.Response(404)

    return httpx.MockTransport(handler)


def test_fetch_downloads_exactly_the_pinned_files_and_verifies_them(tmp_path: Path) -> None:
    files = {name: (FIXTURE / name).read_bytes() for name in TINY.files}
    seen: list[str] = []
    directory = fetch(TINY, tmp_path, transport=_hub(files, seen))
    assert sorted(seen) == sorted(
        f"/{TINY.repository}/resolve/{TINY.revision}/{name}" for name in TINY.files
    )
    assert verify(TINY, tmp_path) == directory
    # Re-running skips verified files.
    fetch(TINY, tmp_path, transport=_hub(files, seen))
    assert len(seen) == len(TINY.files)


def test_a_tampered_download_never_becomes_a_cache_entry(tmp_path: Path) -> None:
    files = {name: (FIXTURE / name).read_bytes() for name in TINY.files}
    files["tokenizer.json"] = files["tokenizer.json"].replace(b"scim", b"SCIM")
    with pytest.raises(LocalModelUnavailable) as err:
        fetch(TINY, tmp_path, transport=_hub(files, []))
    assert err.value.code == "local_model_corrupt"
    leftovers = [p.name for p in TINY.path(tmp_path).iterdir()]
    assert "tokenizer.json" not in leftovers
    assert not any(name.endswith(".partial") for name in leftovers)


# --- the factory ------------------------------------------------------------------------


def _settings(tmp_path: Path, **overrides: Any) -> ProviderSettings:
    return ProviderSettings(env=Environment.TEST, model_dir=tmp_path, **overrides)


def test_factory_local_requires_a_cached_known_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ConfigurationError, match="local_model_missing"):
        build_embedding_provider("local", _settings(tmp_path))
    with pytest.raises(ConfigurationError, match="local_model_unknown"):
        build_embedding_provider("local", _settings(tmp_path, local_embedding_model="nope"))
    install(tmp_path)
    monkeypatch.setitem(MODELS, TINY.name, TINY)
    built = build_embedding_provider("local", _settings(tmp_path, local_embedding_model=TINY.name))
    assert isinstance(built, LocalEmbeddingProvider)
    assert built.model == TINY.identity
    # A model that does not fit the deployment's vector(1024) is refused, not adapted.
    small = replace(TINY, name="tiny-768", dimensions=768)
    monkeypatch.setitem(MODELS, small.name, small)
    with pytest.raises(ConfigurationError, match="768 dimensions"):
        build_embedding_provider("local", _settings(tmp_path, local_embedding_model=small.name))
    # Production is allowed: a local model is a real deployment option.
    prod = ProviderSettings(
        env=Environment.PRODUCTION, model_dir=tmp_path, local_embedding_model=TINY.name
    )
    assert isinstance(build_embedding_provider("local", prod), LocalEmbeddingProvider)
