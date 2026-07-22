# Benchmark & Test Plan — Phase 0 Ground Truth

**Status:** Phase 0. **All model testing/benchmarking lives here and happens before any app code** (it is the ground truth the roster, router, and swapgen are written against). Runs on the box over `ssh ubuntu-ai` using the prebuilt binaries in `/home/john/llm-stack/engine/llama.cpp/build/bin/` (see rule `008-remote-box`). Results fold back into `PLAN.md` §4.1.

**Scope: ONE computer — RTX 3090 (24GB) + RTX 3070 (8GB), Ryzen 9, 64GB RAM.** The second box is future; ignore it here. Everything below is decided for this single machine.

---

## 0. The decision this document exists to make

Two things are unknown until measured, and they cascade into every later phase:

1. **The roster** — which exact model + quant fills each class (`chat-default`, `coder`, `reasoner`, `vision`, `utility`, `embed`, `classifier`, `needle`).
2. **The placement config** — how the 3090 + 3070 are used. On one box these are the only two coherent options, and they are mutually exclusive because a tensor-split big model consumes the 3070, leaving no room for GPU-resident small models:

| | **Config A — Split** | **Config B — Solo+residents** |
|---|---|---|
| Big models (`chat-default`/`coder`/`reasoner`/`vision`) | tensor-split `3,1` across 3090+3070 (~32GB pool) | 3090 only (~24GB) |
| 3070 | absorbed into the big-model split | holds `embed` + `utility` **resident** |
| `embed` / `utility` | on **CPU** (Ryzen 9) | on the **3070** (GPU) |
| `classifier` / `needle` | CPU | CPU |
| Enables | 35B-A3B at **Q4** + Q5/Q6 coder + long ctx | local always-warm small model; big models capped (35B-A3B only Q3 / tight Q4) |
| Costs | no always-warm GPU small model during a swap; embeddings/utility on CPU | smaller max model, no quant headroom |

**Config A is the working hypothesis** (the first benchmark pass already ran split: 35B-A3B Q4 @ 133 tok/s, coder @ 153, R1-distill @ 45 — all fit and all clear the floor). Config A is only correct **if CPU-hosted `embed` and `utility` are fast enough.** §5 measures exactly that; the answer picks A or B. Do not write swapgen (Phase 2) until this is decided.

**Speed floor (owner):** nothing an interactive path depends on runs under **~25 tok/s** at its working quant. Q4_K_M is the default; go higher only where VRAM headroom is measured, not assumed.

---

## 1. How to run (on the box)

```bash
ssh ubuntu-ai
BIN=/home/john/llm-stack/engine/llama.cpp/build/bin
MODELS=/home/john/llm-stack/models/blobs
```

- **Throughput:** `llama-bench` for clean prompt/gen tok/s per model+quant.
- **Real behavior + VRAM + swap:** `llama-server` (`--jinja`) + `curl` a real chat/embeddings/image request while watching `nvidia-smi`. VRAM must be read *with a realistic context allocated*, not just weights.
- **Tensor split:** big-model runs use `--tensor-split 3,1 -ngl 999`; CPU runs use `--device none -ngl 0`.
- Record every number into `docs/phase0-measurements.md` (template built by the `p0/measure` agent). Disk is finite (~363G free) — delete superseded GGUFs before pulling new ones.

The repeatable harness is `scripts/bench_models.sh` (wraps `llama-bench` → markdown table) and `scripts/bench_sqlitevec.py` — both produced by the `p0/measure` agent; this document is the *what and why*, those are the *how*.

---

## 2. Model test matrix (per class)

Q4_K_M unless noted. "Fits" = weights + KV cache for the target context, headroom for no-OOM, measured live.

| Class | Candidates to test | Target ctx | Pass bar |
|---|---|---|---|
| `chat-default` | Qwen3.6-35B-A3B (MoE) **Q4 and Q5** | 32k | ≥60 tok/s, fits split |
| `coder` | Qwen3-Coder-30B-A3B (MoE) **Q4, Q5_K_M, Q6_K** | 32k (+64k variant) | ≥100 tok/s → adopt highest quant that clears it |
| `reasoner` | DeepSeek-R1-Distill-Qwen-32B (dense) **vs** a Qwen3.6-thinking / QwQ-successor **MoE** | 32k | ≥25 tok/s; keep the better reasoner, prefer the faster if quality ties |
| `vision` | **Qwen3-VL-32B** and **Gemma3-27B**, each + mmproj | 16k | loads, answers an image prompt, ≥25 tok/s; keep one |
| `utility` | Qwen3-8B Q4 | 8k | see §5 (placement decides CPU vs GPU) |
| `embed` | nomic-embed-v2 (**vs** Qwen3-embedding) | — | see §5 (embed latency, not tok/s) |
| `classifier` | Qwen3-1.7B Q8 | 4k | CPU; classify latency < ~300ms/turn |
| `needle` | Cactus Needle 26M — own runtime, not llama.cpp | — | CPU; one-call emit < ~50ms |

**`needle` is the one exception to "only use llama.cpp":** [cactus-compute/needle](https://github.com/cactus-compute/needle) ships no GGUF — weights are a `.pkl` checkpoint (26,315,421 params, confirmed) on a custom "Simple Attention Network" architecture (`ZCRMSNorm`, gated-residual layers) with no llama.cpp op support. By explicit sign-off (2026-07-21): acceptable to run it under its own runtime (JAX, CPU-only) instead of llama.cpp, as long as it's exercised through an HTTP API like every other model here — not called in-process. `engine/needle/serve.py` (a thin wrapper around the upstream `needle.ui.server`, needed because the upstream `needle.cli` launcher silently dies in this environment — its stderr-fd log filter kills the process before it binds the port) exposes the same `/generate` endpoint the project's own playground UI uses. `scripts/bench_needle.py` benchmarks it with the same JSONL analytics schema as `bench_server.py`, with a JIT-warmup call excluded from timing (JAX compiles the forward pass on first call; skipping this the first time inflated every measurement by seconds and made the results look ~1000x slower than reality).

**Important:** this benchmarks the `needle` repo's own reference JAX "playground" server (a Python eval/training tool), not Cactus's optimized production runtime (`cactus benchmark`, C++/ONNX/CoreML, NPU-tuned, the ~500-3000 tok/s numbers in [cactus-compute/cactus](https://github.com/cactus-compute/cactus)'s README). Those are different code paths on different hardware (mobile NPU/GPU vs this box's CPU via an unoptimized reference implementation) — not comparable numbers. CPU, warmup-excluded result on this box: **avg 1.1s/call, p50 0.9s** (10 calls, function-calling prompts, 128 max_gen_len) — real, reproducible, but not representative of Cactus's actual production performance.

Per candidate record: **file + source URL + SHA, size on disk, VRAM at target ctx, prompt tok/s, gen tok/s, load time, and (big models) swap-in time.**

Skip / do not keep: Qwen3-32B dense (already measured 44 tok/s — strictly dominated by the 35B-A3B MoE at 133; **do not re-download it as a reasoner candidate either** — reasoner-B is the same Qwen3.6-35B-A3B weights fetched for `chat-default`, tested in thinking mode, since Qwen3.6 natively supports think/no-think switching); DeepSeek-Coder-V2-Lite (trailed by Qwen3-Coder); any 600B-class DeepSeek (V3.2/V4/R2 — not local, remote-provider only).

**Model-mount note (2026-07-21):** the four named GGUFs this plan and `serving/llama-swap/config.yaml` depend on (`Qwen3.6-35B-A3B`, `Qwen3-Coder-30B-A3B-Instruct`, `DeepSeek-R1-Distill-Qwen-32B`, `Qwen3-32B`) had been downloaded once but were later deleted from `models/blobs/` without the config being reconciled — `llama-swap` would have failed to boot those entries. Vision models were similarly "retired" (blobs removed, entries commented out) and never re-fetched. `scripts/download_models.sh` now re-fetches everything the test matrix above needs (skipping `Qwen3-32B` per the line above, and `needle` per the blocked note). See `docs/phase0-measurements.md` §0 for the full reconciliation record.

---

## 3. Context / KV-cache fit (the quiet OOM killer)

For every model you intend to keep, re-measure VRAM **with the KV cache actually allocated** at:

- **32k** (default working context)
- **64k** (long coding / RAG sessions)

A model that fits at 4k but OOMs at 32k is not a kept model. Record the largest context that fits per model+quant; that number becomes its `-c` in the config and its ctx-variant entries (e.g. coder 16k/24k/32k share one weights file, different llama-swap entries). This is where Config A's ~32GB pool earns its keep over Config B's 24GB.

---

## 4. Swap-latency test (big-model slot)

With `chat-default` loaded, request each of `coder` / `reasoner` / `vision` and measure wall-clock to first token (the swap). Expect 3–10s. Record each — this sizes the `model_loading` SSE UX and the warm-keep policy (re-warm `chat-default` after N idle minutes).

Also confirm: while the big slot is loaded, the CPU residents (`classifier`, `needle`, and in Config A `embed`/`utility`) answer **concurrently** — the resident group is never evicted by a swap.

---

## 5. THE placement decision — CPU-resident latency (do this first, it gates everything)

Config A only works if the residents that move to CPU stay fast enough. Measure on the Ryzen 9 with the big model occupying both GPUs:

1. **`embed` on CPU:** batch-embed 100 chunks (~512 tokens each) with nomic-embed-v2 via `llama-server --embeddings --device none`. Record p50/p95 latency per batch. Embeddings are batched and off the token-stream hot path, so a few hundred ms is fine. **Pass:** RAG ingest of a typical doc completes in seconds, not minutes.
2. **`utility` on CPU:** Qwen3-8B Q4 on CPU — measure gen tok/s for a title/summary (short output). **Reality:** expect well under the 25 floor. Decide: is CPU utility acceptable for *background only* (titles/summaries/compaction, which never block chat), given there is **no** always-warm GPU small model in Config A?
3. **Compare against Config B:** same `embed`/`utility` on the 3070. If CPU embed is fine but CPU utility is too slow to be useful even for background, that argues for Config B (and accepting the 24GB big-model cap / 35B-A3B at Q3).

**Output:** a one-line verdict — **Config A** (split + CPU residents) or **Config B** (solo + 3070 residents) — with the numbers that justify it. Everything downstream (swapgen device assignment, the roster's "Where" column, the warm-keep policy) is written to the winner.

---

## 6. Data-plane test — sqlite-vec at scale

Independent of GPUs but a Phase-0 gate (PLAN §3.1 escape hatch): `scripts/bench_sqlitevec.py` inserts 100k×768-dim vectors into a `vec0` table (WAL) and measures top-10 KNN p50/p95 and hybrid FTS5+vector latency. **Pass:** interactive-fast (<~50ms p95) at 100k. **Fail → Qdrant** returns behind the `VectorStore` interface. Record the verdict.

---

## 7. Deliverable & what each result decides

Fill `docs/phase0-measurements.md` with:

| Result | Decides |
|---|---|
| Per-model VRAM @ 32k/64k + tok/s | the roster (`PLAN.md` §4.1 table) and each model's `-c` |
| §5 CPU-resident verdict | **Config A vs B** → swapgen device map, warm-keep policy |
| Coder Q4/Q5/Q6 + chat Q4/Q5 | the adopted quant per big model |
| Reasoner A/B | dense R1-distill vs thinking-MoE — keep one |
| Vision A/B | Qwen3-VL-32B vs Gemma3-27B — keep one |
| Swap latencies | `model_loading` UX + warm policy timing |
| sqlite-vec verdict | sqlite-vec vs Qdrant |

**Phase 0 exits when:** every roster cell has a measured model, the placement config is chosen with numbers, the sqlite-vec verdict is recorded, and the llama-swap service is running on :8080 with the kept set. No app code before this is done.
