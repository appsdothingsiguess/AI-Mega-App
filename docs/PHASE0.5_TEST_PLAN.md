# Phase-0.5 Test Plan — Concurrency, CPU-Placement, Quality Eval, FunctionGemma GGUF

## Context

Phase-0 benchmarking (`docs/phase0-measurements.md`, `docs/PHASE0_FINDINGS_SUMMARY.md`,
`HANDOFF_phase0_benchmarks.md`) benchmarked every model **in isolation** — each
`bench_server.py`/`bench_models.sh` run boots its own `llama-server`, runs
requests, tears down. That answered "does this model clear its throughput
bar alone" but left real gaps the user wants closed before the roster locks:

1. **Concurrent-loading VRAM reality never tested.** Config B (solo 3090 for
   big models, 3070-resident small models) was decided from isolated
   single-model numbers, not from actually loading a big model + small
   model(s) side by side and watching real VRAM headroom/contention. Some
   bigger candidates (Q6_K coder, 32B-class reasoners) may need up to ~30GB
   VRAM alone, which changes what can coexist on the second GPU.
2. **The CPU-placement verdict (§5, "utility on CPU fails hard: 3.3 tok/s")
   was measured against a synthetic 128-token bench, not real usage** — and
   the framing has changed since: Hammer2.1-1.5b (0.10s/call, GPU) is now
   the fast tool-router candidate, so `utility` no longer needs to be fast —
   it only needs to be tolerable for background jobs (summaries, and
   possibly auto-generated chat titles) where nothing is waiting
   synchronously, "just like how Claude does it" per the user. That
   framing was never tested — only the raw tok/s number was.
3. **Reasoner A/B and Vision A/B still lack a real quality eval** — Phase-0
   only ran tok/s + a single correctness spot-check per model. PLAN.md's
   roster can't lock those slots without a real side-by-side quality read.
4. **Several other benchmarked models were never evaluated by Claude for
   programming/reasoning quality** beyond raw tok/s (coder Q4/Q5/Q6,
   coder-small, chat-default) — same gap as #3, broader scope.
5. **FunctionGemma-270M** is still HF/transformers-only — never converted to
   GGUF, never given a real llama.cpp tok/s/VRAM number, and its 100%
   held-out score was measured after training on the *same* 190/250-example
   pool whose last 60 lines are the fixed held-out set used by every script
   in `scripts/needle_training/` — a full-250-example finetune (matching
   Hammer's zero-shot data volume framing) needs a **freshly generated**
   held-out set (different seed/phrasing) to give a real generalization
   number, not a re-hash of the existing split.
6. Optimized token/context sizing (KV-cache budget vs the plan's context
   targets) was flagged as open in `docs/BENCHMARK_PLAN.md` but not
   re-checked against the models actually adopted this round.

None of this requires new app code — it's new benchmark runs and new eval
scripts layered on the existing harness (`scripts/bench_server.py`,
`scripts/bench_models.sh`, `scripts/needle_training/*`), all of which are
confirmed reusable (see Explore findings above). `serving/llama-swap/config.yaml`
lives outside this repo (`/home/john/llm-stack/serving/llama-swap/config.yaml`)
and is confirmed stale — out of scope for this round per the user's own
decision (concurrency tests use standalone `llama-server` instances, not
llama-swap, so the swap-latency gap (§4) stays deferred exactly as
Phase-0 left it).

## Execution model (agents, sequencing, logging)

- **Prep work (non-GPU, non-monitoring) is parallelized across subagents**:
  generating prompt sets/fixtures, writing new scripts, generating the fresh
  FunctionGemma held-out set, converting HF→GGUF (CPU-side conversion step,
  not a GPU load), drafting the multi-language coding-prompt list, deleting
  unused model blobs — each dispatched to its own agent, then **killed/
  collected once its prep artifact lands** (no agent left idle-watching a
  GPU job).
- **Actual GPU-bound test execution is strictly one job at a time**, run
  directly (not delegated), per the Phase-0 hygiene rule already learned
  the hard way (`docs/PHASE0_FINDINGS_SUMMARY.md` §5: concurrent GPU loads
  OOM the 3090+3070) — Tests 1-3 are the one deliberate exception where
  *controlled* concurrency (2-4 processes by design) is the thing being
  measured, but no two *test scenarios* run at once, and no background
  agent babysits a running bench job (matches the user's standing
  preference already in `HANDOFF_phase0_benchmarks.md` against
  background-agent idle-waiting).
- **Logging: every test writes structured JSONL**, not just a summary —
  full request/response text, per-step timestamps, `nvidia-smi` VRAM
  snapshots (before/after/during), exit codes, and stderr/stdout tails on
  failure. Reuse `bench_server.py`'s existing JSONL schema/logging code as
  a library so format stays consistent with Phase-0's logs
  (`logs/benchmarks/server/*.jsonl`), extended with a `concurrent_group_id`
  field for Tests 1-3 and a `quality_review` placeholder field for Test 4
  (filled in by the later Claude-judged pass, not left absent).

## Prep task — configure opencode against llama.cpp

Coding work will mostly route through `opencode` (already installed, but
running bare `opencode` currently doesn't open it — needs debugging as
part of this prep too, not just config-writing). One prep agent handles
both, in parallel with the other prep tasks above:

- **Debug why `opencode` doesn't launch**: check `which opencode` / install
  location, whether it's a shell alias/PATH issue, missing config causing a
  silent exit, or a permissions/binary issue — get it actually opening
  first.
- **Configure the llama.cpp provider** per `opencode.ai/docs/providers/#llamacpp`:
  add a `provider.llama.cpp` block to `~/.config/opencode/opencode.json`
  (or project-local `opencode.json`) using the `@ai-sdk/openai-compatible`
  npm provider, `baseURL: http://127.0.0.1:8080/v1` (llama.cpp's default
  OpenAI-compatible endpoint, no API key needed), with a `models` entry per
  adopted coder model (name + `limit.context`/`limit.output` matching each
  model's real context/output budget from the roster, e.g. Qwen3-Coder Q5_K_M).
- Once configured, this becomes the **actual interface for Test 4's coder
  compile/run + debug-prompt evals** where practical — i.e. drive the
  coding prompts through opencode against the local llama-server instead of
  (or in addition to) raw `bench_server.py` calls, so the eval matches the
  real usage path this app will actually route coding work through.

## Cleanup — remove unused model blobs

Before/alongside prep: delete GGUF blobs for models that are now confirmed
dead ends, to reclaim space and stop them showing up as noise in future
`ls`/`download_models.sh` runs:
- `Qwen3-32B-Q4_K_M.gguf` (19.7GB) — PLAN.md explicitly says not to use it
  (dominated by the 35B-A3B MoE), origin unexplained, safe to delete.
- Qwen3-1.7B-Q8_0 classifier's failed **no-fix-applied** artifacts, if any
  stale intermediate files exist from the pre-`/no_think` FAIL run (keep
  the model itself — it's the adopted classifier post-fix).
- Any corrupted Q6_K coder blob remnants if a stale partial re-download is
  still on disk alongside the now-good re-fetched file.
- Confirm via `ls -la` + `docs/phase0-measurements.md` cross-reference
  before deleting anything not explicitly named above — this is a
  destructive action on large files, so list candidates and get a quick
  go/no-go before `rm`, even though blobs are reproducible via
  `download_models.sh`.

## Test 1 — Concurrent VRAM/throughput: big model + embed only

**Goal:** confirm the second GPU (3070) can hold `embed` resident alongside
whichever big model (chat-default/coder/reasoner) is loaded on the 3090,
with real concurrent throughput, not just summed isolated VRAM numbers.

- Boot one big-model `llama-server` (start with the adopted defaults:
  Qwen3.6-35B-A3B chat-default Q4_K_M, then repeat for Qwen3-Coder-30B-A3B
  Q5_K_M and DeepSeek-R1-Distill-Qwen-32B Q4_K_M) via `bench_server.py`
  parameters already used in Phase-0 (`--tensor-split`, `-ngl 999 -sm
  tensor`), left resident.
- Concurrently start `nomic-embed-text v1.5` on CPU (as already proven fine
  at 12ms/call) — this is the "embed on CPU, big model on GPU" arm — and
  separately try `embed` GPU-resident on the 3070 if VRAM allows, to see if
  GPU-embed is worth it over CPU-embed now that the second GPU isn't
  fully claimed by `utility`.
- Fire concurrent real requests at both (a chat/coder/reasoner request +ba
  batch of embed calls) and record: per-model tok/s under contention vs.
  isolated Phase-0 number, actual `nvidia-smi` VRAM per process, whether
  either request queues/blocks the other.
- New script: `scripts/bench_concurrent.py` — thin orchestrator that boots
  N `llama-server`/embed processes per the existing `bench_server.py` CLI
  flags, fires concurrent request batches via threads, and logs a joined
  JSONL row per scenario (reuses `bench_server.py`'s request/logging code
  as a library import rather than duplicating it).

## Test 2 — Concurrent VRAM/throughput: big model + embed + utility

Same harness as Test 1, but add `utility` (Qwen3-8B) resident too —
GPU-resident on the 3070 alongside embed, since that's the configuration
that would let `utility` be fast when idle-tolerant background use doesn't
demand it, and CPU-resident as the Config-A-style fallback. Record:
- Whether 3070 VRAM (8GB) actually fits `embed` + `utility` together
  GPU-resident (Q4_K_M Qwen3-8B alone measured 6.6GB in Phase-0 §2 — tight
  with embed's ~0.4GB combined, worth confirming it doesn't OOM under
  concurrent load, not just static `nvidia-smi` at idle).
- Real tok/s for `utility` under contention (big model generating on 3090 +
  embed calls hitting 3070 at the same time) vs. Phase-0's isolated 111
  tok/s GPU number.

## Test 3 — Real-world CPU-resident `utility`+`embed`, background-only framing

Re-run the CPU-placement test, but reframed per the user's actual scenario:
Hammer2.1-1.5b (GPU-resident, fast) handles the router job; `utility` only
needs to serve **background, non-blocking** jobs — chat summaries, and
possibly auto-generated chat titles (flagged by the user as "not for sure"
— test it as a candidate, not a commitment).

- Boot: big model (GPU/3090) + Hammer2.1-1.5b (GPU/3070) + `utility` (CPU)
  + `embed` (CPU) all at once — the real Config-C shape the user described
  ("big and small model loaded, cpu models loaded, see how everything
  does").
- Real-world prompts, not the synthetic 128-token bench: 3-5 realistic
  chat transcripts (~500-2000 tokens) needing a one-paragraph summary, and
  a handful of short exchanges needing a 5-8 word title. Measure wall-clock
  per job. Since nothing is waiting on this synchronously, judge against a
  "background job" bar (e.g. "does it finish before the next few user
  turns would plausibly happen," not the interactive 25 tok/s floor) —
  confirm/refute whether the 42s number from Phase-0 actually matters in
  this usage pattern, or whether async delivery makes it fine regardless.
- Then flip `utility`+`embed` to GPU-resident on the 3070 (alongside
  Hammer) and re-measure the same jobs, to get the real speed delta between
  "CPU background, tolerate delay" and "GPU-resident, always fast" —
  the user explicitly asked for this comparison ("load cpu models on 3070
  and test speed differences").
- Bonus check: does Hammer2.1-1.5b work acceptably as a title-generator too
  (single model doing both routing and titles), as a cheaper alternative to
  a separate utility-on-titles path — quick 5-request spot check, not a
  full eval.

## Test 4 — Reasoner A/B and Vision A/B quality eval (Claude-judged)

Per the user's confirmed choice: Claude reviews real transcripts against a
rubric, matching how Phase-0 already evaluated other candidates.

- Build a small fixed prompt set per class:
  - **Reasoner** (5-8 prompts): multi-step word problems, a coding-logic
    puzzle, a "explain your reasoning" ambiguous question — run against
    both DeepSeek-R1-Distill-Qwen-32B (reasoner A) and Qwen3.6-35B-A3B
    thinking mode (reasoner B) at the same temp/settings used in Phase-0.
  - **Vision** (5-8 prompts/images beyond the single "count objects" image
    already used): object counting, spatial relationship ("what's to the
    left of X"), text-in-image reading, a chart/graph read — run against
    Qwen3-VL-32B and Gemma-3-27b-it.
  - **Coder — two-part eval**, run against Q4/Q5/Q6 coder and coder-small:
    1. **Compile/run check across languages**: a fixed list of ~10 prompts
       spanning different languages actually installed on this box (verify
       toolchains first — e.g. Python, JS/Node, Go, Rust, Bash, C — skip
       any language without a working compiler/interpreter present rather
       than assuming). Each prompt asks for a small self-contained
       program/function; harness actually compiles/runs the output and
       records pass/fail + compiler/runtime error text, not just "looks
       plausible."
    2. **Claude-judged code quality**: same or a separate prompt set,
       Claude reviews the generated code directly (style, correctness
       beyond "it ran," edge-case handling, idiomatic use of the language).
    - **Heavier-model debug prompts** (reasoner-class and big coder,
      specifically — not the compile-check set above): go beyond obvious
      syntax/logic bugs. Include prompts simulating real debugging
      scenarios: a missing/uninstalled library causing an import error
      (does the model correctly diagnose "install X" vs. hallucinating a
      code fix), a real-world class of bug (off-by-one in a loop boundary,
      a race condition description, a subtly wrong API usage that runs but
      produces wrong output, a stack trace from a dependency version
      mismatch) — judged by Claude on whether the diagnosis is actually
      correct, not just plausible-sounding.
- Scripts: `scripts/needle_training/` pattern reused —
  `scripts/eval_quality_transcripts.py` for reasoner/vision (fires each
  prompt at each candidate via `llama-server`, writes prompt+response pairs
  to `logs/benchmarks/quality/{reasoner,vision}.jsonl`), plus
  `scripts/eval_coder_compile.py` for the coder compile/run check (executes
  generated code in language-appropriate sandboxed subprocesses, records
  exit code + stdout/stderr per language, writes
  `logs/benchmarks/quality/coder_compile.jsonl`) and reuse of the same
  transcript-capture path for the debug-prompt set
  (`logs/benchmarks/quality/coder_debug.jsonl`).
- Then a **separate step, not scripted**: read each JSONL and score by hand
  (Claude, in a fresh review pass) against a short rubric — correctness,
  reasoning depth/coherence, instruction-following, hallucination — and
  write the verdict into `docs/phase0-measurements.md` §2 as the missing
  "quality" column, finally locking reasoner A vs B, vision A vs B, and the
  coder quant/model pick on quality (not just tok/s).

## Test 5 — FunctionGemma-270M: full-data finetune + GGUF conversion

- Generate a **fresh held-out set**: re-run `scripts/needle_training/gen_training_data.py`
  with a different seed / broadened phrasing templates to produce a new
  test-only batch (not reusing `data.jsonl`'s existing last-60 slice, since
  that's about to become training data). Keep the new set the same size
  class (~60) for comparability with prior numbers.
- Finetune `unsloth/functiongemma-270m-it` on the **full 250 examples**
  (all of `data.jsonl`, not the 190/60 split) via
  `scripts/needle_training/finetune_functiongemma.py`, same hyperparams
  (6 epochs, lr 1e-4) unless the larger batch needs adjustment — flag if
  training time changes meaningfully now that there's no held-out carve-out
  needed internally.
- Eval on the freshly-generated held-out set via
  `scripts/needle_training/eval_functiongemma.py`, get a real generalization
  call_f1/exact_match/parse-rate number (not the old same-pool 100%).
- **Convert to GGUF**: use `/home/john/llm-stack/engine/llama.cpp/convert_hf_to_gguf.py`
  against `logs/benchmarks/functiongemma-finetuned/final/`, quantize (try
  Q8_0 first, matching the zero-shot GGUF quant already used for the
  wrong-prompt-format baseline run, plus Q4_K_M for comparison).
- Bench both quants via the existing `bench_models.sh` (throughput) +
  `bench_server.py` (real request latency/VRAM) — first real llama.cpp
  numbers for this candidate, needed to compare head-to-head against
  Hammer2.1-1.5b's already-known 0.10s/call, 100% parse rate on equal
  footing (both as real GGUF servers, not one HF/transformers + one GGUF).

## Test 6 — Context/token budget re-check

Re-verify KV-cache/context fit for whatever roster this round locks in
(chat-default, coder Q5_K_M, reasoner pick, vision pick, Hammer, utility,
embed) at the plan's target context sizes (32k for big models per the
existing pass-bar column) under the **concurrent** loading scenarios from
Tests 1-3, not isolated — concurrent KV-cache allocations are the case
that could blow the VRAM budget that isolated single-model tests can't
catch. Reuses `bench_server.py --ctx` already-supported flag; no new
script needed, just new invocations layered onto Tests 1-3's harness runs.

## Deliverables

- New scripts: `scripts/bench_concurrent.py`, `scripts/eval_quality_transcripts.py`
  (both thin wrappers reusing `bench_server.py`'s request/logging internals
  as a library, not copy-pasted).
- New data artifact: a freshly-generated FunctionGemma held-out set (fixed
  filename, e.g. `scripts/needle_training/data_holdout_v2.jsonl`) kept
  separate from `data.jsonl` so the original 60-line split stays available
  for any future re-comparison.
- Updated `docs/phase0-measurements.md`: new `## 8. Concurrent-loading
  tests` and `## 9. Quality eval (reasoner/vision/coder)` sections, plus a
  new FunctionGemma GGUF row in the existing §2 test-matrix table.
- Updated `docs/PHASE0_FINDINGS_SUMMARY.md` §1/§2 once verdicts land (locks
  reasoner A/B, vision A/B, and the CPU-vs-GPU utility placement call).
- Swap-latency (§4) and the llama-swap config regeneration stay explicitly
  out of scope this round, unchanged from Phase-0's own deferral.

## Verification

- Every new script run produces a real JSONL/CSV under `logs/benchmarks/`
  (no simulated numbers), following the existing convention.
- Run one GPU job at a time within each test where isolation still matters
  (Test 4/5 model boots), but Tests 1-3 are explicitly about *concurrent*
  loads — confirm via `nvidia-smi` during each run that VRAM usage matches
  expectations and no OOM occurs.
- Cross-check `logs/benchmarks/throughput_summary.csv` and the new JSONL
  logs against the numbers written into `docs/phase0-measurements.md`
  before calling any test's verdict final.
