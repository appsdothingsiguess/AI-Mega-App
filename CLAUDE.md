# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A personal AI platform: a claude.ai-parity web UI backed by local models on a dedicated Ubuntu GPU box. FastAPI orchestrates chat, routing, tools, RAG, and hermes-style memory; llama.cpp `llama-server` instances managed by llama-swap do all inference through one OpenAI-compatible endpoint.

**The existing `app/` directory (and `settings.json`) is the old Ollama/LiteLLM/React codebase — a post-mortem, not a foundation.** Do not extend or copy from it. Build from `PLAN.md`. See `PLAN.md` §1 for the specific failure modes that codebase hit (fragile classifier, components built-but-never-wired, silent SSE stream deaths, config sprawl) — those are the mistakes this rebuild is designed to avoid repeating.

**Current phase: Phase 0 closed 2026-07-23. Phase 1 backend closed 2026-07-25** — config/types/SQLite schema, `llm_client` + fake llama-swap, debug trace/span store, chat SSE endpoint, CI/test harness all merged to `main` (42 tests passing). `web/` (frontend, `p1/web-shell`) is still open, being built separately in Cursor. Before writing more `app/` or `web/` code, check `PLAN.md` §5 and `AGENTS.md` "Current phase".

**Planning docs finalized 2026-07-25.** `PLAN.md`, `docs/FEATURES.md`, `docs/PHASE_PROMPTS.md`, and `.cursor/rules/001`–`010` were audited (`011-ui-design` + `docs/design-doc.md` added later as the visual layer) into mutual consistency and are paste-ready for Phase-1 agents. The audit made `PLAN.md` §4.2 the single source for the chat contract (both other docs restate it; neither may diverge), unified span names to flat snake_case, moved the vector store to `app/rag/store.py`, and standardized frontend paths as `web/src/**` → `web/js/**`. Phase-0 carry-in **cleared**: the stale `llama-server.service` is `disabled`+`inactive` and both GPUs are idle.

**Phase-1 build order (owner decision 2026-07-25):** `ci-harness` lands alone first — it owns `tsconfig.json`, `package.json`, and the fake llama-swap, which both other agents need for the verification gate. Then backend (`app/**`) and frontend (`web/**`) run in parallel on disjoint file scopes, safe because §4.2 freezes the seam between them. Backend work runs in Claude Code; frontend in Cursor. **opencode is not used to build Phase 1** — it is Phase-4 scope, unpinned, and its session API shapes are still `[UNCERTAIN]` pending a smoke test.

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
| Placement | Big models solo-pinned to GPU0 (3090) via `CUDA_VISIBLE_DEVICES` — **never `--tensor-split`**, measured ~3x slower; `dispatcher` resident on GPU1 (3070); `classifier`/`utility`/`embed` CPU-resident |

Model names/aliases/routing labels always come from `config.yaml`, resolved at runtime — never hardcode a model name in prompts or code.

**Two Phase-0 rules that bite production code, not just benchmarks:** suppress thinking mode with llama-server's **`--reasoning off`** flag, never a `/no_think` prompt suffix (it worked on one checkpoint and silently failed on others, returning empty `content`); and give thinking-capable models a real `max_tokens` (≥1024, ≥4096 for reasoning) — an under-budgeted call returns empty and reads as a model failure. Full ruleset: `.cursor/rules/010-benchmark-eval-methodology.mdc`.

## Config file discipline (`.cursor/rules/005-config.mdc`)

| File | Written by | Contains |
|---|---|---|
| `config.yaml` | humans, checked in | models, routing table, tools, prompts, defaults |
| `settings.local.yaml` | Settings UI overlay | user overrides (deep-merged over `config.yaml`) |
| `.env` | humans, never committed | secrets only |
| `llama-swap.yaml` | `gpu/swapgen.py` only | generated — never hand-edit; carries a "generated" header |
| `opencode.json` | config generator only | opencode provider wiring |

To change generated output, edit the generator and regenerate — don't hand-edit the artifact. Routing aliases (`chat-default | coder | coder-small | reasoner | reasoner-alt | vision | utility | embed | classifier | dispatcher`) are config vocabulary shared by prompts and code.

## Frozen contracts

Once these exist, they're read-only without owner approval:
- `app/types.py` — shared types and service Protocols (one module; there is no separate `app/protocols.py`)
- SQLite schema and the SSE event vocabulary (`done`/`error` are the only terminal events)
- **The full chat contract lives in `PLAN.md` §4.2** — REST endpoints, the 7 SSE events, and the flat snake_case span-stage list. Adding an event or endpoint is an owner decision. Two consequences agents get wrong: route/citations/usage/compaction state ride the **`done` payload** (build it as a dict later stages add keys to) rather than getting their own events, and artifacts are detected **client-side** in `web/src/artifacts/detect.ts` — there is no `artifact` event.
- Classifier output schema: `{class, confidence}` where class ∈ `chat|chit_chat|code_task|tool_call_needed|reasoning_task|vision_task` — classes, never model names (re-frozen 2026-07-23 to the taxonomy that measured 91.76%; `effort`/`needs_tools` moved to the rules layer)
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
- Backend modules: chat orchestrator (stream/tool loop), router (override → keyword rules → grammar-constrained classifier), `tools/` (self-describing, auto-discovered, `enabled` flag each), `rag/` + `memory/` (SQLite FTS5 + Qdrant behind `VectorStore`, hermes-style fact memories), `gpu/` (nvidia-smi inventory → llama-swap config generator), `debug/` (per-turn trace store + SSE tap).
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

## The GPU box

The box (RTX 3090 24GB + RTX 3070 8GB, Ryzen 9, 64GB RAM) is **hostname `ailab`**. Check `hostname` before reaching for ssh: sessions started in `/home/john/AI-Mega-App` are typically running *on* the box already, in which case inference commands run locally and `ssh ubuntu-ai` does not resolve. That alias only exists on machines configured for it — when off-box, all inference work (model downloads, `llama.cpp`/`llama-bench` runs, llama-swap config, benchmarks) goes over that connection rather than into the repo checkout. Models live at `/home/john/llm-stack/models`, llama.cpp is already built at `/home/john/llm-stack/engine/llama.cpp/build/bin/` — don't re-install/rebuild.

**llama-swap runs as a systemd service** (`/etc/systemd/system/llama-swap.service`, enabled, `WantedBy=multi-user.target`) — it starts on boot and restarts on failure, so `:8080` should already be up at the start of a session. `sudo systemctl status/restart llama-swap` to check or bounce it (a `sudo` command still needs explicit approval per the rule below). Nothing in Phase-1 CI needs it: tests run against the fake llama-swap.

**sudo on the box is permission-gated:** run non-sudo work freely; a single `sudo` command requires explicit human approval each time, stating what it does and why. Never batch-approve or run sudo autonomously. Full detail: `.cursor/rules/008-remote-box.mdc`.

Disk on the models mount is finite (~363G) — check `df -h` before downloading a large GGUF and remove superseded blobs rather than filling the mount.
