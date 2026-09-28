# ADR-009: Local (self-hosted) embedding provider

Status: Accepted (Phase 5 closure, 2026-09-28). Complements ADR-008; does not replace
it.

**ADR-008** is the *external API* decision: which vendor to call when an organization
uses an external embedding provider (OpenAI `text-embedding-3-small`). **This ADR** is
the *local/self-hosted* decision: which open-weight model Lattice runs itself, so that
Lattice works with no paid API, no external credential and no inference egress. The two
serve different deployments, and both stay behind the same `EmbeddingProvider`
Protocol.

## Context

- The project owner has no paid OpenAI credential and does not want Lattice to require
  one. Phase 5's semantic quality could therefore not be measured with a real model
  (only the `FakeEmbeddingProvider` test double had run).
- `chunks.embedding` is `vector(1024)`. The dimension is per deployment, and changing it
  is a migration plus a full re-embed. A local model should fit 1024 natively rather
  than force a schema change or a projection hack.
- The corpus is English enterprise text: documents, support tickets, Slack. Chunks are
  at most ~600 estimated tokens (969 characters at most in the synthetic corpus,
  including the contextual header). Queries are short questions.
- Development hardware: an Apple M1 with 8 GB of RAM. Servers are ordinary x86-64 or
  arm64 Linux, usually without a GPU.
- Dependency discipline: Lattice's backend has 9 direct dependencies. A local model
  must not bring in a model-serving platform for one embedding call.

## Candidates

Facts from each model's official Hugging Face model card and repository metadata
(`huggingface.co/api/models/<id>`), retrieved 2026-09-28. Vendor benchmark numbers
are the vendors' own and use different suites, so they are not directly comparable.

| | `mixedbread-ai/mxbai-embed-large-v1` | `BAAI/bge-large-en-v1.5` | `Snowflake/snowflake-arctic-embed-l-v2.0` | `Qwen/Qwen3-Embedding-0.6B` |
| --- | --- | --- | --- | --- |
| Revision checked | `b33106f585b9` (2026-01-23) | `d4aa6901d3a4` (2024-02-21) | `ac6544c8a46e` (2025-07-28) | `97b0c614be4d` (2026-04-20) |
| License | Apache-2.0 | MIT | Apache-2.0 | Apache-2.0 |
| Parameters | 335M (BERT-large) | 335M (BERT-large) | 568M (303M non-embedding; XLM-R) | 596M (decoder, 28 layers) |
| Dimensions | **1024 native** (MRL to 512 and lower) | **1024 native** | **1024 native** (MRL to 256) | **1024 native** (MRL 32-1024) |
| Max input | 512 tokens | 512 tokens | 8,192 tokens | 32k tokens |
| Pooling | CLS | CLS | CLS | last token (left padding) |
| Query/document | query prompt `Represent this sentence for searching relevant passages: `; documents none | same query instruction; documents none | query prefix `query: `; documents none | `Instruct: {task}\nQuery:{q}` on queries; documents none |
| Retrieval claim | MTEB retrieval avg 54.39 (15 sets) | MTEB retrieval 54.29 | BEIR(15) NDCG@10 55.6; multilingual | MTEB-eng v2 retrieval 61.83 |
| Official ONNX in the repo | yes: fp32 1.34 GB, fp16 669 MB, int8 337 MB | yes: fp32 1.34 GB | yes: fp32 2.27 GB (external data), int8 570 MB | **no** (PyTorch/safetensors; community exports only) |
| Tokenizer | BERT WordPiece, 711 KB | same | XLM-R SentencePiece, 17 MB | Qwen BPE, 11 MB |
| Languages | English | English | multilingual | multilingual |

Rejected without a closer look:

- 768-dimension models (EmbeddingGemma-300m, nomic-embed-text v1.5/v2,
  granite-embedding-english-r2). None fits `vector(1024)` without a schema change or an
  unsafe projection.
- `jina-embeddings-v3`: CC BY-NC, not for commercial use.
- `BAAI/bge-m3`: multilingual, 568M parameters, BEIR 48.8 on the Arctic card. Weaker
  for English retrieval than the candidates at a larger size.

## Runtime

| Option | Footprint | Apple Silicon | Linux server | Verdict |
| --- | --- | --- | --- | --- |
| **ONNX Runtime + `tokenizers` + NumPy** | ~35 MB of wheels (onnxruntime 1.30 20 MB, numpy 5-15 MB, tokenizers 3 MB) + protobuf, flatbuffers, huggingface-hub (a `tokenizers` requirement, never called) | CPU (arm64 NEON). The CoreML execution provider exists but is not used, to keep results reproducible | CPU wheels for x86-64 and arm64; no GPU needed | **Chosen** |
| PyTorch + transformers / sentence-transformers | torch alone is hundreds of MB installed; MPS on Mac | works | works; large images | Too heavy for one embedding call |
| Ollama / llama.cpp server | a separate model-serving daemon | works | another service to run and secure | Not justified for one model |
| MLX | small | Apple only | **no** | Excludes servers |
| fastembed | ONNX Runtime underneath, plus its own model catalogue and downloader (pillow, loguru, mmh3, ...) | works | works | Would add a second model catalogue and an implicit downloader |

The runtime is an **optional dependency group** (`uv sync --extra local`; `make setup`
and CI install it). The API does not import it unless a local provider is configured.

## Decision

**`mixedbread-ai/mxbai-embed-large-v1`, revision
`b33106f585b9ce46904ad7443a3b52b7a63e231c`, official fp32 ONNX (`onnx/model.onnx`),
run in-process with ONNX Runtime on the CPU**
(`lattice.providers.local_embedding.LocalEmbeddingProvider`). It records
`provider = local`, `model = mxbai-embed-large-v1@b33106f585b9`, `deployment = local`.

Why this one:

- It fits `vector(1024)` natively. There is no schema change and no projection, and
  switching providers is a re-embed (`lattice reindex --reembed`).
- It is Apache-2.0, with no commercial restriction.
- The same repository ships official ONNX weights. Lattice runs what the vendor
  published, needs no PyTorch export step, and pins file hashes for exact artifacts.
- It is the smallest serious option: 335M parameters, a 1.34 GB fp32 download, and a
  WordPiece tokenizer with a 711 KB `tokenizer.json`. That practicality matters on an
  8 GB M1 and on CPU-only servers.
- Its query/document semantics are simple and documented: an instruction on queries,
  nothing on documents. `LocalEmbeddingProvider` maps `purpose = "query"` to it.
- Its retrieval quality is in the same band as bge-large-en-v1.5, with a newer revision
  and quantized variants for later.

The 512-token limit is not a practical constraint for Lattice's chunks. The largest
embedding input in the corpus is 969 characters. Longer inputs are truncated, never
rejected, and the encoder counts truncations. Snowflake Arctic L v2.0 is the stronger
multilingual, long-context alternative. It costs 1.7× the parameters, a 2.27 GB
download and a 17 MB tokenizer, and it is the first model to try if embedding quality
turns out to be the limiting factor (see *Reconsider when*).

Qwen3-Embedding-0.6B posts the highest vendor retrieval score of the four. It was not
chosen because it has no official ONNX weights (it needs PyTorch + transformers, or an
unofficial conversion), it uses last-token pooling in a 28-layer decoder, which is
slower on a CPU, and it needs about 2.4 GB of fp32 weights in RAM.

## Weights, cache and offline operation

- Weights are **never** in the repository and **never** downloaded implicitly: not by
  `pytest`, CI, migrations, the API, `lattice sync` or `lattice eval`.
- `lattice models fetch mxbai-embed-large-v1` (`make models-fetch`) is the only path
  that downloads. It fetches exactly the pinned files (`onnx/model.onnx` 1,336,854,282
  bytes, `tokenizer.json` 711,396 bytes; 1.34 GB) from
  `huggingface.co/<repo>/resolve/<revision>/…` over HTTPS. Each file is written to a
  `.partial` name, checked against its pinned size and SHA-256, then renamed; a
  tampered or interrupted download never becomes a cache entry. Re-running skips
  verified files. `lattice models verify` re-hashes the cache.
- Cache: `LATTICE_MODEL_DIR` (default `~/.cache/lattice/models`), laid out as
  `<repository>/<revision>/<file>`. It can be shared between checkouts. Lattice never
  writes into it except through `fetch`.
- After the fetch everything is offline. The provider reads two local files and opens
  no socket (tested with sockets and HTTP clients made to raise).
- A missing or corrupt model is a clear `ConfigurationError`
  (`local_model_missing` / `local_model_corrupt` / `local_model_invalid` /
  `local_runtime_missing`) that names the fix. Lattice never falls back to another
  model.
- CI runs the real ONNX Runtime path on a committed 189 KB fixture model
  (`backend/tests/providers/fixtures/tiny-embedding`, built deterministically by
  `backend/scripts/build_tiny_embedding_model.py`), not on production weights.

## Resources (measured on the development machine)

Measured by the Phase 5 closure evaluation (evaluation-plan §9) on an Apple M1, 8 GB,
CPU execution provider, fp32:

- Model file: 1.34 GB on disk. Process memory while embedding: see evaluation-plan
  §9 (resident set measured during the full-corpus sync).
- Throughput: see evaluation-plan §9 (chunks per second for the full-corpus sync;
  query latency).

Servers: the same wheels run on x86-64 and arm64 Linux. Budget about 2 GB of RAM for
the model and activations per process that loads it (for example the sync worker, and
the API if it embeds queries locally). Throughput scales with CPU cores
(`LATTICE_LOCAL_EMBEDDING_THREADS`, default = the runtime's choice).

## Egress

`deployment = local`: inference never leaves the Lattice deployment. The egress policy
therefore does not cap it with the organization's *external* egress level. It caps it
with the provider's own approval (`model_providers.approved_for_level`), which, like
every provider record, starts at `internal`, and raising it is an explicit
administrator action (`lattice provider approve … --reason`). Local inference never
bypasses authorization. It only changes whether authorized content may be embedded or
a question may be embedded. See security-model §6 and ADR-011.

## Consequences

- Lattice has a real, zero-cost semantic provider for development, private and offline
  deployments, and for evaluation without any vendor account.
- Vectors from different models never mix. The model identity includes the revision,
  and semantic retrieval compares a query only with vectors of the same recorded model.
- The backend gains an optional ~35 MB native-runtime dependency group.

## Reconsider when

- The real-model evaluation misses Recall@10 ≥ 0.90 and the miss analysis attributes
  the misses to embedding quality (not lexical ranking, fusion or chunking). Then
  evaluate `snowflake-arctic-embed-l-v2.0` (1024 native, same runtime), then
  Qwen3-Embedding-0.6B if an official ONNX export appears.
- Multilingual corpora appear: Arctic L v2.0.
- Throughput on a server CPU is insufficient: the official int8 ONNX (337 MB) after
  measuring its recall, or a GPU execution provider.
- A new revision of the chosen model is published. It is a new pinned entry and a new
  embedding space (re-embed), never an in-place update.
