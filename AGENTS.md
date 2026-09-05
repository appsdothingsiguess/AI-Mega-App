# For Claude and OpenAI models:

## Startup context

At the beginning of every task in this repository:

1. Read `PLAN.md`.
2. Read `docs/AGENT_CONTEXT_MEGA.md`.
3. Read the most recent relevant handoff in `docs/HANDOFF.md`.
4. Treat these as authoritative unless the user says otherwise.
5. Do not read archived handoffs unless the task requires historical context.

# AI Mega App — Agent Entry Point

Personal AI platform: a claude.ai-parity web UI backed by local models on a dedicated Ubuntu GPU box. FastAPI backend orchestrates chat, routing, tools, RAG, and hermes-style memory; llama.cpp `llama-server` instances managed by llama-swap do all inference through one OpenAI-compatible endpoint. The old Ollama/LiteLLM/React codebase in this repo is a post-mortem, not a foundation — build from `PLAN.md`, not from existing `app/` or `web/` code.

**Pi.dev pivot (2026-08-31, status updated 2026-09-05 — see `docs/HANDOFF.md` and `docs/AGENT_CONTEXT_MEGA.md`):** Pi (`earendil-works/pi`) is now the preferred coding-agent/chat-loop platform; do not rebuild that loop in this app's `web/`/`app/` layers unless a concrete gap requires it. This repo's job narrows to the inference/ops layer — llama-swap, model roster, benchmarking, and the Windows-to-Ubuntu relay. Not yet fully validated: a second relay supporting this pivot exists on `ailab` but is untested and not committed here; treat it as not present until it lands in git.

## Operational scripts — read before benchmarking or tracing

The recent model/context work is captured in `scripts/`; do not recreate ad-hoc benchmark harnesses.
Run these from the repository root, preferably on `ailab` with the GPU state recorded first:

```bash
# Isolated model boot + request + VRAM/throughput test; always tears down llama-server.
python3 scripts/bench_server.py --label <label> --model <model.gguf> \
  --model-class <chat-default|coder|coder-small|reasoner|vision|utility> --ctx <tokens>

# Growing real conversation; measures recall, latency, and the usable context ceiling.
python3 scripts/bench_context_depth.py --label <label> --model <model.gguf> \
  --model-class <role> --ctx <tokens> --checkpoints 2000,8000,16000,32000

# Collects prompt/response transcripts for manual quality review. The llama-server
# must already be running; include --system for production summarizer prompts.
python3 scripts/eval_quality_transcripts.py --prompts <prompts.json> \
  --class <reasoner|coder|vision|summarizer> --model-label <label> --port <port> \
  [--model <llama-swap-alias>]

# Router accuracy; base URL must include /v1.
python3 scripts/eval_router.py --base-url http://127.0.0.1:8080/v1

# Agent-driven MTP sweep: loads the full base profile once and varies only
# the requested dimensions; each variant is isolated, torn down, logged, and ranked.
CUDA_VISIBLE_DEVICES=0 python3 scripts/bench_sweep.py --profile qwen38-mtp \
  --matrix ub=256,512,1024,2048 --matrix b=2048,4096
# Environment variables can be swept with --matrix-env.
CUDA_VISIBLE_DEVICES=0 python3 scripts/bench_sweep.py --profile qwen38-mtp \
  --matrix flash-attn=on --matrix-env GGML_CUDA_GRAPH_OPT=0,1
```

`scripts/benchmark_profiles/qwen38-mtp.json` is the checked-in base for the
Qwen3.8 separate-MTP profile (90K context, official MTP sidecar, n-max 4,
q8 KV, batch 2048, ubatch 256, Flash Attention on, CUDA graphs on). Use
`bench_sweep.py` for automated comparisons; do not repeat the full base
configuration for a one-variable test. Sweep summaries are written beside
the per-run JSONL files under `logs/benchmarks/server/`.

`bench_server.py` and `bench_context_depth.py` own their isolated server lifecycle and must not be run with
`--tensor-split` for the production roster. `eval_quality_transcripts.py` appends raw results to
`logs/benchmarks/quality/<class>.jsonl`; it does not score quality. For a live production trace, query
`data/app.db`'s `traces` and `spans` tables by `trace_id`; background title/summary jobs have separate traces
linked by `chat_id`, so the requested chat's neighboring traces may contain the actual `summary` span.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, httpx, uvicorn (async, SSE-native) |
| Inference | llama.cpp `llama-server` behind llama-swap (`:8080`, OpenAI-compatible; group `resident` = classifier/utility/embed on CPU + dispatcher/utility-gpu on GPU1 (summarizer fast path, added 2026-08-15 — GPU1 has room for exactly these two, not a third model like coder-small), group `gpu0-main` = one big model at a time on the 3090, incl. coder-small) |
| Frontend | TypeScript compiled by plain `tsc` → native ES modules; no React, no bundler, no framework |
| Storage | SQLite (WAL) + FTS5 for text/chats/memories/traces + **Qdrant** for vectors, behind `VectorStore` |
| Projects | Filesystem-first (`projects/<id>/instructions.md`, `docs/`) |
| Coding agent | opencode serve (delegated, never nested in the chat tool loop) |
| Browser | BrowserOS via MCP client (host machine, escalation path only) |

## Live services, relays, and mutually exclusive modes

The normal production path is:

```text
browser/LAN client -> ai-mega-app :8000 -> llama-swap :8080 -> llama-server
Windows/Pi Harness -> pi-capture-relay :8081 -> 127.0.0.1:8080
```

`ailab` is the Ubuntu GPU box (`192.168.0.89` on the LAN). GPU0 is the RTX
3090 (24 GiB) and GPU1 is the RTX 3070 (8 GiB). The `gpu0-main` swap group
contains the large models and loads one at a time. `resident` keeps the
dispatcher, classifier, embed, utility, and `utility-gpu` available; the
3070 has room for the dispatcher and `utility-gpu`, not `coder-small`.

There is a separate, mutually exclusive Qwen3.6 worker mode for Harness
testing:

```text
qwen36-ngram.service :5807 -> Qwen3.6-35B-A3B-UD-Q4_K_M, GPU1, 32K
pi-qwen36-relay.service :8082 -> 127.0.0.1:5807
Windows Harness -> http://192.168.0.89:8082/v1
```

In this mode, `llama-swap.service` is stopped so its GPU1 residents do not
consume the worker's VRAM, and the normal app is offline. Do not restart
llama-swap, apply GPU config, or add Qwen3.6 to normal startup while this
test is active. The 8082 relay is text-only and accepts only client
`192.168.0.246`; the 8081 relay is the production/Qwen3.8 route and has the
same client restriction. Both relays forward `GET /v1/models` for Harness
discovery and capture POST chat-completion traffic. Use the relay URLs from
Windows; do not point a Windows client at `127.0.0.1` or the worker's direct
`:5807` port. Both relays retain prompt-bearing captures under
`/tmp/pi-request-captures/` or `/tmp/pi-qwen36-captures/`; treat them as
sensitive and do not delete them during an active investigation.

Check the relay units with:

```bash
systemctl --user status pi-capture-relay.service
systemctl --user status pi-qwen36-relay.service
```

The relay units are independent enabled user services (`default.target`), not
a shared target: both processes may be running, but a relay answers Harness
only while its upstream is active. `:8081` is unavailable whenever
`llama-swap :8080` is stopped; `:8082` is unavailable whenever the worker
`:5807` is stopped.

Unit templates and the isolated worker command are in `ops/`. To run the
manual Qwen3.6 test mode, stop the production services, then start the worker:

```bash
sudo systemctl stop ai-mega-app.service llama-swap.service
systemctl --user start pi-qwen36-relay.service qwen36-ngram.service
```

`qwen36-ngram.service` runs `scripts/warmup_openai_server.py` as
`ExecStartPost`, so a successful start is already warmed. Restart that service
to perform another warmup. To return to production, stop the worker first,
then start `llama-swap.service` and `ai-mega-app.service`. Do not use
`scripts/load_model_check.py` for Qwen3.6: its selector intentionally lists
only the production `config.yaml` roster.

Qwen3.6's measured plain decode is about 12–13 tok/s; n-gram speculation
reaches about 50–53 tok/s only on warm, predictable repeated code. Treat it
as an isolated experimental alias, not a production roster decision.

When switching modes, first record state with the appropriate service/model
check, then stop the active mode and verify the GPU is clear before starting
the other. The production config and generated llama-swap file were not
changed by the 2026-08-30 Qwen3.6 experiment.

## Pointer hierarchy (read in this order)

1. `PLAN.md` — architecture source of truth. Adhere to it; flag conflicts, never improvise around it.
2. `docs/FEATURES.md` — per-feature specs (interfaces, config keys, debug spans, toggles).
3. `docs/PHASE0_FINDINGS_SUMMARY.md` — locked roster + every Phase-0 decision, one line each (raw numbers: `docs/phase0-measurements.md`; the plan behind them: `docs/BENCHMARK_PLAN.md`). Both phases closed.
4. `docs/PHASE_PROMPTS.md` — task prompts per phase (orchestrator → delegated sub-agents in worktrees).
5. `docs/design-doc.md` — visual/UI source of truth (palette, screens, component states). Binding on any `web/**` work via `.cursor/rules/011-ui-design.mdc`; never re-derive look and feel from `PLAN.md`/`FEATURES.md`.
6. `docs/CURSOR_RULES.md` — the full `.cursor/rules/` ruleset (001–011), hooks, and `.cursorignore`. **`010-benchmark-eval-methodology` is mandatory reading before writing or trusting any eval** — every rule in it traces to a specific wrong number that got published in Phase 0.

## Frozen contracts (once they exist)

Interface files are the real constraint layer — the type checker enforces what prose cannot. When created, these are read-only without owner approval:

- `app/types.py` — shared types and service Protocols (one module; there is no separate `app/protocols.py`)
- SQLite schema and SSE event vocabulary (`done`/`error` terminal events)
- Classifier output schema: `{class, confidence}`, class ∈ `chat|chit_chat|code_task|tool_call_needed|reasoning_task|vision_task` — classes, never model names
- Routing aliases: `chat-default | coder | coder-small | reasoner | reasoner-alt | vision | utility | embed | classifier | dispatcher`

## Config architecture

| File | Written by | Contains |
|---|---|---|
| `config.yaml` | humans, checked in | models, routing table, tools, prompts, defaults |
| `settings.local.yaml` | Settings UI overlay | user overrides |
| `.env` | humans, never committed | secrets only |
| `llama-swap.yaml` | `gpu/swapgen.py` only | generated — never hand-edit |
| `opencode.json` | config generator only | opencode provider wiring |

Model names live in `config.yaml`, resolved at runtime — zero-code swaps.

### Config update and live apply workflow

Edit `config.yaml` for model flags, context sizes, placement, routing defaults,
and prompts. `settings.local.yaml` is reserved for sparse Settings UI
overrides; never copy the full model roster into it, because a legacy roster
can silently mask later `config.yaml` edits.

For a config-only change, run from the repository root:

```bash
curl --fail-with-body --silent --show-error --request POST \
  --header 'Accept: application/json' \
  http://127.0.0.1:8000/api/gpu/apply
python3 scripts/config_drift_check.py
python3 scripts/load_model_check.py <alias>
```

The apply endpoint reloads production config from disk, regenerates the
deployed llama-swap file, waits for health, and re-warms residents. Restart
`ai-mega-app` only when application code changed:

```bash
sudo systemctl restart ai-mega-app
```

`config_drift_check.py` verifies generated versus deployed files;
`load_model_check.py` triggers lazy loading and prints the authoritative live
llama-server command and parsed flags.

## Verification gate

For code, tests, configuration, or frontend-source changes, run from repo root
before the completion report:

```bash
python -m pytest -q --basetemp=.pytest-tmp/run
npx tsc --noEmit
```

For documentation-only changes, `git diff --check` is the required
verification; do not run the application test suite solely because docs
changed. Run the TypeScript check only when `web/src/**` or generated
frontend output is in scope, and run pytest when Python, tests, or behavior
affecting configuration changed.

Tests run against a fake llama-swap; no GPU in CI. A feature PR = code + wiring (registered and reachable end-to-end) + tests + `docs/<feature>.md`. "Built but not injected" is a rejected PR. Every pipeline stage writes a debug span — a feature invisible in the Debug panel is not done.

**Debugging & ops scripts — ALWAYS use these, never ad-hoc `sqlite3`/`journalctl`/`curl`/`nvidia-smi`/`ps` (details in `CLAUDE.md` "Debugging & ops scripts"):**
- `scripts/trace_inspect.py <trace_id> [--with-logs]` → `logs/traces/<trace_id>.md` (spans + chat history; `--with-logs` appends filtered journals + GPU/models)
- `scripts/incident_snapshot.py <trace_id|timestamp>` → `logs/incidents/<id>.md` (trace window → both journals filtered + models + nvidia-smi + ps; replaces 6-8 manual calls)
- `scripts/model_state.py` → compact `curl :8080/v1/models` + `nvidia-smi` + `ps aux | grep llama-server` table
- `scripts/config_drift_check.py` → diff `swapgen.generate(get_config())` vs deployed `llama-swap/config.yaml`
- `scripts/load_model_check.py <alias>` → lazy-load one production alias and print its live parsed llama-server flags
- `scripts/chat_ctx_budget.py <chat_id> [--model X]` → context-fit preview via real `assemble_context`/`trusted_covered_count` (lossless fits-vs-refused, pre-flight before sending)

## Worktrees and parallel agents

The user's task prompt supplies **branch + FILE SCOPE + acceptance**; if missing, ask once — never guess. One task = one branch = one FILE SCOPE = one worktree folder (`git worktree add ../AI-Mega-App-<task> -b feat/<task> main`), one Cursor window each. Fork from `main` only; never `git checkout`/`git switch` to another task's branch inside a shared checkout. Stage by explicit path (never `git add .`), conventional commits, completion report with branch/commits/files/pytest. Full procedure: `docs/CURSOR_RULES.md` → `007-git-worktrees`.

## Boundaries (three tiers)

- **Always:** use the verification gate appropriate to the changed files; write debug spans and add tests with behavior changes; keep modules under ~300 lines.
- **Ask first:** new dependencies; schema, SSE-event, or `config.yaml` key changes; touching frozen contracts; CI/hooks/`.cursor/` edits.
- **Never:** hand-edit generated files (`llama-swap.yaml`, `web/js/**`); secrets outside `.env`; `git add .` / force-push / merge / push without explicit user request; read or copy `ref_do_not_copy/`.

When blocked by scope or constraints: stop and report. A described blocker is success; an improvised out-of-scope change is not.

## Current phase

**Phase 2 is merged and the app is live on `ailab`; do not follow the old
Phase-1/open or web-unbuilt notes in historical handoffs.** The authoritative
current roster, open defects, and 2026-08-30 experiment state are in
`docs/AGENT_CONTEXT_MEGA.md`; `docs/HANDOFF.md` is supporting history. For
the live production roster and placement, use the `Stack` table above and
`scripts/model_state.py`, not an old handoff claim. All frontend work still
builds from `web/src/**` to checked-in `web/js/**` according to
`docs/design-doc.md` and `.cursor/rules/011-ui-design.mdc`.
