"""Open-weight embedding models Lattice can run locally, and their weights cache
(ADR-009).

A model is pinned completely: Hugging Face repository, **commit revision**, and the
size and SHA-256 of every file Lattice loads. Nothing floats: a new revision is a new
entry and therefore a new embedding space (``identity`` includes the revision).

Weights are never part of the repository and are never downloaded implicitly -- not by
tests, CI, migrations, the API or a sync. ``lattice models fetch <name>`` is the only
code path that touches the network, downloads exactly the pinned files over HTTPS,
verifies size and SHA-256 before moving each file into place, and is safe to re-run.
After that, inference is fully offline.

Cache layout: ``<model dir>/<repository>/<revision>/<file>``. The model directory is
``LATTICE_MODEL_DIR``, default ``~/.cache/lattice/models``.
"""

import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

import httpx

Pooling = Literal["cls", "mean"]
DEFAULT_MODEL_DIR: Final = Path("~/.cache/lattice/models")
HF_BASE_URL: Final = "https://huggingface.co"


@dataclass(frozen=True, slots=True)
class ModelFile:
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class LocalModelSpec:
    """Everything needed to run one model the way its model card specifies."""

    name: str
    repository: str
    revision: str  # a full commit SHA, never a branch
    license: str
    dimensions: int
    max_tokens: int  # the model's sequence limit; longer inputs are truncated
    pooling: Pooling
    query_prefix: str  # prepended to queries (purpose = "query")
    document_prefix: str  # prepended to documents (purpose = "document")
    onnx_file: str
    tokenizer_file: str
    files: Mapping[str, ModelFile] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        """The embedding-space name recorded with every vector
        (``chunks.embedding_model``, ``model_providers.model``)."""
        return f"{self.name}@{self.revision[:12]}"

    def path(self, root: Path) -> Path:
        return root.expanduser() / self.repository / self.revision

    @property
    def download_bytes(self) -> int:
        return sum(f.size for f in self.files.values())


MXBAI_EMBED_LARGE_V1: Final = LocalModelSpec(
    name="mxbai-embed-large-v1",
    repository="mixedbread-ai/mxbai-embed-large-v1",
    revision="b33106f585b9ce46904ad7443a3b52b7a63e231c",
    license="Apache-2.0",
    dimensions=1024,
    max_tokens=512,
    pooling="cls",
    # Model card: the retrieval prompt goes on queries only; documents get none.
    query_prefix="Represent this sentence for searching relevant passages: ",
    document_prefix="",
    onnx_file="onnx/model.onnx",
    tokenizer_file="tokenizer.json",
    files={
        "onnx/model.onnx": ModelFile(
            1336854282, "adb53ed475faa339bfad3bd2bdb7e6a30b4f47280ade9811f81bef7953f9ab77"
        ),
        "tokenizer.json": ModelFile(
            711396, "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"
        ),
    },
)

# The documented alternative (ADR-009 "Reconsider when"): multilingual, 8k context,
# 568M parameters. Evaluated in the Phase 5 closure; not the default.
SNOWFLAKE_ARCTIC_EMBED_L_V2: Final = LocalModelSpec(
    name="snowflake-arctic-embed-l-v2.0",
    repository="Snowflake/snowflake-arctic-embed-l-v2.0",
    revision="ac6544c8a46e00af67e330e85a9028c66b8cfd9a",
    license="Apache-2.0",
    dimensions=1024,
    max_tokens=1024,  # the model accepts 8,192; Lattice's chunks are far shorter
    pooling="cls",
    # Model card: "query: " on queries only; documents get no prefix.
    query_prefix="query: ",
    document_prefix="",
    onnx_file="onnx/model.onnx",
    tokenizer_file="tokenizer.json",
    files={
        "onnx/model.onnx": ModelFile(
            702280, "f74aa79745ccfb1e75daa7e8e6552a78402d4de193eb8ca67a931358d3e0a25e"
        ),
        "onnx/model.onnx_data": ModelFile(
            2266886160, "fe7d75ff258fbda10a6bea63c5422df5579d625355b1aca69ba6923c0ba604a9"
        ),
        "tokenizer.json": ModelFile(
            17083074, "39feb9863a378165ab9c5c689047203d789422966c0c58721c5309fd039a8edc"
        ),
    },
)

MODELS: Final[dict[str, LocalModelSpec]] = {
    spec.name: spec for spec in (MXBAI_EMBED_LARGE_V1, SNOWFLAKE_ARCTIC_EMBED_L_V2)
}
DEFAULT_LOCAL_MODEL: Final = MXBAI_EMBED_LARGE_V1.name


class LocalModelUnavailable(Exception):
    """The local model cannot be used. ``code`` is fixed: ``local_runtime_missing``,
    ``local_model_unknown``, ``local_model_missing``, ``local_model_corrupt``,
    ``local_model_invalid``. The message says what to do; no silent fallback to any
    other model ever happens."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def spec_for(name: str) -> LocalModelSpec:
    try:
        return MODELS[name]
    except KeyError:
        known = ", ".join(sorted(MODELS))
        raise LocalModelUnavailable(
            "local_model_unknown", f"unknown local model {name!r} (known: {known})"
        ) from None


def check_present(spec: LocalModelSpec, root: Path) -> Path:
    """The model's directory, if every pinned file is present with its pinned size.
    Cheap (no hashing); ``verify`` hashes."""
    directory = spec.path(root)
    for name, expected in spec.files.items():
        path = directory / name
        if not path.is_file():
            raise LocalModelUnavailable(
                "local_model_missing",
                f"{spec.name} is not in the model cache ({directory}); "
                f"run `lattice models fetch {spec.name}` (downloads "
                f"{spec.download_bytes / 1e9:.2f} GB once)",
            )
        if path.stat().st_size != expected.size:
            raise LocalModelUnavailable(
                "local_model_corrupt",
                f"{name} of {spec.name} has the wrong size; "
                f"run `lattice models fetch {spec.name}` again",
            )
    return directory


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(spec: LocalModelSpec, root: Path) -> Path:
    """Every pinned file present with its pinned size and SHA-256."""
    directory = check_present(spec, root)
    for name, expected in spec.files.items():
        if _sha256(directory / name) != expected.sha256:
            raise LocalModelUnavailable(
                "local_model_corrupt",
                f"{name} of {spec.name} does not match its pinned SHA-256; "
                f"delete {directory} and fetch again",
            )
    return directory


def _download(
    client: httpx.Client,
    url: str,
    name: str,
    partial: Path,
    expected: ModelFile,
    progress: Callable[[str, int, int], None] | None,
) -> None:
    """Append to ``partial`` from where it stopped (HTTP Range), re-hashing what is
    already there. A server that ignores the range gets a clean restart."""
    digest = hashlib.sha256()
    have = partial.stat().st_size if partial.is_file() else 0
    if have > expected.size:
        partial.unlink()
        have = 0
    if have:
        with partial.open("rb") as existing:
            for block in iter(lambda: existing.read(1 << 20), b""):
                digest.update(block)
    headers = {"Range": f"bytes={have}-"} if have else {}
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code == 200 and have:
            digest, have = hashlib.sha256(), 0  # range ignored: start over
        elif response.status_code not in (200, 206):
            raise LocalModelUnavailable(
                "local_model_missing", f"download of {name} failed (HTTP {response.status_code})"
            )
        with partial.open("ab" if have else "wb") as out:
            written = have
            for block in response.iter_bytes():
                out.write(block)
                digest.update(block)
                written += len(block)
                if progress is not None:
                    progress(name, written, expected.size)
    if written != expected.size or digest.hexdigest() != expected.sha256:
        partial.unlink(missing_ok=True)
        raise LocalModelUnavailable(
            "local_model_corrupt", f"{name} does not match its pinned size/SHA-256"
        )


def fetch(
    spec: LocalModelSpec,
    root: Path,
    *,
    base_url: str = HF_BASE_URL,
    transport: httpx.BaseTransport | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    attempts: int = 5,
) -> Path:
    """Download the pinned files into the cache (explicit operator action only).
    Files already present and verified are skipped. A file is written to a
    ``.partial`` name, verified against its pinned size and SHA-256, then renamed: an
    interrupted or tampered download never becomes a cache entry. An interrupted
    transfer resumes where it stopped (up to ``attempts`` times per file; running the
    command again resumes too)."""
    directory = spec.path(root)
    with httpx.Client(
        base_url=base_url, transport=transport, follow_redirects=True, timeout=60.0
    ) as client:
        for name, expected in spec.files.items():
            target = directory / name
            if (
                target.is_file()
                and target.stat().st_size == expected.size
                and _sha256(target) == expected.sha256
            ):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".partial")
            url = f"/{spec.repository}/resolve/{spec.revision}/{name}"
            for attempt in range(1, attempts + 1):
                try:
                    _download(client, url, name, partial, expected, progress)
                    break
                except httpx.HTTPError:
                    if attempt == attempts:
                        raise LocalModelUnavailable(
                            "local_model_missing",
                            f"download of {name} was interrupted; run the command again to resume",
                        ) from None
            os.replace(partial, target)
    return verify(spec, root)
