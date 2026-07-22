# Phase-0 Findings — Condensed for Plan/Prompt/Config Updates

Purpose-built for an agent that needs to update `PLAN.md`, `FEATURES.md`,
`docs/BENCHMARK_PLAN.md`, `settings.json`/`app/config.py`, and
`serving/llama-swap/config.yaml` — without re-reading the full session
history. Source docs, if more detail is needed: `docs/phase0-measurements.md`
(raw numbers/verdicts), `HANDOFF_phase0_benchmarks.md`,
`HANDOFF_tool_calling_latency.md`, `logs/benchmarks/TODO.md`.

All benchmark downloads finished and all queued jobs completed this session
(`auto_bench_watcher.log` ends `ALL PENDING BENCHMARKS COMPLETE`,
2026-07-22 ~02:46 UTC). Nothing is still downloading or pending.

## 1. Decisions that are final, with numbers behind them

| Decision | Answer | Evidence |
|---|---|---|
| §5 placement | **Config B** (solo 3090 for big models, 3070-resident small models) | `utility` (Qwen3-8B) on CPU: 3.3 tok/s, 42s for a 128-token summary — fails even the plan's "background only" bar |
| §6 vector store | **Qdrant**, not sqlite-vec | 100k-vector KNN p95 = 105ms vs. the 50ms interactive-fast bar |
| coder quant | **Q5_K_M** (not Q6_K, not Q4) | Q4 PASS (211 tok/s bench/136 real); **Q5_K_M PASS (197 tok/s bench/~140 real)**; **Q6_K FAILS to load — corrupted/incomplete file** (`llama_model_load: error loading model: tensor 'blk.41.ffn_up_exps.weight' data is not within the file bounds`), re-download needed if Q6 is ever wanted again, but Q5 already clears the plan's "≥100 tok/s" bar with better quality than Q4 — **adopt Q5_K_M** |
| reasoner A vs B | Both pass; **A (DeepSeek-R1-Distill-Qwen-32B) confirmed on re-fetch**: 38.45 tok/s bench, ~44 tok/s real-gen — matches original measurement exactly. B (Qwen3.6-35B-A3B thinking) still faster real-gen (126 vs 44 tok/s) via MoE efficiency. **Quality eval (not just speed) still needed before final pick** — not resolved this session |
| vision A vs B | **Both PASS correctness** (3/3 and 2/2 correct object-count on the same test image); **A (Qwen3-VL-32B) has the latency edge**: real-gen 21-43 tok/s vs B (gemma-3-27b-it)'s 11-27 tok/s. Root cause: gemma's mmproj tokenizes the test image into far more prompt tokens (276 vs 54 for Qwen3-VL) even though raw bench tok/s is similar (38.4 vs 43.0) — the extra image-token overhead costs gemma real-world latency. **Recommend adopting Qwen3-VL-32B** as vision, pending a broader accuracy test (only one image/prompt was tried, not a rigorous eval) |
| classifier candidate | **Qwen3-1.7B-Q8_0 on CPU has a thinking-mode problem** — burned its entire 32-token completion budget on internal reasoning (`"Okay, let's see. The user wants me to classify..."`) and **never produced an answer** (`output: ""`, `truncated_before_answer: true`), at only 14-15 tok/s CPU. This is the same "can't suppress thinking" failure flagged for Qwen3-4B in the dispatcher eval — **not usable as-is for a fast classifier role**; needs either a chat-template/`enable_thinking=false` fix or a different (non-thinking) small model before this slot is usable |
| embed-B | Qwen3.6/nomic-embed-text-v2-moe on CPU: **PASS**, 11-23ms/call — fine for the embed-CPU slot |
| Needle vs. small-Qwen dispatcher | **Small Qwen wins today**: Qwen2.5-3B 51.4% call_f1 @ 0.12s/call (best latency, ~7-9x faster than needle's own reference runtime) vs needle untuned 25.2% call_f1 @ ~0.9-1.1s/call. Qwen3-4B scored higher (63.4%) but its thinking mode can't be suppressed in llama.cpp, adding 150-300 reasoning tokens/call — **not viable as a low-latency dispatcher despite best raw accuracy**. **Not the final Phase-3 decision** — re-run both once the tool registry stabilizes, per plan |

## 2. Open items that need action (not just observation)

1. **`coder-small` naming bug in PLAN.md** (§4.1 roster table, also line ~140
   and ~114/118/145/160). PLAN.md names **"Qwen3-Coder-7B"** — this model
   **does not exist** (Qwen3-Coder only ships as 30B-A3B and a 3B-active
   "Next" MoE; confirmed via web search, nothing on unsloth's HF org).
   **Fix:** replace every `coder-small` / "Qwen3-Coder-7B" reference in
   PLAN.md with **`Qwen2.5-Coder-7B`** (already on disk at
   `gguf/qwen2.5-coder-7b.gguf`, already benchmarked: **PASS**, 124.8 tok/s
   bench / 114-121 tok/s real, only 6GB VRAM — fits the PC2 3070 budget
   resident, per PLAN.md's own §"PC2 budget" open question, now answered:
   **yes, it fits resident**, no swap entry needed).
2. **`settings.json` / `app/config.py` — empty `ollama_model_names` entries
   (confirmed live bug, causes the 10s+ tool-calling latency).** These keys
   are currently `""`:
   ```
   local/coding-light, local/coding-medium, local/coding-heavy,
   local/reasoning-medium, local/reasoning-heavy,
   local/vision-light, local/vision-medium, local/vision-heavy,
   local/tool-calling-medium
   ```
   Every request routed through one of these pays a ~3s tax **per LLM
   iteration** (two failed warmup retries against Ollama for a literal
   empty model name, confirmed via live log capture:
   `ensure_loaded_elapsed_ms=3044.9`, `model=` blank, 404 x2). This compounds
   on multi-step tool-calling turns (10s+ observed) vs. `general_chat`'s
   1-3s (its alias, `local/qwen3-8b` → `qwen3:8b-32k`, is populated
   correctly). **Fix:** populate each empty value with the real Ollama tag
   that `litellm_config.yaml`'s (or wherever the generated LiteLLM proxy
   config now lives — a `litellm_config.yaml` file was not found in this
   pass; check `app/litellm_sync.py`/`app/litellm_resolver.py` for wherever
   it's generated) `litellm_params.model` already resolves that alias to.
   Also fix the same default in `app/config.py` (~line 163) so a fresh
   install doesn't regenerate the bug. **No code was changed for this —
   still an open fix**, confirmed root cause only.
3. **Unexplained `Qwen3-32B-Q4_K_M.gguf` blob** (19.7GB, still present as of
   this session, mtime `2026-07-22 02:27:58 UTC`) — PLAN.md explicitly says
   not to re-download this model (strictly dominated by the 35B-A3B MoE)
   and `download_models.sh` has no fetch line for it. Never explained; still
   on disk taking ~20GB. Recommend deleting it to reclaim space unless
   someone confirms a reason it's there (currently 207G free of 492G, so not
   urgent, but it's dead weight against the plan's own skip-list).
4. **Reasoner A/B and Vision A/B final picks are not locked** — need a real
   quality eval (not just tok/s and a single-image smoke test) before
   PLAN.md's roster table is finalized. This session only measured
   speed + one correctness spot-check for each.
5. **§4 swap latency** — never scripted. Needs `llama-swap` actually running
   with `serving/llama-swap/config.yaml` regenerated to point at the
   re-fetched blob paths (several entries in the current config are stale —
   4 named GGUFs were deleted from disk without the config being updated).
   Do this only after the roster above is locked.
6. **`llama-server.service`** (systemd) was found auto-respawning a leftover
   qwen3-8b test server on :8080, stealing ~10GB VRAM. User ran
   `sudo systemctl stop llama-server.service` — **confirmed currently
   inactive/dead** as of this session. If it's `enabled` at boot, it could
   respawn after a reboot; worth deciding whether to `disable` it too if
   it's not supposed to run at all (not done this session — read-only
   check only).

## 3. Needle — final verdict for Phase-3 planning, external research folded in

Full detail in `docs/phase0-measurements.md`'s needle sections. Condensed:

- Needle 26M, untuned: 25.2% call_f1 on this app's real 6-tool registry,
  0/10 on `file_grep`/`file_read`/`web_search` (over half the registry).
- Rehearsal finetune (250 examples, ~41/tool — under Cactus's own ≥120/tool
  minimum): 42% call_f1, **inconclusive**, too few gradient steps to trust.
- Cactus's production C++/ONNX runtime (the actual optimized version, not
  needle's JAX reference server) **cannot be built on this hardware at
  all** — hard-locked to ARM NEON intrinsics in 13 of 16 kernel source
  files, no x86_64 path exists anywhere in the codebase. This box will
  never produce a latency number better than the ~0.9-1.1s JAX-reference
  figure without an ARM device or a from-scratch SIMD port.
- **External research (Perplexity) confirms this is normal for Needle's
  current maturity, not a sign of local misconfiguration**: no one has
  published a validated finetuning recipe beyond the README's "≥120
  examples/tool" minimum; independent HN testers hit the same failure
  classes on Cactus's own demo tools (argument-composition/chaining
  errors, hallucinated multi-tool-chain data, semantic misrouting); a
  second independent report confirms Needle's runtime is fragile off ARM
  (unexplained CPU errors even in a Linux container); Cactus's own LoRA
  finetuning-tooling PR is still open/unmerged.
- **Verdict for Phase-3 planning:** on today's numbers, small Qwen
  (Qwen2.5-3B) beats needle's measured accuracy by 2-2.5x at ~1/7th the
  latency. Not the final gate decision — PLAN.md §4.7's own condition
  ("re-run once the tool registry stabilizes, with a real ≥120
  examples/tool finetune — 720 total, not 250") is still the agreed path
  before locking in either needle or a small-Qwen dispatcher. **Action for
  whoever updates PLAN.md/FEATURES.md F9**: consider documenting the
  small-Qwen fallback explicitly (it currently isn't in the plan at all,
  just raised as an option) so the Phase-3 re-evaluation has a defined
  second candidate, not just "needle or bust."

## 4. Harness (reusable, all real/tested — no changes needed)

`scripts/bench_models.sh`, `bench_server.py`, `bench_needle.py`,
`bench_sqlitevec.py`, `run_benchmark_suite.sh`, `download_models.sh`,
`scripts/needle_training/*`, `scripts/auto_bench_watcher.sh` — all working,
all logs under `logs/benchmarks/`. No simulated numbers anywhere; every
figure above traces to a real JSONL/CSV file there.

## 5. Process/hygiene notes worth preserving in CLAUDE.md or team norms

- Don't run more than one GPU benchmark job at a time (two concurrent big
  loads OOMs the 3090+3070).
- `timeout <N> cmd` in bash: capture `$?` immediately after the command,
  not inside an `if ! cmd; then RC=$?; fi` block (real bug hit and fixed in
  `bench_models.sh` — was masking every failure as "OK").
- User explicitly asked to stop using background agents/monitors for
  idle-waiting on downloads — prefer direct `Bash` background waits
  (`run_in_background` / bounded `until` loops).
