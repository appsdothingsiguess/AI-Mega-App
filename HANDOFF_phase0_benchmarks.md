# Handoff: Phase-0 benchmarking (docs/BENCHMARK_PLAN.md)

**Date:** 2026-07-21, updated 2026-07-22 ~02:45 UTC (handing off to a new
chat here — this doc reflects the current real state). **Where this runs:**
directly on the GPU box (RTX 3090
+ RTX 3070, Ryzen 9, 64GB RAM) — this session is *on* that box, not SSH'd
into it (rule `008-remote-box` describes a separate control-machine setup
that doesn't apply here; ignore its SSH instructions).

## TL;DR for the new chat

Phase-0 benchmarking is **mostly done and real** (not simulated). Key
outputs to read first:
- `docs/phase0-measurements.md` — the actual results table + verdicts (start here)
- `logs/benchmarks/TODO.md` — granular progress tracker, what's done/pending
- `docs/BENCHMARK_PLAN.md` — the spec this all measures against (has a few
  corrections/notes added during this session, marked inline)

Two decisions are already made with real numbers behind them:
- **§5 placement: Config B** (solo 3090 for big models + 3070-resident
  small models), not Config A — CPU-hosted `utility` (Qwen3-8B) measured
  3.3 tok/s / 42s-per-summary, fails even the plan's relaxed "background
  only" bar.
- **§6 vector store: Qdrant**, not sqlite-vec — 100k-vector KNN p95 measured
  105ms vs. the ~50ms interactive-fast bar.

## What's still open (pick up here)

1. **Downloads still running** (as of 2026-07-22 ~02:45 UTC, 213G free on
   `/home/john/llm-stack/models`, 254G/492G used). **Done and PASS this
   session:** coder Q4/Q5/Q6, DeepSeek-R1-32B re-fetch, coder-small
   (Qwen2.5-Coder-7B, substituted for a nonexistent "Qwen3-Coder-7B" — see
   item 2b), vision A (Qwen3-VL-32B). **Still downloading/queued, in this
   order:** `gemma-3-27b-it` + mmproj (downloading now, ~13.5/~18GB),
   `Qwen3-1.7B-Q8_0` (classifier), `nomic-embed-text-v2-moe` (embed-B).
   **`scripts/auto_bench_watcher.sh` is running unattended in the
   background** (started this session — check `pgrep -af
   "auto_bench_watcher.sh$"`) and benches each file the moment it lands, one
   at a time, no GPU overlap. Check progress:
   ```bash
   tail -30 /tmp/auto_bench_watcher.log     # per-job RUN/START/HEALTHY/DONE
   pgrep -af "auto_bench_watcher.sh$"       # still alive?
   ps aux | grep wget                       # still downloading, which file?
   ls -la /home/john/llm-stack/models/blobs/*.gguf
   ```
   If the watcher died, just re-launch it — it's idempotent, `wait_for()`
   re-checks disk state each time: `cd /home/john/AI-Mega-App && nohup bash
   scripts/auto_bench_watcher.sh > /tmp/auto_bench_watcher_stdout.log 2>&1 &
   disown`. A persistent `Monitor` was also armed this session watching
   `/tmp/auto_bench_watcher.log` for RUN/DONE/FAIL lines — if you're
   resuming in the same Claude Code session it may still be live; if this is
   a fresh chat it won't be, re-arm one if you want push-style notifications
   instead of polling.
   **Open question, not investigated:** a `Qwen3-32B-Q4_K_M.gguf` blob
   (19.7GB) appeared in `models/blobs/` this session even though
   `BENCHMARK_PLAN.md` explicitly says not to re-download it (strictly
   dominated by the 35B-A3B MoE) and `download_models.sh` has no fetch line
   for it. Still unexplained — check `ls -la --time-style=full-iso
   models/blobs/Qwen3-32B-Q4_K_M.gguf` and whatever process's history might
   explain it before deciding whether to delete it to reclaim ~20GB.
2. **Coder Q4/Q5/Q6 adoption decision** — all three now measured and PASS
   (Q4: 211/136 tok/s bench/real; Q5: 197/~140; Q6: real-server 104-136,
   its `bench_models.sh`/llama-bench throughput step logged no OK/FAIL line
   this run, worth a quick standalone re-run to fill that cell). Per the
   plan ("adopt highest quant that clears ≥100 tok/s"), Q6_K is the
   strongest candidate on paper — needs the throughput number confirmed and
   a VRAM-headroom-at-64k check (§3) before calling it final.
2b. **`coder-small` gap found and closed.** PLAN.md's roster names
   "Qwen3-Coder-7B" for this slot — **that model doesn't exist** (Qwen3-Coder
   only ships as 30B-A3B and a 3B-active "Next" MoE, confirmed via web
   search, nothing on unsloth's HF org). Substituted the already-on-disk
   `Qwen2.5-Coder-7B` (`gguf/qwen2.5-coder-7b.gguf`) and benchmarked it:
   **PASS**, 124.8 tok/s bench / 114-121 tok/s real, only 6GB VRAM
   (4.15+1.87 split automatically, no `--tensor-split` needed). **PLAN.md's
   roster table needs a naming fix** (§4.1, the `coder-small` row) — not
   done yet, flagging for whoever locks the final roster.
3. **Vision A/B** (Qwen3-VL-32B vs Gemma-3-27b-it) — **A is done and PASSES**
   (38.4 tok/s bench, 21-43 tok/s real, 25.1GB VRAM, correctly answered a
   real "how many objects" image test 3/3). B (Gemma-3-27b-it) still
   downloading, watcher will bench it automatically (same test image:
   `/home/john/llm-stack/ollama/test_files/vision_count.png`, prompt "How
   many objects are in this image?"). Compare and pick one once B lands.
4. **§4 swap latency** — not scripted at all yet. Needs `llama-swap`
   actually routing between models; do this once the roster above is
   locked and `serving/llama-swap/config.yaml` is regenerated to point at
   the re-fetched blob files (see "Model mount" below — several paths in
   the current config are stale/missing).
5. **Needle dispatcher decision — ANSWERED this session, see next section.**

## Needle vs. a small Qwen model — ANSWERED (updated after this doc was first written)

**Head-to-head is done, with real numbers** — see `docs/phase0-measurements.md`
"Needle vs. small-Qwen dispatcher head-to-head." Short version: **both
Qwen2.5-3B and Qwen3-4B beat needle's measured call_f1 by 2-2.5x** on this
app's real 6-tool registry (Qwen2.5-3B: 51.4% call_f1 @ 0.12s/call — also
~7-9x faster than needle's own reference-runtime latency; Qwen3-4B: 63.4%
call_f1 but its "thinking" mode couldn't be suppressed in llama.cpp, adding
150-300 reasoning tokens/call, making it not viable as a low-latency
dispatcher despite best raw accuracy). This flips the original framing —
today's numbers favor a small Qwen model over needle. **Not the final
Phase-3 decision** (re-run both properly once the tool registry stabilizes,
per the plan), and it's an apples-to-oranges comparison (Qwen prompted
directly vs. needle's purpose-built single-shot arch / Cactus's wrapper).

**Also resolved: Cactus's production runtime (the actual optimized C++
engine, not needle's JAX reference server) cannot be built on this box at
all** — `cactus-kernels/CMakeLists.txt` hardcodes ARM NEON compile flags
with no x86_64 path, confirmed by reading the CMake source directly (13 of
16 kernel `.cpp` files `#include <arm_neon.h>` and call NEON intrinsics
directly — not fixable with a different `-march=` flag, would need a real
x86 SIMD port). So needle's real latency ceiling on this specific hardware
is permanently unmeasurable without an ARM device or that port — the
0.9-1.1s JAX-reference number is the only latency data this box will ever
produce for needle. Full trace in `docs/phase0-measurements.md`. **Cactus
was removed from disk** (`rm -rf engine/cactus`, reclaimed 6.9GB) — don't
re-clone it here.

**Follow-up queued, not run yet:** a Perplexity research prompt was written
and handed to the user (`docs/perplexity_needle_research_prompt.txt`) asking
about real-world Needle finetuning recipes/gotchas beyond Cactus's own docs,
schema-shape-specific accuracy gaps, whether anyone has a working x86
workaround for Cactus, and published Needle-vs-small-model comparisons. If
the user shares results from that research, fold them into
`docs/phase0-measurements.md`'s needle section and reconsider the "small
Qwen wins" verdict above if it changes the picture.

Original open-question text below, superseded by the above but kept for
context on what was uncertain when this doc was first written:

PLAN.md (§4.7, F9 in FEATURES.md) specs **Cactus Needle 26M** as the
tool-call dispatcher (CPU-resident, single-shot call emission for models
tagged `tool_call: weak`). What we actually measured this session:

- **Needle's accuracy on this app's real tool schemas is weak out of the
  box**: `call_f1=25.2%, exact_match=25%` on 60 held-out test examples
  across the 6 real tools from FEATURES.md F9 (`web_search`, `fetch_url`,
  `file_read`, `file_grep`, `run_code`, `memory_save`). It got **0/10
  correct on `file_grep`, `file_read`, and `web_search`** — more than half
  the registry. (It did fine — 15/15 — on a separate 3-tool *toy* smoke
  test with simpler single-argument schemas; that number is not
  representative of the real registry.)
- Ran a quick local finetune rehearsal (250 synthetic examples, GPU,
  ~40s) — improved live val Call F1 to 42%, but that's **not enough data
  to call it fixed**: needle's own tooling recommends ≥120 examples/tool
  (we had ~41/tool), and the shift didn't generalize to novel phrasings in
  a spot-check. Full writeup with the bugs found along the way (needle's
  CLI silently dying, checkpoint force-re-download breaking local
  finetunes, a checkpoint-identical-output red herring that turned out to
  be "too few gradient steps," not a real bug) is in
  `docs/phase0-measurements.md` under "needle — local finetune experiment."
- Latency (separate from accuracy): ~0.9s avg/call on CPU, far over the
  plan's <50ms bar, but that's needle's own *unoptimized reference JAX
  server*, not Cactus's production C++/ONNX runtime — not a fair
  comparison, flagged in the doc.

**What this means for the "should we use needle" decision, not yet
resolved:** the plan's own gate is "adopt only if it beats the untuned
baseline" (PLAN.md §4.7) — we don't have that verdict yet, because 250
examples is roughly a third of the recommended minimum. Two live options
once the tool registry stabilizes (end of Phase 3, per plan):
1. **Do the real finetune** (≥120 examples/tool, all 6+ real tools) and
   re-run this same eval harness (`scripts/needle_training/`) — if it
   clears a real bar (define one — e.g. beat some target exact-match%),
   keep needle as the dispatcher.
2. **Fall back to a small Qwen model** as the dispatcher instead of
   needle if the finetuned accuracy still isn't good enough. Nothing in
   the current plan documents this fallback explicitly — it's not yet a
   decision, just the option you raised. Candidates already partially in
   scope: `Qwen3-1.7B` (already the `classifier` candidate, Q8_0,
   downloading now) or `Qwen3-4B` (already on disk at
   `models/gguf/qwen3-4b.gguf`, used for early smoke tests, ~166 tok/s at
   Q4 measured). A 3B-class model would trade needle's near-zero VRAM/CPU
   footprint and single-shot design for real instruction-following at
   real latency cost (dozens to ~150+ tok/s range, not sub-second calls)
   — worth a head-to-head eval against the same 250 (or better, 120+/tool)
   examples before deciding. **This eval hasn't been run — do it before
   picking one over the other.**

## Model mount status (real, current)

- `/home/john/llm-stack/models/blobs/` had 4 named GGUFs that
  `serving/llama-swap/config.yaml` references but that had been deleted
  without the config being updated — re-fetching now (see downloads
  above). `llama-swap` would fail to boot those entries as-is; don't start
  it until the roster is locked and the config is regenerated.
- `needle` has no GGUF at all (custom architecture, not llama.cpp-loadable)
  — runs via its own JAX runtime instead, set up at
  `/home/john/llm-stack/engine/needle/` (own venv, both CPU and CUDA
  jaxlib installed; `serve.py` and `finetune.py` there are *our* direct
  launchers that bypass two upstream bugs in `needle.cli` — see comments
  in those files before touching them).
- `engine/cactus/` (cloned this session, `github.com/cactus-compute/cactus`)
  was **removed** (`rm -rf`, reclaimed 6.9GB) after confirming it's a dead
  end on this hardware — not just a missing `-march=` flag, 13 of its 16
  kernel source files directly `#include <arm_neon.h>` and call ARM NEON
  intrinsics in the function bodies, no x86_64 fallback exists anywhere in
  the codebase. Don't re-clone it here; full trace of what was tried and
  why it can't work in `docs/phase0-measurements.md` under "Cactus
  (production Needle runtime)". Needle stays on its own JAX reference
  runtime (`engine/needle/`) for this box — see below for the follow-up
  research task on getting more out of that runtime instead.
- There's a systemd service, `llama-server.service`, that was
  auto-respawning a leftover qwen3-8b test server on :8080 and stealing
  ~10GB of GPU memory — user ran `sudo systemctl stop llama-server.service`
  to fix it. If GPU-heavy jobs OOM again for no obvious reason, check
  `systemctl status llama-server.service` and `nvidia-smi
  --query-compute-apps=...` first.

## Harness (all real, tested, reusable)

- `scripts/bench_models.sh` — llama-bench throughput wrapper → JSON + CSV
  (`logs/benchmarks/throughput_summary.csv`)
- `scripts/bench_server.py` — real llama-server chat/embed requests →
  JSONL analytics (tokens, latency, VRAM, full output text) per model
- `scripts/bench_needle.py` — same JSONL schema, for needle's own runtime
- `scripts/bench_sqlitevec.py` — the §6 data-plane gate
- `scripts/run_benchmark_suite.sh` — orchestrates all of the above,
  ordered smallest→largest model, supports `ONLY=<class1,class2>` to run a
  subset (`needle embed classifier utility coder reasoner chat-default
  vision` are the valid class names)
- `scripts/download_models.sh` — idempotent (`wget -c`), re-run any time
  to pick up missing files
- `scripts/needle_training/` — the finetune rehearsal scripts
  (`gen_training_data.py`, `compare_checkpoints.py`,
  `memorization_check.py`, `eval_llm_tool_calling.py` — the small-Qwen
  dispatcher eval added this session, same 60-example held-out slice and
  call_f1/exact_match metric as needle's own `eval.py`)
- `scripts/auto_bench_watcher.sh` — added this session; watches
  `models/blobs/` for each still-pending download and runs its bench job
  automatically the moment it lands (coder-Q6_K → reasoner re-fetch →
  vision A → vision B → classifier → embed-B, matching
  `download_models.sh`'s own fetch order). Currently running in the
  background — see item 1 above. (coder-small was run manually, not
  through this watcher, since its source file was already on disk.)
- `docs/perplexity_needle_research_prompt.txt` — not a bench script, a
  research prompt handed to the user this session, see the needle section
  above.

All logs under `logs/benchmarks/` — nothing simulated, every number in
`docs/phase0-measurements.md` traces back to a real JSONL/CSV file there.

## Process hygiene notes (things that bit us this session)

- Don't run more than one benchmark job at a time on GPU — `bench_server.py`
  always tears its server down on exit, but two concurrent big-model loads
  will OOM the 3090+3070.
- `timeout <N> some_script.sh` in bash: capture `$?` **immediately** after
  the command, not inside an `if ! cmd; then RC=$?; fi` block — `$?` there
  reflects the `if` condition's own status, not the command's (real bug we
  hit and fixed in `bench_models.sh`).
- Background agents/monitors are expensive and were mostly idle-waiting on
  downloads this session — user explicitly asked to stop using them for
  this kind of babysitting; prefer direct `Bash` background waits
  (`run_in_background` / bounded `until` loops) over spawning subagents to
  watch a log file.
