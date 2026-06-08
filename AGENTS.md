# Prompter X — Agent Entry Point

**Architecture spec (source of truth):** `prompter_x_complete_spec.md`

## What This Is

Personal AI platform replacing Claude.ai (web) with intent routing, local/remote model management, and deep research. Built on FastAPI + React/Vite + filesystem projects.

## Stack

Python 3.12, FastAPI (async), React 18 / Vite / TypeScript, LiteLLM, Ollama (Docker), Qdrant (Docker).

## Related rules (always applied)

| Rule | Topic |
|------|--------|
| `.cursor/rules/008-git-discipline.mdc` | Plan order, branches, worktrees, commits, pytest, roles |
| `.cursor/rules/006-no-scope-creep.mdc` | FILE SCOPE, frozen `project_manager.py`, no ChatService |
| `.cursor/rules/007-no-hardcoding.mdc` | Models/aliases from `settings.json`, intent labels |
| `.cursor/rules/009-no-ref-do-not-copy.mdc` | `ref_do_not_copy/` blacklisted |

## Frozen contracts — read only, never modify

- `app/protocols.py` — service Protocol interfaces
- `app/types.py` — shared types (SearchResult, ClassifierOutput, RouteSource, RouteResult, ToolCallDelta)
- `app/project_manager.py` — project CRUD, threads, filesystem structure
- Intent labels: `general_chat | web_search | deep_research | coding_basic | coding_advanced | bash | pdf_gen | file_ops | vision`

## Config architecture

| File | Contains | Secrets? |
|------|----------|----------|
| `settings.json` | Models, router, embedding, search, infra | No |
| `.env` | API keys (`OPENCODE_API_KEY`, etc.) | Yes |
| `litellm_config.yaml` | Model alias → endpoint routing | No |

## Reference material (blacklisted)

`ref_do_not_copy/` is excluded (`.cursorignore`, `.gitignore`) — see rule 009. Use in-repo docs under `docs/` for design handoffs.

## Task contract

The **user's message** supplies branch, FILE SCOPE, and acceptance. Wait for it before editing. If branch or scope is missing, ask once — do not guess.

When the prompt includes **Workspace** / **worktree**, treat that folder as isolated: verify `git branch --show-current` matches the named branch; **do not `git checkout` other branches**.

## Key conventions

- Streaming via SSE; `ChatOrchestrator.handle_message()` — ChatService is deleted
- All I/O async; model names from `settings.json` / `litellm_config.yaml` — never hardcoded in app logic
- `ModelScheduler` serializes local model swaps; Qdrant down = warning, not fatal

## Verification (builders)

Default gate: **pytest from repo root** (not `web/`). Do not start uvicorn / `npm run dev` unless the task requires live E2E.

```bash
python -m pytest -q --basetemp=.pytest-tmp/run
```

- Baseline on `main`: **240 passed, 2 failed** (known harness issues). **New** failures are regressions.
- Add/update tests when behavior contracts change.
- Git procedure, plan order, completion report: `008-git-discipline.mdc`.

## Roles

| Role | Assigned when | You must |
|------|----------------|----------|
| **Builder** | Default | Plan: pre-flight **first**, pytest+commit **last** (`008`). Stay in FILE SCOPE. One branch per worktree. |
| **Integrator** | User says integrator / audit | Audit branches (`008` integrator section). Merge/push only if user asks. |

**Parallel builders:** one workspace folder = one branch. Other tasks run in other folders (user-created worktrees). Never `git checkout` another task branch in a shared folder.

## Current phase

**Phase 1: Core Platform** — message → route → streamed response; web search toggleable.

## File ownership (Phase 1 task wave)

If the user prompt lists FILE SCOPE, **the prompt wins**. Otherwise defer to this table:

| Owner | Files |
|-------|-------|
| Task 1 (LiteLLM) | `app/config.py`, `pyproject.toml`, `litellm_config.yaml`, `app/model_scheduler.py`, `app/config_validation.py` |
| Task 2a (Embedding) | `app/protocols.py`, `app/types.py`, `app/adapters/embedding_nomic.py` |
| Task 2b (Qdrant) | `app/adapters/qdrant_store.py`, `docker-compose.yml` |
| Task 2c (Search) | `app/adapters/search_ddg.py` |
| Task 3 (Router) | `app/router.py`, `app/adapters/classifier_qwen.py` |
| Task 4 (Orchestrator) | `app/chat_orchestrator.py`, `app/main.py` |
| Task 5 (SSE) | `app/main.py` (extends Task 4) |
| Task 6 (MCP) | `app/tools/web_search.py` |
| Task 7 (Settings) | `app/settings_store.py`, `settings.json`, `web/src/components/SettingsModal.tsx` |
| Task 8 (Frontend) | `web/src/api/client.ts`, `web/src/components/ChatView.tsx`, `web/src/components/MessageBubble.tsx`, new TSX |

### Bug-fix overlap

Split by concern — one branch each; minimal diffs on shared files (`App.tsx`, `app/main.py`, `app/chat_orchestrator.py`).

| Concern | Typical files |
|---------|----------------|
| Nav UI | `App.tsx`, `ProjectSidebar.tsx`, `ProjectGrid.tsx`, `ChatView.tsx` |
| Stop button UI | `ChatView.tsx` |
| Stop / SSE disconnect backend | `app/main.py`, `app/chat_orchestrator.py` |
| UI prefs `localStorage` | `App.tsx` |
| `enabled_tools` backend | `app/schemas.py`, `app/chat_orchestrator.py`, tests |
| Model dropdown | `ModelSelector.tsx`, adapters |
