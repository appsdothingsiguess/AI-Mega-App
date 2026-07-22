# Handoff: benchmark remaining/candidate models

**STATUS UPDATE (2026-07-22 ~03:20 UTC, this session):** items 3 and 4 done,
item 2 in progress. See `docs/phase0-measurements.md` §2/§3 and
`HANDOFF_phase0_benchmarks.md` for full results:
- Classifier (item 3): **fixed** — `/no_think` suffix suppresses thinking
  mode, now PASS.
- New candidates (item 4): **Hammer2.1-1.5b PASS zero-shot, new best
  dispatcher** (76.3% call_f1/100% parse/0.10s). **FunctionGemma-270M**
  scored 0% under this doc's originally-planned harness due to a
  prompt-format bug (fixed: 22.2% true zero-shot), then **100%** after a
  46s finetune on `scripts/needle_training/data.jsonl` — see caveats in
  phase0-measurements.md before treating that as final. New scripts:
  `scripts/needle_training/finetune_functiongemma.py`,
  `scripts/needle_training/eval_functiongemma.py`. Not yet GGUF-converted.
- Coder Q6_K (item 2): root-caused as genuine bit corruption (not
  truncation), deleted + full re-fetch in progress, re-bench pending.

Context: Phase-0 benchmarking is done (`docs/phase0-measurements.md`,
`HANDOFF_phase0_benchmarks.md`). Needle/Cactus verdict is settled (small
Qwen wins on our real 6-tool registry; see phase0-measurements.md). This
session's job is narrower: fill remaining gaps and try new candidates.

## What to test

1. **Check for anything still unbenchmarked in the current roster** — grep
   `docs/phase0-measurements.md` §2 table for any row without a real
   PASS/FAIL verdict (there may be a reasoner or vision quality re-eval
   still open — check §7 deliverable checklist for unchecked items).
2. **Coder-Q6_K re-download + re-bench** — the blob was corrupted last
   session (`tensor 'blk.41.ffn_up_exps.weight' data is not within the file
   bounds`). Re-fetch via `scripts/download_models.sh` and re-run
   `scripts/bench_models.sh` / `bench_server.py` on it to get a real
   verdict instead of "assume corrupt."
3. **Classifier fix-or-replace** — Qwen3-1.7B-Q8_0 failed (thinking mode
   burns the whole completion budget, never answers). Either (a) get
   `enable_thinking=false` actually working in the chat template/request
   and re-test, or (b) benchmark a non-thinking small model instead
   (Qwen2.5-1.5B-Instruct is the obvious non-thinking fallback candidate).
4. **New tool-calling dispatcher candidates from Perplexity research**
   (see `docs/phase0-measurements.md` "external research round 2"):
   - **FunctionGemma 270M** (Google) — has a published finetune recipe
     (Distil Labs knowledge distillation, 10-39% → 90-97%). Worth pulling
     the GGUF/checkpoint and running through the same
     `scripts/needle_training/eval_llm_tool_calling.py` harness used for
     the Qwen2.5-3B/Qwen3-4B head-to-head, against our real 6-tool
     registry (`scripts/needle_training/data.jsonl`).
   - **Hammer** (MadeAgents, 1.5B) — smallest Hammer variant, same harness,
     same 6-tool registry. Check if a GGUF exists or if it needs
     conversion first.
   - Both should be compared against the existing Qwen2.5-3B (51.4%
     call_f1) and Needle (25.2%) baselines already in the eval harness —
     same 60 held-out examples, same call_f1/exact_match metric.

## Harness (already built, reuse don't rebuild)

- `scripts/download_models.sh`, `scripts/bench_models.sh`,
  `scripts/bench_server.py`, `scripts/run_benchmark_suite.sh`
- `scripts/needle_training/eval_llm_tool_calling.py` — the tool-calling
  dispatcher eval, same metric as needle's own `eval.py`
- Results go in `docs/phase0-measurements.md`; raw logs in
  `logs/benchmarks/`

## Rules from last session (don't relearn these)

- One GPU benchmark job at a time — concurrent big loads OOM the
  3090+3070.
- `timeout <N> cmd`: capture `$?` immediately after the command, not
  inside an `if !cmd; then` block.
- Don't spawn background agents/monitors just to babysit a download —
  use direct `Bash run_in_background` or bounded `until` loops.
