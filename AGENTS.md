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

**Phase 2 merged — live app running on `ailab`.** 136+ tests passing, `tsc --noEmit` clean, full end-to-end chat working against real llama-swap on GPU0 (3090) + GPU1 (3070). Phase 0 closed 2026-07-23 (roster locked, Config B placement, classifier 91.76%, dispatcher = Hammer2.1-1.5b — see `docs/PHASE0_FINDINGS_SUMMARY.md`). Phase 1 backend + frontend closed 2026-07-25. Phase 2 (config-schema, router-classifier, gpu-swapgen, background-utility, router-eval, settings-api, settings-ui) merged 2026-07-31 and live-verified 2026-08-02 (router eval 93.33%, GPU-reassignment demo working). Subsequent hardening commits shipped on `main`: `8c4f7b4` (regenerate button, tok/s display, swap-aware routing, warmup timeout, drain reduction), `53dac3a` (scroll stick-to-bottom, nav-interrupt fix), `0170ca4` (warmup logging + resident-model hot-at-boot).

**Open items as of 2026-08-11 — see `docs/FIX_PLAN_2026-08-11.md` and `docs/AGENT_CONTEXT_MEGA.md` for the audited state.** Four parallel workstreams in flight:
- **WS-A fix/config-drift** — `CUDA_DEVICE_ORDER=PCI_BUS_ID` in swapgen, revert `settings.local.yaml` drift (coder-small residency, ttl_s, routing overlay), classifier timeout 6→90s, legacy ollama tag renames in settings.json.
- **WS-B fix/backend-reliability** — classifier/utility hot at service start (warmup retry + stored task ref), `_on_turn_complete` on error/timeout paths, `reasoning_content` field on `ChatDelta` + parse in `llm_client`.
- **WS-C fix/web-gaps** — retry affordance on error banner, response time + usage inline in `.msg-meta`, fix misleading `chat.ts:35-36` comment, real `npx tsc` build committed with `web/src/**`.
- **WS-D docs/refresh** (this branch) — refresh stale "Phase 1 open / web unbuilt" prose in `AGENTS.md`/`CLAUDE.md`, add 2026-08-11 entry to `docs/HANDOFF.md` marking audited-fixed items and recording the plan.

All frontend work still builds to `docs/design-doc.md` (graphite/indigo dark system, compact density, IBM Plex Sans/Mono), enforced by `.cursor/rules/011-ui-design.mdc`. Placement truth: `gpu0-main` swap group = `[chat-default, coder, coder-small, vision]` (one at a time on 3090); `resident` group = `[dispatcher(GPU1), utility, embed, classifier (CPU)]`; `reasoner` = chat-default blob w/ thinking (deduped, never a swap entry).
