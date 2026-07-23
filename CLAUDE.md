# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A personal AI platform: a claude.ai-parity web UI backed by local models on a dedicated Ubuntu GPU box. FastAPI orchestrates chat, routing, tools, RAG, and hermes-style memory; llama.cpp `llama-server` instances managed by llama-swap do all inference through one OpenAI-compatible endpoint.

**The existing `app/` directory (and `settings.json`) is the old Ollama/LiteLLM/React codebase — a post-mortem, not a foundation.** Do not extend or copy from it. Build from `PLAN.md`. See `PLAN.md` §1 for the specific failure modes that codebase hit (fragile classifier, components built-but-never-wired, silent SSE stream deaths, config sprawl) — those are the mistakes this rebuild is designed to avoid repeating.

**Current phase: Phase 0 (ground truth / benchmarking) is essentially done; no application code exists yet.** Phase-0 deliverables (`docs/phase0-measurements.md`, the model roster, the tensor-split placement decision) are landed. Before writing `app/` or `web/` code, check `PLAN.md` §5 and `AGENTS.md` "Current phase" for what's actually authorized to start.

## Pointer hierarchy — read in this order

1. `PLAN.md` — architecture source of truth. Adhere to it; flag conflicts, never improvise around it. It also records *why* (rejected alternatives, owner decisions with dates).
2. `docs/FEATURES.md` — per-feature specs (interfaces, config keys, debug spans, toggles).
3. `docs/BENCHMARK_PLAN.md` / `docs/phase0-measurements.md` — Phase-0 model testing/benchmarks; decides the roster and placement config.
4. `docs/PHASE_PROMPTS.md` — task prompts per phase (orchestrator → delegated sub-agents in worktrees).
5. `docs/CURSOR_RULES.md` and `.cursor/rules/001`–`009` — the full ruleset this file summarizes; read the `.mdc` file directly for anything not covered below.

`AGENTS.md` is the condensed agent entry point — read it first in a new session, it links everything above.

## Stack (frozen — see `.cursor/rules/001-stack.mdc`)

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, httpx, uvicorn (async, SSE-native) — these four are the entire backend dependency core; new deps need owner approval |
| Inference | llama.cpp `llama-server` behind llama-swap (`:8080`, OpenAI-compatible); reached only through `app/llm_client.py` — the app has zero scheduler/model-lifecycle code |
| Frontend | TypeScript compiled by plain `tsc` → native ES modules; no React, no bundler, no framework |
| Storage | One SQLite file (WAL) + sqlite-vec + FTS5 — chats, messages, memories, vectors, debug traces; vector access goes through a `VectorStore` interface |
| Projects | Filesystem-first (`projects/<id>/instructions.md`, `docs/`) |
| Coding agent | `opencode serve` (delegated; never nested inside the chat tool loop) |
| Browser | BrowserOS via MCP client (host machine, escalation path only, off by default) |

Model names/aliases/routing labels always come from `config.yaml`, resolved at runtime — never hardcode a model name in prompts or code.

## Config file discipline (`.cursor/rules/005-config.mdc`)

| File | Written by | Contains |
|---|---|---|
| `config.yaml` | humans, checked in | models, routing table, tools, prompts, defaults |
| `settings.local.yaml` | Settings UI overlay | user overrides (deep-merged over `config.yaml`) |
| `.env` | humans, never committed | secrets only |
| `llama-swap.yaml` | `gpu/swapgen.py` only | generated — never hand-edit; carries a "generated" header |
| `opencode.json` | config generator only | opencode provider wiring |

To change generated output, edit the generator and regenerate — don't hand-edit the artifact. Routing aliases (`chat-default | coder | coder-small | reasoner | vision | utility | embed | classifier | needle`) are config vocabulary shared by prompts and code.

## Frozen contracts

Once these exist, they're read-only without owner approval:
- `app/protocols.py` / `app/types.py` — service Protocols and shared types
- SQLite schema and the SSE event vocabulary (`done`/`error` are the only terminal events)
- Classifier output schema: `{class, effort, needs_tools}` — classes, never model names
- Routing aliases listed above

## Verification gate

Run from repo root before any completion report:

```bash
python -m pytest -q --basetemp=.pytest-tmp/run
npx tsc --noEmit
```

Full CI gate = ruff + `tsc --noEmit` + pytest + Playwright-vs-fake. Tests run against a fake llama-swap (canned OpenAI-format responses) — no GPU needed in CI. Live-hardware checks belong in `scripts/preflight.py`, run only on the box. A feature PR = code + wiring (registered at startup, reachable end-to-end) + tests + `docs/<feature>.md`; "built but not injected" is a rejected PR. Every new pipeline stage must write a debug span — a feature invisible in the Debug panel (`PLAN.md` §4.16) is not done.

Router changes additionally run the eval harness (`eval/` labeled prompt→route CSV + scoring script); Phase 2 exit criterion is ≥90%.

## Architecture shape (see `PLAN.md` §3 for the full diagram)

- FastAPI backend on the Ubuntu GPU box is "the app." The browser (any LAN machine) is the client; there is no separate cross-machine API for the box beyond that.
- Backend modules: chat orchestrator (stream/tool loop), router (override → keyword rules → grammar-constrained classifier), `tools/` (self-describing, auto-discovered, `enabled` flag each), `rag/` + `memory/` (SQLite + sqlite-vec + FTS5, hermes-style fact memories), `gpu/` (nvidia-smi inventory → llama-swap config generator), `debug/` (per-turn trace store + SSE tap).
- Frontend: one TS module per view (`mount(el, state)`/`unmount()`), hash-based `router.ts`, pub/sub `store.ts` — the entire "framework."
- Smart router is three strictly-ordered layers (manual override → deterministic keyword rules → classifier), every decision logged to the debug panel with source + latency.
- Debug is a separate window/route (`#/debug`), not an embedded panel — every turn gets a `trace_id`; every stage writes a span with real token counts/timings from llama.cpp's own `usage`/`timings` fields, never client-side estimates.

## Boundaries (`.cursor/rules/002-boundaries.mdc`)

**Always:** run the verification gate; write debug spans for new pipeline stages; add/update tests with behavior changes; snake_case Python, camelCase TypeScript; keep modules under ~300 lines.

**Ask first:** new dependencies; SQLite schema, SSE event-type, or `config.yaml` key changes; touching frozen contracts; any edit to CI, hooks, or `.cursor/`.

**Never:** hand-edit generated files (`llama-swap.yaml`, `web/js/**`); put secrets anywhere but `.env`; `git add .` / force-push / merge / push without explicit user request; read or copy `ref_do_not_copy/`.

A described blocker is success; an improvised out-of-scope change is not — stop and report instead.

## Worktrees, branches, and sub-agents

One task = one branch = one FILE SCOPE = one worktree folder: `git worktree add ../AI-Mega-App-<task> -b feat/<task> main`, forked from `main` only. Never `git checkout`/`switch` to another task's branch inside a shared checkout. Stage by explicit path (never `git add .`), conventional commit messages, completion report with branch/commits/files/pytest result. Full procedure in `.cursor/rules/007-git-worktrees.mdc`.

When a task prompt says to delegate to sub-agents (phase work), follow `.cursor/rules/009-subagents.mdc`: one sub-agent = one worktree = one FILE SCOPE = one branch; run independent sub-agents in parallel, serialize only real dependencies; the orchestrator itself implements nothing beyond spawning/collecting.

## The remote GPU box

The box (RTX 3090 24GB + RTX 3070 8GB, Ryzen 9, 64GB RAM) is reached via `ssh ubuntu-ai` (preconfigured host alias). All inference work — model downloads, `llama.cpp`/`llama-bench` runs, llama-swap config, benchmarks — happens on the box over this connection, not in the repo checkout. Models live at `/home/john/llm-stack/models`, llama.cpp is already built at `/home/john/llm-stack/engine/llama.cpp/build/bin/` — don't re-install/rebuild.

**sudo on the box is permission-gated:** run non-sudo work freely; a single `sudo` command requires explicit human approval each time, stating what it does and why. Never batch-approve or run sudo autonomously. Full detail: `.cursor/rules/008-remote-box.mdc`.

Disk on the models mount is finite (~363G) — check `df -h` before downloading a large GGUF and remove superseded blobs rather than filling the mount.
