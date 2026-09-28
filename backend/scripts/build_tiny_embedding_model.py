"""Build the tiny ONNX embedding model and tokenizer used by the local-provider tests.

    cd backend && uv run --extra local --with onnx python scripts/build_tiny_embedding_model.py

Writes ``tests/providers/fixtures/tiny-embedding/{model.onnx,tokenizer.json}`` (about
200 KB, committed). It is not a language model: a word-level tokenizer over a small
vocabulary and a graph that returns, for each token, its (seeded random) embedding plus
the masked mean of the sequence -- so the first ([CLS]) position depends on every token,
like a real encoder's, and CLS pooling gives different vectors for different texts. It
exists so CI exercises the real ONNX Runtime path (tokenize, run, pool, normalize)
without downloading production weights. Deterministic: rerunning produces the same
files.
"""

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors

OUT = Path(__file__).resolve().parents[1] / "tests" / "providers" / "fixtures" / "tiny-embedding"
DIMENSIONS = 1024
SPECIAL = ["[PAD]", "[UNK]", "[CLS]", "[SEP]"]
VOCABULARY = (
    "represent this sentence for searching relevant passages sso redirect loop after "
    "switching idp to entra login support ticket document renewal pricing acme contract "
    "security review scim provisioning date slack thread what is the when does who owns "
    "latest status decision customer account"
)
WORDS = VOCABULARY.split()


def tokenizer() -> Tokenizer:
    vocab = {token: i for i, token in enumerate([*SPECIAL, *dict.fromkeys(WORDS)])}
    tok = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))  # noqa: S106
    tok.normalizer = normalizers.Lowercase()
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]",
        special_tokens=[("[CLS]", vocab["[CLS]"]), ("[SEP]", vocab["[SEP]"])],
    )
    return tok


def graph(vocab_size: int) -> onnx.ModelProto:
    rng = np.random.default_rng(20260928)
    table = rng.standard_normal((vocab_size, DIMENSIONS)).astype(np.float32)
    nodes = [
        helper.make_node("Gather", ["table", "input_ids"], ["emb"], axis=0),
        helper.make_node("Cast", ["attention_mask"], ["mask"], to=TensorProto.FLOAT),
        helper.make_node("Unsqueeze", ["mask", "axis2"], ["mask3"]),
        helper.make_node("Mul", ["emb", "mask3"], ["masked"]),
        helper.make_node("ReduceSum", ["masked", "axis1"], ["total"], keepdims=1),
        helper.make_node("ReduceSum", ["mask3", "axis1"], ["count"], keepdims=1),
        helper.make_node("Div", ["total", "count"], ["mean"]),
        helper.make_node("Add", ["emb", "mean"], ["last_hidden_state"]),
    ]
    g = helper.make_graph(
        nodes,
        "tiny-embedding",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "seq"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["batch", "seq"]),
        ],
        [
            helper.make_tensor_value_info(
                "last_hidden_state", TensorProto.FLOAT, ["batch", "seq", DIMENSIONS]
            )
        ],
        initializer=[
            numpy_helper.from_array(table, "table"),
            numpy_helper.from_array(np.array([1], dtype=np.int64), "axis1"),
            numpy_helper.from_array(np.array([2], dtype=np.int64), "axis2"),
        ],
    )
    model = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    model.producer_name = "lattice-tests"
    onnx.checker.check_model(model)
    return model


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tok = tokenizer()
    tok.save(str(OUT / "tokenizer.json"))
    onnx.save(graph(tok.get_vocab_size()), str(OUT / "model.onnx"))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
