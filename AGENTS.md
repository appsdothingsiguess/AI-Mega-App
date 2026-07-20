# AI Mega App — Agent Entry Point

Personal AI platform: a claude.ai-parity web UI backed by local models on a dedicated Ubuntu GPU box. FastAPI backend orchestrates chat, routing, tools, RAG, and hermes-style memory; llama.cpp `llama-server` instances managed by llama-swap do all inference through one OpenAI-compatible endpoint. The old Ollama/LiteLLM/React codebase in this repo is a post-mortem, not a foundation — build from `PLAN.md`, not from existing `app/` or `web/` code.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, httpx, uvicorn (async, SSE-native) |
| Inference | llama.cpp `llama-server` behind llama-swap (`:8080`, OpenAI-compatible; groups: resident small models + swapped big-model slot) |
| Frontend | TypeScript compiled by plain `tsc` → native ES modules; no React, no bundler, no framework |
| Storage | SQLite (WAL) + sqlite-vec + FTS5 — one file for chats, memories, vectors, traces |
| Projects | Filesystem-first (`projects/<id>/instructions.md`, `docs/`) |
| Coding agent | opencode serve (delegated, never nested in the chat tool loop) |
| Browser | BrowserOS via MCP client (host machine, escalation path only) |

## Pointer hierarchy (read in this order)

1. `PLAN.md` — architecture source of truth. Adhere to it; flag conflicts, never improvise around it.
2. `docs/FEATURES.md` — per-feature specs (interfaces, config keys, debug spans, toggles).
3. `docs/PHASE_PROMPTS.md` — task prompts per phase.
4. `docs/CURSOR_RULES.md` — the full `.cursor/rules/` ruleset, hooks, and `.cursorignore`.

## Frozen contracts (once they exist)

Interface files are the real constraint layer — the type checker enforces what prose cannot. When created, these are read-only without owner approval:

- `app/protocols.py` / `app/types.py` — service Protocols and shared types
- SQLite schema and SSE event vocabulary (`done`/`error` terminal events)
- Classifier output schema: `{class, effort, needs_tools}` — classes, never model names
- Routing aliases: `chat-default | coder | reasoner | vision | utility | embed | classifier | needle`

## Config architecture

| File | Written by | Contains |
|---|---|---|
| `config.yaml` | humans, checked in | models, routing table, tools, prompts, defaults |
| `settings.local.yaml` | Settings UI overlay | user overrides |
| `.env` | humans, never committed | secrets only |
| `llama-swap.yaml` | `gpu/swapgen.py` only | generated — never hand-edit |
| `opencode.json` | config generator only | opencode provider wiring |

Model names live in `config.yaml`, resolved at runtime — zero-code swaps.

## Verification gate

From repo root, before any completion report:

```bash
python -m pytest -q --basetemp=.pytest-tmp/run
npx tsc --noEmit
```

Tests run against a fake llama-swap; no GPU in CI. A feature PR = code + wiring (registered and reachable end-to-end) + tests + `docs/<feature>.md`. "Built but not injected" is a rejected PR. Every pipeline stage writes a debug span — a feature invisible in the Debug panel is not done.

## Worktrees and parallel agents

The user's task prompt supplies **branch + FILE SCOPE + acceptance**; if missing, ask once — never guess. One task = one branch = one FILE SCOPE = one worktree folder (`git worktree add ../AI-Mega-App-<task> -b feat/<task> main`), one Cursor window each. Fork from `main` only; never `git checkout`/`git switch` to another task's branch inside a shared checkout. Stage by explicit path (never `git add .`), conventional commits, completion report with branch/commits/files/pytest. Full procedure: `docs/CURSOR_RULES.md` → `007-git-worktrees`.

## Boundaries (three tiers)

- **Always:** run the verification gate; write debug spans; add tests with behavior changes; keep modules under ~300 lines.
- **Ask first:** new dependencies; schema, SSE-event, or `config.yaml` key changes; touching frozen contracts; CI/hooks/`.cursor/` edits.
- **Never:** hand-edit generated files (`llama-swap.yaml`, `web/js/**`); secrets outside `.env`; `git add .` / force-push / merge / push without explicit user request; read or copy `ref_do_not_copy/`.

When blocked by scope or constraints: stop and report. A described blocker is success; an improvised out-of-scope change is not.

## Current phase

**Phase 0 — Ground truth.** Box setup: llama.cpp build, llama-swap systemd unit, initial model downloads, hand-written first `llama-swap.yaml`, swap-latency and sqlite-vec benchmarks. Deliverable is a doc of measured facts. **No app code in Phase 0.** See `PLAN.md` §5.
