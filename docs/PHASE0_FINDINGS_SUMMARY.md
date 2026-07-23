# Phase-0 / Phase-0.5 Findings — Condensed

Full detail and raw numbers: `docs/phase0-measurements.md` (section numbers
referenced below). This doc is the short version — one line per decision,
no repeated reasoning.

## 0. Glossary

- **classifier** — Qwen3-1.7B, CPU-resident router that classifies each user turn's intent to decide downstream routing (needs the non-thinking-mode fix confirmed before use).
- **utility** — Qwen3-8B Q4, background/non-streaming model for titles, summaries, compaction, memory review; tolerant of slow/CPU latency since nothing waits on it synchronously.
- **embed** — nomic-embed (v1.5 GPU / v2-moe CPU), batched embedding model for retrieval/memory.
- **dispatcher / needle** — originally scoped as Cactus-Needle-26M, in practice Hammer2.1-1.5b (with FunctionGemma-270M as a credible secondary candidate): a small, single-shot (never the full agent loop) tool-call dispatcher that picks a tool + arguments.
- **chat-default** — Qwen3.6-35B-A3B (MoE), the always-warm general-chat model with native tool calling.
- **coder** — Qwen3-Coder-30B-A3B (MoE); coder-small is Qwen2.5-Coder-7B, a fast fallback/parallel coding lane.
- **reasoner** — DeepSeek-R1-Distill-Qwen-32B and/or Qwen3.6-35B-A3B-thinking (dual pick, no single winner), for deliberate multi-step reasoning with visible chain-of-thought.
- **vision** — Qwen3-VL-32B (+ mmproj), the vision-language model for image understanding.

**Clarifying note on `utility`+CPU:** the isolated Phase-0 benchmark found CPU `utility` "fails hard" (3.3 tok/s, worst-case synthetic 128-token forced generation), while the later Phase-0.5 concurrent test found it acceptable under real concurrent conditions with a real transcript (17.6-21.8s, a background job nobody waits on synchronously). Both are correct; the Config B verdict (CPU-resident `utility`+`embed`) rests on the concurrent number, not the isolated one — see `docs/phase0-measurements.md` §5/§8 for the raw numbers.

## 0.1 How the locked-roster candidates interact at runtime

```mermaid
flowchart TD
    U[User turn] --> C[classifier: Qwen3-1.7B, CPU]
    C -->|tool-call intent| D[dispatcher/needle: Hammer2.1-1.5b, single-shot]
    C -->|general chat| CD[chat-default: Qwen3.6-35B-A3B]
    C -->|coding task| CO[coder: Qwen3-Coder-30B-A3B]
    C -->|hard reasoning| R[reasoner: DeepSeek-R1-32B / Qwen3.6-thinking]
    C -->|image input| V[vision: Qwen3-VL-32B]

    D -->|selected tool + args| T[Tool execution]
    T --> CD

    CD -.async.-> UT[utility: Qwen3-8B, titles/summaries/compaction]
    CD -.async.-> EM[embed: nomic-embed, retrieval/memory]
    CO -.async.-> UT
    R -.async.-> UT
```

- `classifier` is the single entry point on every turn, always on CPU.
- `dispatcher`/`needle` only ever makes one single-shot tool-call decision — never a persistent agent loop; the tool result flows back into whichever main model is active for the actual response.
- `chat-default`/`coder`/`reasoner`/`vision` are mutually exclusive per-turn destinations (one big model active at a time on the GPU0 slot), not concurrent.
- `utility` and `embed` are drawn with dashed/async edges because they run in the background (titles, summaries, compaction, memory retrieval) and are never on a turn's synchronous critical path.

## 1. Locked roster

| Slot | Pick | Why (one line) | Detail |
|---|---|---|---|
| Vector store | Qdrant | sqlite-vec p95 105ms vs. 50ms bar | §6 |
| Coder quant | Q5_K_M | clears ≥100 tok/s bar, better quality than Q4, Q6 needs +3GB for marginal gain | §2, §9 |
| Reasoner | DeepSeek-R1-Distill-Qwen-32B **and** Qwen3.6-35B-A3B thinking — no single winner | both 7/7 correct on reasoning prompts at a proper token budget; B more reliable on debug-diagnosis (5/6 vs A's 3/6, A hallucinated 2 wrong fixes) | §9 |
| Vision | Qwen3-VL-32B | 6/6 correct vs Gemma-3-27b-it's 5/6 (missed a dot-count) | §9 |
| Classifier | Qwen3-1.7B-Q8_0 + `/no_think` suffix | thinking mode burned its budget with no suffix; fixed with a prompt-level fix, no template change | §2 |
| Embed | nomic-embed-text (v1.5 GPU / v2-moe CPU) | GPU-resident ~5x faster for ~70MB VRAM; CPU fine as fallback | §8 |
| Dispatcher | Hammer2.1-1.5b | 79.0% call_f1 prompt-tuned, 100% parse, 0.10s/call | §2 |
| Title generation | Hammer2.1-1.5b | 760x faster and more accurate than CPU-resident utility (which hits the same thinking-budget trap as reasoners) | §12 |
| `utility`+`embed` placement | CPU (Config B) | background-only framing holds: 18-22s real summary latency is fine async; GPU-resident would cost Hammer 5-7x dispatcher latency for ~370MB headroom | §5, §8 |
| Big-model GPU pinning | `CUDA_VISIBLE_DEVICES` solo-GPU0, not tensor-split | tensor-split-3,1 is ~3x slower and structurally OOMs `utility` on GPU1 | §8 |
| FunctionGemma-270M | Not adopted (secondary candidate only) | full-250 finetune: 88.3% call_f1 on a fresh holdout, beats Hammer's number, but higher per-call latency (0.29-0.34s vs 0.10s) — Hammer's track record wins for now | §10 |
| KV-cache quant | Low priority | Q8_0 only recovers ~250MB at 32k ctx for this MoE model (GQA keeps KV small) | §11 |
| Needle / Cactus | Dropped | see below | §2 |

## 2. Needle / Cactus — dropped

Needle 26M failed on both latency (~0.9-1.1s/call vs. a 50ms bar) and,
after finetuning, generalization (100% held-out score came from a
limited-template training set). Cactus's production runtime that would fix
the latency can't build on x86_64 at all (hard-locked to ARM NEON
intrinsics) — dropped in favor of Hammer2.1-1.5b and FunctionGemma-270M.

## 3. Open action items

- **Swap latency (§4, never scripted)** — needs `llama-swap` actually
  running with `serving/llama-swap/config.yaml` regenerated against
  current blob paths.
- **Stale `granite4:3b`/`granite4-3b-longctx` llama-swap entries** point at
  a missing blob (`sha256-6c02683...`) — not fixed, out of scope so far.
- **`settings.json`/`app/config.py` empty `ollama_model_names` entries** —
  confirmed live bug (~3s tax per LLM iteration on affected routes,
  compounds to 10s+ on multi-step tool calls). Root cause confirmed, fix
  not yet applied.
- **`llama-server.service` (systemd)** — was auto-respawning a leftover
  test server stealing ~10GB VRAM; stopped this session but not disabled —
  decide if it should be `disable`d at boot.

## 4. Test 7+ round — real results (this session, see `docs/phase0-measurements.md` §13 for full detail)

Run directly against real models on `ailab` (this session's environment IS
the GPU box — `hostname`/`nvidia-smi` confirm it, no ssh needed). Headline
findings, most of them new open items rather than closed ones:

- **Hammer title cleanup**: the deterministic fence/quote/punct strip is
  correct and unit-tests 12/12, but on live output it *lowered* the rubric
  pass rate (3/8 → 2/8) — fence tokens were accidentally padding titles into
  the 5-8 word range; the real title underneath is often too short. **New
  follow-up**: Hammer's title length itself needs prompt-tuning, separate
  from the formatting fix.
- **FunctionGemma-270M as a title generator**: fails outright (0/8 → 1/8) —
  it echoes the input instead of summarizing and leaks function-call
  special tokens. Confirmed dispatcher-only, not viable for titles.
- **Tool-registry stress test (13 overlapping-name tools, 82 examples)**:
  Hammer2.1-1.5b call_f1 **53.85%** (down ~25 points from the narrow 6-tool
  baseline of 76.3-79.0%) — confirms the flagged degradation risk.
  FunctionGemma-270M scored **0%** under the generic prompt harness, but
  that's a harness mismatch (it needs its own native chat template, like
  Hammer got in `eval_hammer_native.py`), not proof it can't do the task —
  flagged as a real follow-up.
- **Classifier broader accuracy (85 items, 6 real routing categories)**:
  **45.88% overall** — badly fails the ≥90% bar. It essentially never
  predicts `tool_call_needed` or `chit_chat` (0% each), collapsing almost
  everything into `chat`. The earlier "5/5 PASS" was on an easier 3-class
  scheme; **the classifier is not currently viable for the app's real
  routing categories** — a new, more serious blocker than the original
  thinking-mode issue (which is already fixed here via `/no_think`).
- **Embedding retrieval recall@k**: **96.7%/100%/100%** at k=1/5/10 —
  clears the proposed bar comfortably. First real accuracy number for
  `embed` (previously latency-only).
- **Structured JSON output (chat-default)**: 0% at a 300-token budget (same
  thinking-mode budget trap as reasoners), **80%** at 1200 tokens — still
  below the proposed 95%/90% bars, with nested/array schemas the specific
  failure mode.
- **Context-depth degradation (chat-default, 2k→32k)**: tok/s degrades
  **36%** by 32k (fails the proposed ≤30% bar), and the recall probe came
  back **empty** at every checkpoint past 2k at a 256-token budget — root
  cause confirmed as the same thinking-mode budget trap (a follow-up run at
  8k with 1024 tokens answered correctly). **Any context-depth or
  structured-output eval on a thinking-capable model needs ≥1024 tokens of
  budget or the result is meaningless**, not a genuine quality signal.
- **Dynamic util-load-on-demand**: still not run — blocked on regenerating
  the stale `serving/llama-swap/config.yaml` (references deleted blobs),
  which is a config-authoring task outside this round's eval scripts.
  `scripts/bench_swap_latency.py` is ready once that's done.

Still open, not yet designed:
- **A debug panel / dev-tool surface** for manually triggering and
  comparing dispatcher candidates against live traffic — functionality gap,
  not yet built.
- **Multi-slot concurrent-user throughput** (`--parallel N`) — no test yet
  covers 2-3 simultaneous users hitting one chat-default server.
- **Sustained-load thermal/throttle check** — all benches so far are short
  bursts; a 10-15 min continuous run would catch clock-throttling.
- **FunctionGemma-270M native-chat-template stress eval** — needed for a
  fair head-to-head with Hammer on the overlapping-tool-name registry.
- **Classifier fix for the real 6-category routing taxonomy** — the
  `/no_think` fix solved thinking-mode truncation but the broader accuracy
  problem (collapsing tool_call_needed/chit_chat into chat) is unsolved.

## 5. Harness (reusable, no changes needed)

`scripts/bench_models.sh`, `bench_server.py`, `bench_concurrent.py`,
`bench_needle.py`, `bench_sqlitevec.py`, `run_benchmark_suite.sh`,
`download_models.sh`, `eval_quality_transcripts.py`, `eval_coder_compile.py`,
`eval_title_gen.py`, `needle_training/*` — all working, all logs under
`logs/benchmarks/`.

## 6. Process/hygiene notes

- One GPU job at a time, except deliberate concurrency tests (§8).
- Pin big models to a single GPU via `CUDA_VISIBLE_DEVICES`, not a
  degenerate `--tensor-split` — the latter is ~3x slower (§8).
- Thinking-mode models need a real token budget (4096+) before judging
  quality — an under-budgeted eval measures truncation, not correctness (§9).
- `timeout <N> cmd`: capture `$?` immediately after the command, not inside
  an `if !cmd; then RC=$?; fi` block (real bug hit in `bench_models.sh`).
- No background agents idle-watching downloads/benchmarks — use direct
  bounded waits instead.
