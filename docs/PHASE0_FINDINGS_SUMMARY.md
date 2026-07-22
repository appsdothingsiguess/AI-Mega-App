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
| §5 placement | **Config B** (solo 3090 for big models, 3070-resident small models) — **confirmed under real concurrent load, Phase-0.5 Tests 1-3**: big model must be pinned solo to GPU0 via `CUDA_VISIBLE_DEVICES` (not the tensor-split-3,1 shape Phase-0 originally benched — that shape OOMs `utility` on the 3070 and is itself ~3x slower, see `docs/phase0-measurements.md` §8); `utility`+`embed` on CPU is the right call once framed as background-only work (18-22s real summary latency, not the original synthetic 42s) — keeping them off GPU1 avoids a 5-7x Hammer dispatcher slowdown from contention for only ~370MB of headroom | `utility` (Qwen3-8B) on CPU: 3.3 tok/s, 42s for a 128-token summary — fails even the plan's "background only" bar (superseded framing in §8) |
| §6 vector store | **Qdrant**, not sqlite-vec | 100k-vector KNN p95 = 105ms vs. the 50ms interactive-fast bar |
| coder quant | **Q5_K_M** (not Q6_K, not Q4) — **quality-confirmed, Phase-0.5 Test 4**: 9/9 compile-pass across every installed language toolchain, 6/6 correct debug-diagnosis (including catching a trick "impossible premise" prompt rather than hallucinating a plausible-sounding fix) | Q4 PASS (211 tok/s bench/136 real); **Q5_K_M PASS (197 tok/s bench/~140 real)**; **Q6_K FAILS to load — corrupted/incomplete file** (`llama_model_load: error loading model: tensor 'blk.41.ffn_up_exps.weight' data is not within the file bounds`), re-download needed if Q6 is ever wanted again, but Q5 already clears the plan's "≥100 tok/s" bar with better quality than Q4 — **adopt Q5_K_M** |
| reasoner A vs B | **LOCKED: A (DeepSeek-R1-Distill-Qwen-32B)** — Phase-0.5 Test 4 quality eval found Reasoner B (Qwen3.6-35B-A3B thinking mode) **fails to produce a final answer 5/7 times on multi-step reasoning prompts and 5/6 times on debug-diagnosis prompts** at a normal 1024-token budget — its thinking traces consistently blow the budget before reaching an answer (`truncated_before_answer`). Reasoner A finishes reliably (7/7 and 2/6 — the only class A also struggled on was debug-diagnosis, but still far ahead of B's 1/6). B remains faster real-gen (126 vs 44 tok/s) when it *does* finish, so it's not ruled out entirely, but needs 2048+ `max_tokens` budgeted if used at all | See `docs/phase0-measurements.md` §9 for full per-prompt breakdown |
| vision A vs B | **LOCKED: A (Qwen3-VL-32B)** — Phase-0.5 Test 4 broader accuracy eval (6 prompts: counting, spatial relation, OCR, chart-reading) found **A: 6/6 correct, B: 5/6** (B miscounted a 9-dot cluster as 10) — confirms the latency-based recommendation from Phase-0 and adds a real accuracy edge case B misses that A doesn't | See `docs/phase0-measurements.md` §9 |
| classifier candidate | **Qwen3-1.7B-Q8_0 on CPU has a thinking-mode problem — FAIL, confirmed on all 5 test calls** — burned its entire 32-token completion budget on internal reasoning (`"Okay, let's see. The user wants me to classify..."`) and **never produced an answer** (`output: ""`, `truncated_before_answer: true`), at only 14.9-15.3 tok/s CPU. This is the same "can't suppress thinking" failure flagged for Qwen3-4B in the dispatcher eval — **not usable as-is for a fast classifier role**; needs either a chat-template/`enable_thinking=false` fix or a different (non-thinking) small model before this slot is usable |
| embed-B | nomic-embed-text-v2-moe on CPU: **PASS**, 11.7-23.5ms/call — fine for the embed-CPU slot |
| Needle vs. small-Qwen dispatcher | **Small Qwen wins today**: Qwen2.5-3B 51.4% call_f1 @ 0.12s/call (best latency, ~7-9x faster than needle's own reference runtime) vs needle untuned 25.2% call_f1 @ ~0.9-1.1s/call. Qwen3-4B scored higher (63.4%) but its thinking mode can't be suppressed in llama.cpp, adding 150-300 reasoning tokens/call — **not viable as a low-latency dispatcher despite best raw accuracy**. **Not the final Phase-3 decision** — re-run both once the tool registry stabilizes, per plan |
| Needle vs. alternatives, research round 2 | External research (Perplexity, 2026-07-22) surfaced named tool-calling-model alternatives with real documentation Needle lacks: **FunctionGemma 270M** (Google) has a published finetune case study (10-39% base → 90-97% after Distil Labs knowledge-distillation from a 120B teacher); **Hammer** (MadeAgents, ICLR 2025 Spotlight) is peer-reviewed with a dedicated eval suite (HammerBench) for the ambiguous/overlapping-tool-name failures we hit. Two community benchmarks rank `Qwen3-0.6B`/`LFM2.5-1.2B`/`Qwen3.5-4B` well above Needle-class accuracy generally — **but** a direct Reddit head-to-head (Needle vs Qwen3-0.6B, 50 queries/5 tiers) found **Needle still wins on narrow, low-ambiguity, fixed-toolset dispatch** (both accuracy and 4.4x speed), consistent with our own findings that Needle's edge shrinks as schema complexity rises. **No change to the Phase-3 decision** — full detail in `docs/phase0-measurements.md` |

## 2. Open items that need action (not just observation)

1. **`coder-small` naming bug in PLAN.md — FIXED.** PLAN.md's §4.1 roster
   table named "Qwen3-Coder-7B", which **doesn't exist** (Qwen3-Coder only
   ships as 30B-A3B and a 3B-active "Next" MoE). Updated to
   **`Qwen2.5-Coder-7B`** (already on disk at `gguf/qwen2.5-coder-7b.gguf`,
   benchmarked: **PASS**, 124.8 tok/s bench / 113.8-120.8 tok/s real, 6.0GB
   VRAM, confirmed resident-fit on the PC2 3070, no swap entry needed).
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
3. **`Qwen3-32B-Q4_K_M.gguf` blob — DELETED this session (Phase-0.5 cleanup).**
   Was 19.7GB dead weight (PLAN.md explicitly marks it not-kept, strictly
   dominated by the 35B-A3B MoE); removed along with its `llama-swap`
   config entry. Also cleaned up ~58GB of unreferenced Ollama blobs from the
   pre-restart old-version-1 era (gemma/granite/deepseek-r1-8b/qwen2.5-1.5b
   variants not used by any kept model) — ~72GB reclaimed total, 206G→278G
   free. The `granite4:3b`/`granite4-3b-longctx` llama-swap entries still
   point at a missing blob (`sha256-6c02683...`) — flagged but not fixed,
   out of scope for this cleanup pass.
4. **Reasoner A/B and Vision A/B — RESOLVED, Phase-0.5 Test 4.** Reasoner A
   (DeepSeek-R1-Distill-Qwen-32B) and Vision A (Qwen3-VL-32B) are now
   **locked** (see §1 table above and `docs/phase0-measurements.md` §9 for
   the full Claude-judged transcripts). PLAN.md's roster table should be
   updated to reflect these as final, not "recommended."
4b. **FunctionGemma-270M — full-250 finetune + GGUF done, Phase-0.5 Test 5.**
   Real generalization number on a freshly-generated (non-overlapping)
   holdout: **88.3% call_f1, 100% parse rate** — beats Hammer2.1-1.5b's
   79.0% on the same metric, at ~1.8-2x the raw tok/s and 5-6x less VRAM.
   However per-call latency is still higher than Hammer (0.29-0.34s vs
   0.07-0.21s), likely fixed per-request overhead at this short completion
   length. **Not adopted as the primary dispatcher this round** — Hammer's
   proven track record and lower absolute latency keep it the safer
   primary pick, but FunctionGemma is now a credible cheap secondary
   candidate worth a real production-traffic side-by-side. Two real bugs
   were found and fixed getting here (see `docs/phase0-measurements.md`
   §10): a `DataParallel` auto-wrap OOM on the 3070 during training (fixed
   via `CUDA_VISIBLE_DEVICES=0`), and a vocab-size assertion in
   `convert_hf_to_gguf.py` caused by a pre-existing quirk in the upstream
   base checkpoint's tokenizer (fixed via `resize_token_embeddings`).
4c. **Context/KV-cache budget re-check — done, Phase-0.5 Test 6.** At 32k
   context under real Config B concurrent load, KV-cache quantization
   (Q8_0) only recovers ~250MB on this model (GQA keeps its KV cache small
   relative to its ~22GB dense weights) — a real but modest gain, not a
   game-changer. Headroom on GPU0 at 32k ctx concurrent with Hammer is
   ~2.6-2.9GB — comfortable but should be re-checked if any future
   coder/reasoner context target grows past 32k.
5. **§4 swap latency** — never scripted. Needs `llama-swap` actually running
   with `serving/llama-swap/config.yaml` regenerated to point at the
   re-fetched blob paths (several entries in the current config are stale —
   4 named GGUFs were deleted from disk without the config being updated;
   the dead `Qwen3-32B-Q4_K_M.gguf` entry was already removed this session,
   see item 3). Do this now that the roster above is locked.
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
