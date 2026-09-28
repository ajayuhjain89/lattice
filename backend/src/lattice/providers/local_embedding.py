"""Local embeddings: an open-weight model run in-process with ONNX Runtime (ADR-009).

``deployment = "local"``: inference happens inside the Lattice deployment, so content
never leaves it. That changes the *egress* reading (the organization's external egress
level does not govern it; its own approval does, default ``internal``), never
authorization: only content the pipeline was already allowed to handle is embedded.

Two layers:

* ``Encoder`` -- text in, pooled vectors out, synchronous and CPU-bound. The production
  encoder is ``OnnxEncoder`` (``tokenizers`` + ``onnxruntime`` + ``numpy``, the optional
  ``local`` dependency group); tests substitute a tiny ONNX model or a stub.
* ``LocalEmbeddingProvider`` -- the ``EmbeddingProvider``: applies the model card's
  query/document prefixes for ``purpose``, batches, runs the encoder off the event loop,
  and checks every vector (count, dimension, finite, non-zero, unit length after
  normalization). Failures are ``EmbeddingError`` codes, never silently another model.

No network: the encoder reads two local files (``tokenizer.json`` and the ONNX graph)
and nothing else. The weights come only from ``lattice models fetch`` (see
``lattice.providers.local_models``).
"""

import asyncio
import math
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from lattice.providers.egress import Deployment
from lattice.providers.embedding import EmbeddingError, Purpose
from lattice.providers.local_models import LocalModelSpec, LocalModelUnavailable, check_present

if TYPE_CHECKING:  # pragma: no cover
    import numpy


class Encoder(Protocol):
    @property
    def dimensions(self) -> int: ...

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """One pooled, L2-normalized vector per text, in order."""
        ...


class OnnxEncoder:
    """Tokenize with the model's own ``tokenizer.json``, run the ONNX graph on the CPU,
    pool (``cls`` or ``mean``) and L2-normalize -- what the model card's reference
    code does."""

    def __init__(self, spec: LocalModelSpec, directory: Path, *, threads: int = 0) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError:
            raise LocalModelUnavailable(
                "local_runtime_missing",
                "the local embedding runtime is not installed; "
                "run `uv sync --extra local` in backend/",
            ) from None
        self._np = np
        self.spec = spec
        self.dimensions = spec.dimensions
        self.truncated = 0  # inputs cut at max_tokens so far
        try:
            tokenizer = Tokenizer.from_file(str(directory / spec.tokenizer_file))
            pad_token = "[PAD]" if tokenizer.token_to_id("[PAD]") is not None else "<pad>"
            pad_id = tokenizer.token_to_id(pad_token)
            if pad_id is None:
                raise ValueError("no padding token")
            tokenizer.enable_truncation(max_length=spec.max_tokens)
            tokenizer.enable_padding(pad_id=pad_id, pad_token=pad_token)
            options = ort.SessionOptions()
            options.intra_op_num_threads = threads
            options.log_severity_level = 3
            session = ort.InferenceSession(
                str(directory / spec.onnx_file), options, providers=["CPUExecutionProvider"]
            )
        except LocalModelUnavailable:
            raise
        except Exception:  # corrupt or foreign files: the runtime's own errors vary
            raise LocalModelUnavailable(
                "local_model_invalid",
                f"{spec.name} could not be loaded from {directory}; fetch it again",
            ) from None
        self._tokenizer = tokenizer
        self._session = session
        self._inputs = {i.name for i in session.get_inputs()}
        if not {"input_ids", "attention_mask"} <= self._inputs:
            raise LocalModelUnavailable(
                "local_model_invalid", f"{spec.name}: unexpected model inputs"
            )
        self._output = session.get_outputs()[0].name

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        np = self._np
        encodings = self._tokenizer.encode_batch(list(texts))
        self.truncated += sum(1 for e in encodings if e.overflowing)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed: dict[str, Any] = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        (hidden,) = self._session.run([self._output], feed)
        pooled = self._pool(np.asarray(hidden, dtype=np.float32), mask)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.where(norms == 0, 1.0, norms)).tolist()  # type: ignore[no-any-return]

    def _pool(self, hidden: "numpy.ndarray[Any, Any]", mask: "numpy.ndarray[Any, Any]") -> Any:
        if hidden.ndim != 3:
            raise EmbeddingError("embedding_failed:malformed_response")
        if self.spec.pooling == "cls":
            return hidden[:, 0, :]
        weights = mask[:, :, None].astype(hidden.dtype)
        return (hidden * weights).sum(axis=1) / self._np.maximum(weights.sum(axis=1), 1.0)


class LocalEmbeddingProvider:
    provider = "local"
    deployment: Deployment = "local"
    retention_mode = "none"  # nothing leaves the deployment; nothing is retained
    training_opt_out = True  # no third party receives the content
    max_batch = 32

    def __init__(self, spec: LocalModelSpec, encoder: Encoder, *, inference_batch: int = 8):
        if encoder.dimensions != spec.dimensions:
            raise LocalModelUnavailable(
                "local_model_invalid",
                f"{spec.name}: encoder has {encoder.dimensions} dimensions, "
                f"spec says {spec.dimensions}",
            )
        self.spec = spec
        self.model = spec.identity
        self.dimensions = spec.dimensions
        self._encoder = encoder
        self._inference_batch = max(1, inference_batch)

    @classmethod
    def from_cache(
        cls, spec: LocalModelSpec, root: Path, *, threads: int = 0
    ) -> "LocalEmbeddingProvider":
        """Load the pinned model from the cache. Never downloads: a missing model is a
        ``LocalModelUnavailable('local_model_missing')`` naming the fetch command."""
        directory = check_present(spec, root)
        return cls(spec, OnnxEncoder(spec, directory, threads=threads))

    def __repr__(self) -> str:
        return f"LocalEmbeddingProvider(model={self.model!r}, dimensions={self.dimensions})"

    def _prefixed(self, text: str, purpose: Purpose) -> str:
        prefix = self.spec.query_prefix if purpose == "query" else self.spec.document_prefix
        return prefix + text

    def _encode_all(self, texts: list[str]) -> list[list[float]]:
        # Similar lengths batch together (less padding); results go back in order.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out: list[list[float] | None] = [None] * len(texts)
        for start in range(0, len(order), self._inference_batch):
            indices = order[start : start + self._inference_batch]
            vectors = self._encoder.encode([texts[i] for i in indices])
            if len(vectors) != len(indices):
                raise EmbeddingError("embedding_failed:malformed_response")
            for i, vector in zip(indices, vectors, strict=True):
                out[i] = self._checked(vector)
        done = [v for v in out if v is not None]
        if len(done) != len(texts):
            raise EmbeddingError("embedding_failed:malformed_response")
        return done

    def _checked(self, vector: Sequence[float]) -> list[float]:
        if not isinstance(vector, Sequence) or len(vector) != self.dimensions:
            raise EmbeddingError("embedding_failed:wrong_dimensions")
        values = [float(v) for v in vector]
        if not all(math.isfinite(v) for v in values):
            raise EmbeddingError("embedding_failed:malformed_response")
        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0.0:
            raise EmbeddingError("embedding_failed:malformed_response")
        return [v / norm for v in values]

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > self.max_batch:
            raise ValueError(f"at most {self.max_batch} texts per call")
        payload = [self._prefixed(t, purpose) for t in texts]
        try:
            return await asyncio.to_thread(self._encode_all, payload)
        except EmbeddingError:
            raise
        except Exception:  # a runtime failure inside the model: a code, never its text
            raise EmbeddingError("embedding_failed:local_inference") from None
