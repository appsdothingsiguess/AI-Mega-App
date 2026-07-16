# Ollama (RTX 3090) — Model Recommendations & Concurrency Guide

Generated 2026-07-14 from `benchmark_quality.sh` (incl. new `vision` category) and
`benchmark_deepseek_reasoning.sh` results. See `CLAUDE.md` for full model table,
VRAM figures, and prior benchmark history.

## Best model per use case

### 1. Tool-calling / agentic
| Tier | Model | Why |
|---|---|---|
| Light | `granite3.3:8b-16k` | All 4 tool_calling cases PASS, fastest of the reliable tool-callers |
| Medium | `qwen3:8b-32k` | All cases PASS incl. distractors/multi-arg, best general-purpose assistant |
| Heavy | `qwen3-coder:30b-24k` | Tool-calling PASS + programming PASS in the same model — agentic coding loops. Take `-24k` over `-16k` for more ctx headroom on multi-turn tool chains |

⚠️ `qwen2.5-coder:7b-*` **failed 3/4 tool_calling cases** (single_call, distractors, multi_arg). Do not route tool-calling traffic to it.

### 2. JSON / structured output
| Tier | Model | Why |
|---|---|---|
| Light | `granite3.3:8b-16k` | All 4 json_schema cases PASS, cheapest/fastest |
| Medium | `qwen3:8b-32k` | All PASS, more robust to ambiguous prompts (thinking) |
| Heavy | `gemma4:26b-16k` | All PASS + vision — use when JSON must be extracted from an image |

### 3. Instruction-following / general chat
| Tier | Model | Why |
|---|---|---|
| Light | `granite3.3:8b-16k` | All PASS, fastest turnaround |
| Medium | `gemma4:12b-16k` | All PASS incl. negative constraints, good speed/quality balance |
| Heavy | `gemma4:26b-16k` | All PASS, stronger for nuanced/long-form instructions |

### 4. Programming
| Tier | Model | Why |
|---|---|---|
| Light | `qwen2.5-coder:7b-16k` | Fast dense coder (~50-190 tok/s) for isolated snippets/boilerplate — not for tool-calling |
| Medium | `qwen3-coder:30b-16k` | Tool-calling PASS + coding, routine fix/feature work needing tool orchestration |
| Heavy | `qwen3-coder:30b-24k` | Same MoE, larger ctx (24k) for big-file refactors / long agentic sessions |

### 5. Reasoning / planning / debugging (deepseek-r1)
| Tier | Model | Why |
|---|---|---|
| Light | `deepseek-r1:8b-32k` | Fast (~100-113 tok/s), passed both planning and debugging |
| Medium | *(same — no smaller reliable option)* | `deepseek-r1:8b-16k` **fails real debugging tasks**, burns entire 14.8k completion budget mid-reasoning, returns empty. Avoid for anything beyond trivial reasoning |
| Heavy | `deepseek-r1:32b-16k` | Correctly fixed all 4 planted bugs incl. the expert-only race condition; slower (~32-35 tok/s) but the only one confirmed reliable on hard multi-bug debugging |

Router intents `reasoning_medium` / `reasoning_heavy` map to `local/reasoning-*` tiers and may use tools `web_search`, `bash`, `pdf_gen`, `file_ops` (not `vision`).

### 6. Vision
| Tier | Model | Why |
|---|---|---|
| Light | `gemma4:12b-16k` | PASS color + count tests, fastest vision-capable model |
| Medium | `gemma4:26b-16k` | PASS both, stronger visual reasoning for less obvious images |
| Heavy | `gemma4:31b-12k` | PASS both, largest weights — but **smallest ctx (12k)** of the three despite being biggest, so it's a poor fit for image + long-document tasks |

### 7. Classification / routing (fixed, no tiers)
**Updated 2026-07-16:** the classifier moved off CPU. Production default (`app/config.py:376`, `settings.json`) is now `ollama/qwen2.5:3b` on GPU (`num_gpu: 999`, `num_ctx: 8192`), running the "mut12" prompt — 96.0-96.8% composite accuracy on the full 253-row eval, ~300-330ms median latency. This replaced `qwen2.5:1.5b-32k` (CPU-only, `num_gpu:0`, mut7 prompt, 86.6% composite, ~1.1s latency) because mut7/1.5b topped out below the 90%+ accuracy target. `qwen2.5:1.5b-32k` is still installed but no longer the routed classifier.

---

## Concurrency: what can run together on one 3090 (24GB VRAM)

`OLLAMA_MAX_LOADED_MODELS` is unset on this box, so Ollama keeps multiple models resident and evicts LRU only when VRAM runs out — the real limit is fitting inside 24GB, not a hard model-count cap.

### The classifier now competes for GPU headroom — concurrency figures below need re-verification

**Stale as of 2026-07-16:** this section assumed the CPU-only `qwen2.5:1.5b-32k` classifier (zero VRAM cost). Production has since moved to `qwen2.5:3b` on GPU (`num_gpu: 999`) — see section 7 above. That model **does** consume GPU VRAM and **does** compete with task models for headroom, so the "always-on, zero-VRAM-cost" framing below is no longer accurate and the concurrent-pairs table hasn't been re-measured against the new classifier. Treat the numbers below as pre-migration reference only until a fresh `gpu_sanity_check.sh`/VRAM pass is run with `qwen2.5:3b` loaded alongside each combo.

### Practical concurrent pairs (classifier + 2 task models)

| Combo | VRAM | Fits? | Use case |
|---|---|---|---|
| classifier + `granite3.3:8b-16k` (8GB) + `qwen2.5-coder:7b-16k` (5.8GB) | ~13.8GB | ✅ plenty of headroom | fast tool-calling + light coding |
| classifier + `qwen3:8b-32k` (8GB) + `qwen2.5-coder:7b-16k` (5.8GB) | ~13.8GB | ✅ | general assistant + light coding |
| classifier + `qwen3:8b-32k` (8GB) + `gemma4:12b-16k` (9GB) | ~17GB | ✅ | tool-calling/chat + vision, both live |
| classifier + `granite3.3:8b-16k` (8GB) + `gemma4:12b-16k` (9GB) | ~17GB | ✅ | fast tool fallback + vision |
| classifier + `deepseek-r1:8b-32k` (~7.7GB) + `granite3.3:8b-16k` (8GB) | ~15.7GB | ✅ | reasoning + tool-calling |
| classifier + `deepseek-r1:8b-32k` (~7.7GB) + `qwen2.5-coder:7b-16k` (5.8GB) | ~13.5GB | ✅ | reasoning + light coding |
| classifier + `gemma4:26b-16k` (17GB) + `qwen2.5-coder:7b-16k` (5.8GB) | ~22.8GB | ⚠️ tight — right at the edge, risk of OOM under load spikes |

### Models that essentially monopolize the GPU (classifier-only pairing)
- `qwen3-coder:30b-24k` (19GB) — only ~5GB left, not enough for a second reliable model.
- `gemma4:31b-12k` (21GB) and `deepseek-r1:32b-16k` (21GB) — same story, solo occupants.

These three should be treated as swap-in-swap-out heavy hitters, not always-on. Running two of them together will not fit in 24GB. The classifier still rides free with any of them.

### Recommended standing combo

**classifier (free) + `qwen3:8b-32k` (tool-calling/general) + `gemma4:12b-16k` (vision/instruction)** — ~17GB, leaves ~7GB slack for KV cache growth under concurrent requests, covers tool-calling, JSON, chat, and vision simultaneously without swap-induced latency.

For a coding-heavy always-warm setup, swap `gemma4:12b-16k` for `qwen2.5-coder:7b-16k` (~13.8GB total, more headroom) — but vision drops out until Ollama evicts something to make room.