# Agent Context Mega File — 2026-08-11 (read this first to save tokens)

Dense digest of everything learned auditing AI-Mega-App + llm-stack. Pair with `docs/FIX_PLAN_2026-08-11.md` (execution) and `docs/HANDOFF.md` (raw history). Facts below were code-verified 2026-08-11; prefer them over stale doc prose.

## Scripts and live investigation quick reference

The model/context investigations added reusable harnesses under `scripts/`. Use them before inventing a new
test, and record outputs under `logs/benchmarks/`:

```bash
# Isolated llama-server lifecycle, request, VRAM, and throughput.
python3 scripts/bench_server.py --label <label> --model <model.gguf> \
  --model-class <role> --ctx <tokens>

# Replay a growing conversation and probe recall at context checkpoints.
python3 scripts/bench_context_depth.py --label <label> --model <model.gguf> \
  --model-class <role> --ctx <tokens> --checkpoints 2000,8000,16000,32000

# Collect production-shaped prompt/response transcripts for manual review.
# The server must already be running; --system is essential for summarizer tests.
python3 scripts/eval_quality_transcripts.py --prompts <prompts.json> \
  --class <reasoner|coder|vision|summarizer> --model-label <label> --port <port> \
  [--model <llama-swap-alias>]

# Router eval against llama-swap (include /v1 or every request falls back).
python3 scripts/eval_router.py --base-url http://127.0.0.1:8080/v1
```

`bench_server.py` and `bench_context_depth.py` boot and tear down their own isolated server; use production
GPU pinning and no `--tensor-split`. `eval_quality_transcripts.py` appends unscored JSONL transcripts to
`logs/benchmarks/quality/<class>.jsonl`. For live incidents, inspect `data/app.db` tables `traces` and `spans`:
background title/summary jobs use separate traces linked through `chat_id`, so a supplied trace ID may not contain
the actual `summary` span. The Aug 23–24 summarizer/context findings and exact prompts are recorded in
`docs/HANDOFF.md` and `docs/current_bugs.md`.

## 1. Machine & hardware (live-verified)
- Box = `ailab`, Ubuntu; GPU0 = RTX 3090 24GB PCI `00000000:0D:00.0`; GPU1 = RTX 3070 8GB `00000000:0E:00.0`. Docs citing `03:00.0`/`07:00.0` are STALE (llm-stack/CLAUDE.md:79-81, ollama/CLAUDE.md:3).
- `CUDA_DEVICE_ORDER=PCI_BUS_ID` MUST accompany `CUDA_VISIBLE_DEVICES=N` (wrong-GPU incidents). Generated llama-swap config + swapgen.py OMIT it — top config bug.
- ollama binary exists (~/.local/bin/ollama) but Ollama is RETIRED on this box (llm-stack/CLAUDE.md): never invoke. ollama/ dir = historical 3090-stack artifacts (Modelfiles, benchmarks); its CLAUDE.md describes retired setup.

## 2. Services & deployment
- `llama-swap.service`: `--config /home/john/llm-stack/serving/llama-swap/config.yaml -watch-config`, :8080. `ai-mega-app.service`: uvicorn app.main:app :8000, `--timeout-graceful-shutdown 10`, `After=/Wants=llama-swap.service` (ordering OK, but Wants≠readiness — warmup must retry).
- Frontend served straight off disk (StaticFiles web/). Edit web/src/** → run REAL `npx tsc` → web/js/** (checked in, commit together). No restart needed for web changes; hard-refresh browser. Python changes need `sudo systemctl restart ai-mega-app` (sudo = explicit human approval each time, rule 008).
- Verification gate: `python -m pytest -q --basetemp=.pytest-tmp/run` + `npx tsc --noEmit`. Playwright: Chromium broken (libasound); use Firefox. `#/debug` SSE → use waitUntil:"load" + waitForTimeout, never networkidle.

## 3. Architecture quick map (AI-Mega-App)
- Backend: FastAPI; app/llm_client.py (only llama-swap client), app/chat/orchestrator.py (SSE turn loop), app/router/ (override→keyword rules→grammar classifier), app/gpu/ (swapgen generates llama-swap config; inventory), app/background/ (titles via dispatcher, summaries — redesigned 2026-08-15, see §4/§7 below: token-pressure trigger not turn count, utility-gpu GPU1 fast path first then utility CPU fallback), app/debug/ (trace/span store + SSE tap), app/warmup.py.
- Frontend: vanilla TS, one module per view (mount/unmount), hash router.ts, pub/sub store.ts. Views in web/src/views/*.
- Contracts: PLAN.md §4.2 = chat REST/SSE/span vocab (frozen; additions = owner decision). Spans flat snake_case. done/error terminal events. Route/usage/timings ride done payload.
- Config layering: config.yaml (checked-in) ← settings.local.yaml overlay (Settings UI, deep-merge) → swapgen → llama-swap config (generated, never hand-edit).

## 4. Roster/placement truth & config drift
Current as of 2026-08-24 (supersedes the Config B line below for GPU1): gpu0-main swap group = [chat-default, coder, coder-small, vision, reasoner, reasoner-alt] (one at a time on 3090); resident group = [dispatcher(GPU1), utility-gpu(GPU1, summarizer fast path, ~14x CPU decode measured live), utility(CPU, summarizer fallback), embed(CPU), classifier(CPU)]. `chat-default` is Qwen3.8-27B at 131,072 context with server-side reasoning off. `reasoner` is a distinct llama-swap entry pointing at the same Qwen GGUF through the symlink `Qwen3.8-27B-UD-Q4_K_XL-reasoner.gguf`, enabling `reasoning_effort: medium` and a 5,000-token reasoning budget; the separate filename is necessary because swapgen otherwise deduplicates same-file/same-GPU aliases. `reasoner-alt` is DeepSeek-R1 32B at 8,192 context. Isolated capacity sweep: coder-small native 32,768 and ships at 30,000 (112.3 decode tok/s at 32,162 prompt tokens); Qwen3.8's documented MTP flag set OOMs at 262,144, while 131,072 boots, so both chat-default and coder ship at validated 131,072. GPU1's utility-gpu is native 40,960 but uses ~7.8GiB at that allocation and therefore remains at 16,384 to coexist with dispatcher; coder-small must stay on GPU0, never GPU1. Original PLAN §4.1 Config B intent (before utility-gpu existed): gpu0-main = [chat-default, coder, coder-small, vision]; resident = [dispatcher(GPU1), utility, embed, classifier (CPU)]; dispatcher latency is critical path (5-7x degradation measured when GPU1 is shared with an *always-busy* model, phase0-measurements.md §8) — utility-gpu avoids that because it's idle between rare token-pressure-triggered summary runs, not concurrently busy like the rejected Phase-0 test.
Drift found 2026-08-11 (fix = WS-A): settings.local.yaml had coder-small gpu:1/resident:true (in swap:false resident group on 8GB 3070), ttl_s:0 on 5 non-residents, routing overlay dropped attachments map + code_task rules, classifier timeout_s 6 (cold load 1m32s → should be 90). settings.json legacy ollama aliases still wide-context tags.
**Drift recurred 2026-08-15**: settings.local.yaml again had coder-small on gpu:1 (this time resident:false) — moved back to gpu:0 as part of making room for utility-gpu resident on GPU1. If a future audit finds coder-small on gpu:1 a third time, treat it as a Settings UI bug (something keeps writing this), not a one-off — worth instrumenting.

## 5. Fixed defects (code-verified present in tree)
scroll stick-to-bottom; nav-interrupt (no abort on unmount, store.activeChatStreaming); clipboard execCommand fallback; stop button; model_loading banner; summary banner UI; reasoner alias→canonical swap name (orchestrator._canonical_swap_name); list_models queries /v1/models; inventory trace spam removed; shutdown drain 8s; router llm_client/config kwargs; error-path partial persist (orchestrator finally); warmup per-model 60s timeout + logging. Commits: 8c4f7b4, 53dac3a, 0170ca4, 96602e4, 52f5b31, e9cc8fc, ad84232, 492e260, e751a8b, 652c910, fac6c8e, 655f67d, e7a1a30.

## 6. Open defects (fix now; file:line)
- Warmup silent no-op risk: main.py:96 unstored task ref; _warmup_loop no try/except; llm_client only set by background.start (main.py:65-66, background/__init__.py:33-38) → classifier not hot at boot (user requirement: hot at service start).
- _on_turn_complete success-path only (orchestrator.py:456; error paths :481-491 skip titles/summaries).
- reasoning_content dropped: types.py ChatDelta lacks field; llm_client._parse_stream_chunk:255 reads content only.
- Web: no retry on error banner; response time/usage not inline in msg-meta; Debug has no thinking display; chat.ts:35-36 misleading comment.
- Config drift (§4). CUDA_DEVICE_ORDER missing (swapgen.py:136-141).
- Docs stale (§7).

## 7. Stale-doc inventory (fix = WS-D / orchestrator)
- AI-Mega-App AGENTS.md + CLAUDE.md "Current phase": say Phase 1 open/web unbuilt — reality: Phase 2 merged, 136+ tests, live app with fixes above.
- llm-stack/CLAUDE.md: PCI IDs stale; says dual-3070 (box is 3090+3070).
- ollama/CLAUDE.md: 3090 PCI id stale (07:00.0→0D:00.0); Modelfile↔tag naming: 10 unsuffixed files (5 = old wide tags kept for rollback, 5 = current defaults) — values correct, names misleading.
- benchmark_quality.sh: both queued fixes DONE (gemma4 in is_thinking_model; usage tokens in CSV/report). Remaining optional: separate reasoning from graded content.

## 8. Hard-won lessons (patterns)
- Test doubles with narrower signature than callee mask missing-arg bugs (router kwargs incident) — stubs accept **kwargs or mirror real signature.
- Background jobs calling new_trace(chat_id) outrank the live turn in most-recent trace order — Debug must not assume traces[0] = last turn.
- `#id` CSS beats `.class` regardless of order — views reusing #view need `#view.class` overrides.
- navigator.clipboard undefined on plain-HTTP LAN — always fallback.
- eval passing ≠ live wiring (93.33% eval while classifier never ran live) — verify live path, not just unit/eval.
- `--base-url` for eval_router must include /v1.
- asyncio.gather + hanging httpx call can swallow all logs — per-call timeout + entry/exit logging.
- llama-swap -watch-config reload kills ALL llama-server processes → warmup sweep after apply.

## 9. Deferred / explore later (user-directed)
- **MoE RAM offload for ctx headroom**: once all fixes live, explore `--n-cpu-moe` on chat-default (Qwen3.8-27B-UD-Q4_K_XL as of 2026-08-21, not the earlier Qwen3.6-35B-A3B) to increase ctx; precedent: qwen3-coder-30b stable with `-ncmoe 20` (llm-stack config; ~2.9GB/GPU headroom).
- benchmark re-run for FAIL_EMPTY validity (needs Ollama box). llama-swap eviction events in Debug. Modelfile renames.

## 10. Pointer hierarchy
PLAN.md (architecture why) → docs/FEATURES.md → docs/PHASE0_FINDINGS_SUMMARY.md → docs/PHASE_PROMPTS.md (delegation conventions) → docs/design-doc.md (UI truth: graphite/indigo #0e0f13/#6e6afd, IBM Plex Sans/Mono, compact, mono for system values) → docs/CURSOR_RULES.md. HANDOFF.md = bug history. This file + FIX_PLAN = 2026-08-11 snapshot.
