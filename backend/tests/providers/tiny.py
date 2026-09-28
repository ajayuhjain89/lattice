"""The tiny ONNX embedding model (scripts/build_tiny_embedding_model.py) as a pinned
``LocalModelSpec``, so tests run the real ONNX Runtime path without production
weights."""

import hashlib
import shutil
from pathlib import Path

from lattice.providers.local_models import LocalModelSpec, ModelFile

FIXTURE = Path(__file__).parent / "fixtures" / "tiny-embedding"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _pin(name: str) -> ModelFile:
    data = (FIXTURE / name).read_bytes()
    return ModelFile(len(data), hashlib.sha256(data).hexdigest())


TINY = LocalModelSpec(
    name="tiny-embedding",
    repository="lattice-tests/tiny-embedding",
    revision="0123456789abcdef0123456789abcdef01234567",
    license="test fixture",
    dimensions=1024,
    max_tokens=16,
    pooling="cls",
    query_prefix=QUERY_PREFIX,
    document_prefix="",
    onnx_file="model.onnx",
    tokenizer_file="tokenizer.json",
    files={"model.onnx": _pin("model.onnx"), "tokenizer.json": _pin("tokenizer.json")},
)


def install(root: Path) -> Path:
    """Put the tiny model into a model cache at ``root``, as ``lattice models fetch``
    would."""
    directory = TINY.path(root)
    directory.mkdir(parents=True, exist_ok=True)
    for name in TINY.files:
        shutil.copyfile(FIXTURE / name, directory / name)
    return directory
