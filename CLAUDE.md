# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A personal AI platform: a claude.ai-parity web UI backed by local models on a dedicated Ubuntu GPU box. FastAPI orchestrates chat, routing, tools, RAG, and hermes-style memory; llama.cpp `llama-server` instances managed by llama-swap do all inference through one OpenAI-compatible endpoint.

## Operational scripts — read before benchmarking or tracing

The recent model/context work is captured in `scripts/`; use these existing harnesses before writing a new one.
Run from the repository root, preferably on `ailab`:

```bash
# Isolated boot/request/VRAM/throughput test; tears down llama-server when finished.
python3 scripts/bench_server.py --label <label> --model <model.gguf> \
  --model-class <chat-default|coder|coder-small|reasoner|vision|utility> --ctx <tokens>

# Growing conversation: recall, latency, and usable context ceiling.
python3 scripts/bench_context_depth.py --label <label> --model <model.gguf> \
  --model-class <role> --ctx <tokens> --checkpoints 2000,8000,16000,32000

# Prompt/response collection for manual quality review. The server is already running;
# provide --system when reproducing a production summarizer call.
python3 scripts/eval_quality_transcripts.py --prompts <prompts.json> \
  --class <reasoner|coder|vision|summarizer> --model-label <label> --port <port> \
  [--model <llama-swap-alias>]

# Router evaluation; the /v1 suffix is required.
python3 scripts/eval_router.py --base-url http://127.0.0.1:8080/v1

# Agent-driven sweep from the checked-in base profile; only changed variables
# are supplied, and every variant is isolated, logged, ranked, and torn down.
CUDA_VISIBLE_DEVICES=0 python3 scripts/bench_sweep.py --profile qwen38-mtp \
  --matrix ub=256,512,1024,2048 --matrix b=2048,4096
# Sweep environment variables with --matrix-env.
CUDA_VISIBLE_DEVICES=0 python3 scripts/bench_sweep.py --profile qwen38-mtp \
  --matrix flash-attn=on --matrix-env GGML_CUDA_GRAPH_OPT=0,1
```

The `qwen38-mtp` profile contains the validated 90K-context Qwen3.8
separate-MTP baseline. Prefer `bench_sweep.py` for optimization: it expands
only the requested matrix dimensions, runs variants sequentially, tears down
llama-server after each run, and writes a ranked JSON summary in
`logs/benchmarks/server/`. Do not hand-copy the full profile for a one-variable
change.

The isolated harnesses should follow the production no-`--tensor-split` placement rule. Quality transcripts are
written to `logs/benchmarks/quality/<class>.jsonl` and are not automatically scored. To investigate a live
`trace_id`, query both `traces` and `spans` in `data/app.db`; title/summary jobs create separate traces linked by
`chat_id`, so the supplied trace may be only the parent/title trace rather than the actual `summary` span.

**The existing `app/` directory (and `settings.json`) is the old Ollama/LiteLLM/React codebase — a post-mortem, not a foundation.** Do not extend or copy from it. Build from `PLAN.md`. See `PLAN.md` §1 for the specific failure modes that codebase hit (fragile classifier, components built-but-never-wired, silent SSE stream deaths, config sprawl) — those are the mistakes this rebuild is designed to avoid repeating.

**Phase 2 is merged and the app is live on `ailab`; do not follow the old
Phase-1/open or web-unbuilt notes in historical handoffs.** The current
roster, open defects, and isolated 2026-08-30 worker state are in
`docs/AGENT_CONTEXT_MEGA.md`; `docs/HANDOFF.md` is supporting history.
Before writing more `app/` or `web/` code, check `PLAN.md` §5 and
`AGENTS.md` "Current phase".

**Live model-role update — 2026-08-24.** `chat-default` is Qwen3.8-27B with llama-server reasoning explicitly off. The separate `reasoner` alias targets the same Qwen blob through a distinct symlink and enables medium reasoning; `reasoner-alt` is DeepSeek-R1 32B. A five-prompt live comparison recorded in `logs/benchmarks/quality/reasoner.jsonl` found both correct; Qwen completed the selected runs in 111.3s / 3,081 output tokens (~27.7 end-to-end output tok/s), versus DeepSeek’s 141.3s / 3,243 (~23.0). This is a small manual-quality comparison, not a general model ranking. Pi’s previous 12–14 tok/s tool-loop problem was chiefly server-side reasoning on every tool turn, not oversized prompt context; disabling reasoning reduced one matched workflow from 205.5s / 4,267 completion tokens to 63.8s / 1,462 (3.2x faster). See `docs/HANDOFF.md` for method/caveats.

**Planning docs finalized 2026-07-25.** `PLAN.md`, `docs/FEATURES.md`, `docs/PHASE_PROMPTS.md`, and `.cursor/rules/001`–`010` were audited (`011-ui-design` + `docs/design-doc.md` added later as the visual layer) into mutual consistency. The audit made `PLAN.md` §4.2 the single source for the chat contract (both other docs restate it; neither may diverge), unified span names to flat snake_case, moved the vector store to `app/rag/store.py`, and standardized frontend paths as `web/src/**` → `web/js/**`. Phase-0 carry-in **cleared**: the stale `llama-server.service` is `disabled`+`inactive` and both GPUs are idle.

## Pointer hierarchy — read in this order

1. `PLAN.md` — architecture source of truth. Adhere to it; flag conflicts, never improvise around it. It also records *why* (rejected alternatives, owner decisions with dates).
2. `docs/FEATURES.md` — per-feature specs (interfaces, config keys, debug spans, toggles).
3. `docs/PHASE0_FINDINGS_SUMMARY.md` — the locked roster and every Phase-0 decision, one line each. Raw numbers in `docs/phase0-measurements.md`; the plan that produced them in `docs/BENCHMARK_PLAN.md` (both closed).
4. `docs/PHASE_PROMPTS.md` — task prompts per phase (orchestrator → delegated sub-agents in worktrees).
5. `docs/design-doc.md` — visual/UI source of truth (palette, screen inventory, component states) for all `web/**` work; enforced by `.cursor/rules/011-ui-design.mdc`. The UI mirrors claude.ai's *structure*, never its skin.
6. `docs/CURSOR_RULES.md` and `.cursor/rules/001`–`011` — the full ruleset this file summarizes; read the `.mdc` file directly for anything not covered below.

`AGENTS.md` is the condensed agent entry point — read it first in a new session, it links everything above.

## Stack (frozen — see `.cursor/rules/001-stack.mdc`)

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, httpx, uvicorn (async, SSE-native) — these four are the entire backend dependency core; new deps need owner approval |
| Inference | llama.cpp `llama-server` behind llama-swap (`:8080`, OpenAI-compatible); reached only through `app/llm_client.py` — the app has zero scheduler/model-lifecycle code |
| Frontend | TypeScript compiled by plain `tsc` → native ES modules; no React, no bundler, no framework |
| Storage | One SQLite file (WAL) + FTS5 — chats, messages, memories, chunk text, debug traces — **plus Qdrant for vectors**; all vector access goes through the `VectorStore` interface (sqlite-vec failed its Phase-0 100k gate: p95 105ms vs a 50ms bar) |
| Projects | Filesystem-first (`projects/<id>/instructions.md`, `docs/`) |
| Coding agent | `opencode serve` (delegated; never nested inside the chat tool loop) |
| Browser | BrowserOS via MCP client (host machine, escalation path only, off by default) |
| Placement | Big models solo-pinned to GPU0 (3090) via `CUDA_VISIBLE_DEVICES` — **never `--tensor-split`**, measured ~3x slower; `coder-small` shares GPU0's swap slot (not GPU1 — no room there once `utility-gpu` is resident, see below); `dispatcher` + `utility-gpu` (summarizer GPU1 fast path, ~14x CPU decode) both resident on GPU1 (3070), ~590 MiB free at peak — no third model fits; `classifier`/`utility` (summarizer CPU fallback)/`embed` CPU-resident |

Model names/aliases/routing labels always come from `config.yaml`, resolved at runtime — never hardcode a model name in prompts or code.

**Two Phase-0 rules that bite production code, not just benchmarks:** suppress thinking mode with llama-server's **`--reasoning off`** flag, never a `/no_think` prompt suffix (it worked on one checkpoint and silently failed on others, returning empty `content`); and give thinking-capable models a real `max_tokens` (≥1024, ≥4096 for reasoning) — an under-budgeted call returns empty and reads as a model failure. Full ruleset: `.cursor/rules/010-benchmark-eval-methodology.mdc`.

## Live services and relay modes

Production is `ai-mega-app :8000 -> llama-swap :8080`; the persistent
`pi-capture-relay.service` exposes that route to the Windows/Pi Harness at
`http://192.168.0.89:8081/v1` and forwards to `127.0.0.1:8080`. The relay
allows client `192.168.0.246` and captures prompt-bearing traffic in
`/tmp/pi-request-captures/`.

The isolated Qwen3.6 worker is a different mode:
`qwen36-ngram.service :5807` plus `pi-qwen36-relay.service :8082`, exposed to
the Harness as `http://192.168.0.89:8082/v1` with model
`qwen3.6-35b-ngram` and text-only input. While it is active,
`llama-swap.service` and the normal app are intentionally stopped so GPU1's
production residents do not compete for VRAM. Never apply GPU config or
restart llama-swap in that mode; the 8082 worker is experimental and is not
part of the production roster. Unit templates live in `ops/`; the worker
warmup uses `scripts/warmup_openai_server.py`. See `AGENTS.md` for the
complete mode map and capture-handling warning.

Both relay units are independently enabled user services; they can both be
running, but each requires its own upstream (`:8081` → `:8080`, `:8082` →
`:5807`). For a manual Qwen3.6 test, first stop production, then run
`systemctl --user start pi-qwen36-relay.service qwen36-ngram.service`.
`qwen36-ngram.service` runs its warmup helper as `ExecStartPost`; a successful
start is warm, and `systemctl --user restart qwen36-ngram.service` re-warms
it. The regular `load_model_check.py` selector remains production-only.

## Config file discipline (`.cursor/rules/005-config.mdc`)

| File | Written by | Contains |
|---|---|---|
| `config.yaml` | humans, checked in | models, routing table, tools, prompts, defaults |
| `settings.local.yaml` | Settings UI overlay | user overrides (deep-merged over `config.yaml`) |
| `.env` | humans, never committed | secrets only |
| `llama-swap.yaml` | `gpu/swapgen.py` only | generated — never hand-edit; carries a "generated" header |
| `opencode.json` | config generator only | opencode provider wiring |

To change generated output, edit the generator and regenerate — don't hand-edit the artifact. Routing aliases (`chat-default | coder | coder-small | reasoner | reasoner-alt | vision | utility | embed | classifier | dispatcher`) are config vocabulary shared by prompts and code.

### Config update and live apply workflow

Edit `config.yaml` for model flags, context, placement, routing defaults, and
prompts. Treat `settings.local.yaml` as a sparse Settings UI overlay only;
never store a copied full model roster there, because it can silently override
newer `config.yaml` values.

For a config-only change, run from the repository root:

```bash
curl --fail-with-body --silent --show-error --request POST \
  --header 'Accept: application/json' \
  http://127.0.0.1:8000/api/gpu/apply
python3 scripts/config_drift_check.py
python3 scripts/load_model_check.py <alias>
```

The apply endpoint reloads config from disk before swapgen, writes the
generated llama-swap config, waits for health, and re-warms residents. Restart
`ai-mega-app` only when application code changed:

```bash
sudo systemctl restart ai-mega-app
```

`config_drift_check.py` verifies generated-versus-deployed files. The live
truth is `load_model_check.py`, which triggers lazy loading and prints the
actual running llama-server command with parsed flags. `restart_apply_test.sh`
is the full restart/apply/pytest/TypeScript gate, not a requirement for a
config-only edit.

## Frozen contracts

Once these exist, they're read-only without owner approval:
- `app/types.py` — shared types and service Protocols (one module; there is no separate `app/protocols.py`)
- SQLite schema and the SSE event vocabulary (`done`/`error` are the only terminal events)
- **The full chat contract lives in `PLAN.md` §4.2** — REST endpoints, the 7 SSE events, and the flat snake_case span-stage list. Adding an event or endpoint is an owner decision. Two consequences agents get wrong: route/citations/usage/compaction state ride the **`done` payload** (build it as a dict later stages add keys to) rather than getting their own events, and artifacts are detected **client-side** in `web/src/artifacts/detect.ts` — there is no `artifact` event.
- Classifier output schema: `{class, confidence}` where class ∈ `chat|chit_chat|code_task|tool_call_needed|reasoning_task|vision_task` — classes, never model names (re-frozen 2026-07-23 to the taxonomy that measured 91.76%; `effort`/`needs_tools` moved to the rules layer)
- Routing aliases listed above

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

Full CI gate = ruff + `tsc --noEmit` + pytest + Playwright-vs-fake. Tests run against a fake llama-swap (canned OpenAI-format responses) — no GPU needed in CI. Live-hardware checks belong in `scripts/preflight.py`, run only on the box. A feature PR = code + wiring (registered at startup, reachable end-to-end) + tests + `docs/<feature>.md`; "built but not injected" is a rejected PR. Every new pipeline stage must write a debug span — a feature invisible in the Debug panel (`PLAN.md` §4.16) is not done.

Router changes additionally run the eval harness (`eval/` labeled prompt→route CSV + scoring script); Phase 2 exit criterion is ≥90%. `--base-url` for `scripts/eval_router.py` must include `/v1` (matching `config.yaml`'s `llama_swap.base_url`, e.g. `http://127.0.0.1:8080/v1`) — the bare host:port 404s every classifier call and silently falls back to `chat` for everything, which reads like a broken classifier but is just a wrong URL.

## Architecture shape (see `PLAN.md` §3 for the full diagram)

- FastAPI backend on the Ubuntu GPU box is "the app." The browser (any LAN machine) is the client; there is no separate cross-machine API for the box beyond that.
- Backend modules: chat orchestrator (stream/tool loop), router (override → keyword rules → grammar-constrained classifier), `tools/` (self-describing, auto-discovered, `enabled` flag each), `rag/` + `memory/` (SQLite FTS5 + Qdrant behind `VectorStore`, hermes-style fact memories), `gpu/` (nvidia-smi inventory → llama-swap config generator), `debug/` (per-turn trace store + SSE tap).
- Frontend: one TS module per view (`mount(el, state)`/`unmount()`), hash-based `router.ts`, pub/sub `store.ts` — the entire "framework."
- Smart router is three strictly-ordered layers (manual override → deterministic keyword rules → classifier), every decision logged to the debug panel with source + latency.
- Debug is a separate window/route (`#/debug`), not an embedded panel — every turn gets a `trace_id`; every stage writes a span with real token counts/timings from llama.cpp's own `usage`/`timings` fields, never client-side estimates.

## Debugging & ops scripts — ALWAYS use these (never ad-hoc sqlite/journalctl/curl)

**Rule: for traces/chats, GPU state, config drift, or context-fit pre-flight, use the scripts below — never hand-rolled `sqlite3`, `journalctl`, `curl :8080/v1/models`, `nvidia-smi`, or `ps aux` one-offs.** All run under both `python3` (system) and `.venv/bin/python`; outputs land in `logs/` (git-ignored).

- **`scripts/trace_inspect.py <trace_id>`** — trace + chat logs. Fetches `traces` + all `spans` + `chats`/`messages` → `logs/traces/<trace_id>.md` (overview, chat metadata + sibling trace_ids, full message history, span waterfall, per-span JSON). `--with-logs` appends that window's filtered `journalctl -u ai-mega-app`/`-u llama-swap` + `nvidia-smi`/`ps`/`/v1/models` (tune `--log-pad`/`--log-lines`/`--base-url`). On `trace not found` lists the 5 most recent traces. Also `--db`/`--out`/`--output`/`--stdout`.
- **`scripts/incident_snapshot.py <trace_id|timestamp>`** — biggest win. Resolves a trace's span window (min `started_at` → max `ended_at`, `--pad` 90s) or a raw ISO/epoch timestamp, then dumps *both* filtered journals + `curl :8080/v1/models` + `nvidia-smi` + `ps aux | grep llama` → `logs/incidents/<id>.md`. Filters routine polling (`/api/gpu/inventory`, `/api/debug/summary-status`, `304`s, `/static/`; `--no-filter` to keep). `--with-trace` embeds the full `trace_inspect` dump. Replaces ~6-8 manual correlating-timestamps calls.
- **`scripts/model_state.py`** — what's loaded and where. Wraps `curl :8080/v1/models` + `nvidia-smi --query-gpu` + `ps aux | grep llama-server` into one compact table → stdout (or `--out file`, `--json`).
- **`scripts/config_drift_check.py`** — diffs fresh `app/gpu/swapgen.generate(get_config())` vs deployed `config.gpu.swap_yaml_path` (`/home/john/llm-stack/serving/llama-swap/config.yaml`), exit 1 + unified diff on drift. Catches silent `coder-small` placement drift. `--out /tmp/fresh.yaml` to preview fresh, `--write` to overwrite deployed.
- **`scripts/load_model_check.py <alias>`** — sends a minimal request to trigger lazy loading, then prints the live alias status, GGUF, full command, and parsed llama-server flags. Use this after apply; it catches stale running processes that file-only drift checks cannot.
- **`scripts/bench_sweep.py`** — agent-oriented isolated throughput matrix. Load a profile once with `--profile`, vary llama flags with repeated `--matrix KEY=V1,V2`, vary environment variables with `--matrix-env KEY=V1,V2`, and read the ranked `<label>-sweep.json`; no manual per-variant command construction.
- **`scripts/chat_ctx_budget.py <chat_id> [--model X]`** — context-fit pre-flight. Uses the *current* real `app/chat/context.py assemble_context` + `app/background/summary_coverage.py trusted_covered_count` (exactly as `app/chat/turn.py:_request_completion` calls them), so it never drifts. Semantics are lossless: reports "✓ FITS" or "⛔ WOULD BE REFUSED" (no silent truncation) with budget vs estimated tokens, trusted covered count, and a per-message coverage table. `--next-msg` simulates appending a user message; `--list` shows recent chats.

## Boundaries (`.cursor/rules/002-boundaries.mdc`)

**Always:** use the verification gate appropriate to the changed files; write debug spans for new pipeline stages; add/update tests with behavior changes; snake_case Python, camelCase TypeScript; keep modules under ~300 lines.

**Ask first:** new dependencies; SQLite schema, SSE event-type, or `config.yaml` key changes; touching frozen contracts; any edit to CI, hooks, or `.cursor/`.

**Never:** hand-edit generated files (`llama-swap.yaml`, `web/js/**`); put secrets anywhere but `.env`; `git add .` / force-push / merge / push without explicit user request; read or copy `ref_do_not_copy/`.

A described blocker is success; an improvised out-of-scope change is not — stop and report instead.

## Worktrees, branches, and sub-agents

One task = one branch = one FILE SCOPE = one worktree folder: `git worktree add ../AI-Mega-App-<task> -b feat/<task> main`, forked from `main` only. Never `git checkout`/`switch` to another task's branch inside a shared checkout. Stage by explicit path (never `git add .`), conventional commit messages, completion report with branch/commits/files/pytest result. Full procedure in `.cursor/rules/007-git-worktrees.mdc`.

When a task prompt says to delegate to sub-agents (phase work), follow `.cursor/rules/009-subagents.mdc`: one sub-agent = one worktree = one FILE SCOPE = one branch; run independent sub-agents in parallel, serialize only real dependencies; the orchestrator itself implements nothing beyond spawning/collecting.

## The GPU box

The box (RTX 3090 24GB + RTX 3070 8GB, Ryzen 9, 64GB RAM) is **hostname `ailab`**. Check `hostname` before reaching for ssh: sessions started in `/home/john/AI-Mega-App` are typically running *on* the box already, in which case inference commands run locally and `ssh ubuntu-ai` does not resolve. That alias only exists on machines configured for it — when off-box, all inference work (model downloads, `llama.cpp`/`llama-bench` runs, llama-swap config, benchmarks) goes over that connection rather than into the repo checkout. Models live at `/home/john/llm-stack/models`, llama.cpp is already built at `/home/john/llm-stack/engine/llama.cpp/build/bin/` — don't re-install/rebuild.

**llama-swap runs as a systemd service** (`/etc/systemd/system/llama-swap.service`, enabled, `WantedBy=multi-user.target`) — it starts on boot and restarts on failure, so `:8080` should already be up at the start of a session. `sudo systemctl status/restart llama-swap` to check or bounce it (a `sudo` command still needs explicit approval per the rule below). Nothing in Phase-1 CI needs it: tests run against the fake llama-swap.

**sudo on the box is permission-gated:** run non-sudo work freely; a single `sudo` command requires explicit human approval each time, stating what it does and why. Never batch-approve or run sudo autonomously. Full detail: `.cursor/rules/008-remote-box.mdc`.

Disk on the models mount is finite (~363G) — check `df -h` before downloading a large GGUF and remove superseded blobs rather than filling the mount.
