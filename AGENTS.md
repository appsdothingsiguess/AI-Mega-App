# AI Mega App — Agent Entry Point

Personal AI platform: a claude.ai-parity web UI backed by local models on a dedicated Ubuntu GPU box. FastAPI backend orchestrates chat, routing, tools, RAG, and hermes-style memory; llama.cpp `llama-server` instances managed by llama-swap do all inference through one OpenAI-compatible endpoint. The old Ollama/LiteLLM/React codebase in this repo is a post-mortem, not a foundation — build from `PLAN.md`, not from existing `app/` or `web/` code.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, httpx, uvicorn (async, SSE-native) |
| Inference | llama.cpp `llama-server` behind llama-swap (`:8080`, OpenAI-compatible; group `resident` = classifier/utility/embed on CPU + dispatcher on GPU1, group `gpu0-main` = one big model at a time on the 3090) |
| Frontend | TypeScript compiled by plain `tsc` → native ES modules; no React, no bundler, no framework |
| Storage | SQLite (WAL) + FTS5 for text/chats/memories/traces + **Qdrant** for vectors, behind `VectorStore` |
| Projects | Filesystem-first (`projects/<id>/instructions.md`, `docs/`) |
| Coding agent | opencode serve (delegated, never nested in the chat tool loop) |
| Browser | BrowserOS via MCP client (host machine, escalation path only) |

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

**Phase 1 — Skeleton with eyes. Backend closed 2026-07-25.** `app/config.py`, `app/db.py`+schema, `app/llm_client.py`, `app/debug/**` (trace/span store + SSE tap), `app/chat/**` (SSE chat endpoint), and the CI/test harness are all merged to `main` (42 tests passing, ruff clean). `web/` (`p1/web-shell`) is still open in Cursor — Phase 1 isn't done until it lands and the exit demo runs. **All frontend work builds to `docs/design-doc.md`** (graphite/indigo dark system, compact density, IBM Plex Sans/Mono; prototype: `docs/design_example.html`), enforced by `.cursor/rules/011-ui-design.mdc` — the UI mirrors claude.ai's *structure* only, never its skin. Phase 0 closed 2026-07-23: roster locked, placement decided (**Config B** — big models solo-pinned to GPU0 via `CUDA_VISIBLE_DEVICES`, `dispatcher` on GPU1, `classifier`/`utility`/`embed` on CPU), sqlite-vec rejected for Qdrant, classifier at 91.76%, dispatcher = Hammer2.1-1.5b. Read `docs/PHASE0_FINDINGS_SUMMARY.md` before touching anything model-shaped.

Phase 1 builds: config load/validate, `/health`, `llm_client`, SQLite schema, SSE chat endpoint, minimal chat UI, **debug trace store + Debug view**, `done`/`error` contract, plus the test harness and CI from day one. Exit: chat with a manually-picked model, every turn fully traced. Run as an orchestrator delegating sub-agents (rule `009-subagents`); prompts in `docs/PHASE_PROMPTS.md`.

Phase-0 carry-ins: the live llama-swap config was fixed and verified on 2026-07-23 (groups block, `--reasoning off`, dispatcher on GPU1, CPU residents no longer holding CUDA contexts) — develop against it as-is and do not hand-edit it further; swapgen reproduces those four properties in Phase 2. The stale `llama-server.service` was disabled by the owner and verified `disabled`+`inactive` with both GPUs idle on 2026-07-25 — **carry-in closed**. Size `first_token_timeout_s` above the **measured 12.47s cold load** and ship a visible `model_loading` state.

Planning docs were audited into mutual consistency on 2026-07-25 and are paste-ready: `PLAN.md` §4.2 is the single source for the chat contract, span names are flat snake_case, the vector store is `app/rag/store.py`, and frontend paths are `web/src/**` → `web/js/**`. Build order: `ci-harness` alone first (owns `tsconfig.json`, `package.json`, fake llama-swap), then backend and frontend in parallel.
