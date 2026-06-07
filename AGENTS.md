# Prompter X — Agent Entry Point

**Architecture spec (source of truth):** `prompter_x_complete_spec.md`
**Cursor prompts:** `phase1_cursor_prompts.md`

## What This Is

Personal AI platform replacing Claude.ai (web) with intent routing, local/remote model management, and deep research. Built on an existing FastAPI + React/Vite + filesystem projects codebase.

## Stack

Python 3.12, FastAPI (async), React 18 / Vite / TypeScript, LiteLLM, Ollama (Docker), Qdrant (Docker).

## Frozen Contracts — Read Only, Never Modify

- `app/protocols.py` — All service Protocol interfaces
- `app/types.py` — Shared data types (SearchResult, ClassifierOutput, RouteSource, RouteResult, ToolCallDelta)
- `app/project_manager.py` — Project CRUD, threads, filesystem structure
- Intent labels: `general_chat | web_search | deep_research | coding_basic | coding_advanced | bash | pdf_gen | file_ops | vision`

## Config Architecture

| File | Contains | Secrets? |
|------|----------|----------|
| `settings.json` | Structured config (models, router, embedding, search, infra) | No |
| `.env` | API keys only (OPENCODE_API_KEY) | Yes |
| `litellm_config.yaml` | Model alias → endpoint routing | No |

## Reference Material (blacklisted)

The folder `ref_do_not_copy/` is **excluded from agent context** (`.cursorignore`, `.gitignore`) and **must not be read or copied** — see `.cursor/rules/009-no-ref-do-not-copy.mdc`. Use in-repo docs (e.g. `docs/phase1-search-ddg-resilience.md`) for design handoffs instead.

## Key Rules

- **Git:** Each task uses branch `phase1/<task-name>`, commits are mandatory at task completion — see `.cursor/rules/008-git-discipline.mdc`. Do not wait for the user to ask.
- All model names resolved from settings.json at runtime — never hardcoded
- All I/O methods are async
- Streaming via SSE is the primary response path
- ChatOrchestrator replaces ChatService (deleted)
- ModelScheduler serializes local model swaps behind asyncio lock
- Qdrant unavailability is a warning, not an error — app degrades gracefully

## Current Phase

**Phase 1: Core Platform** — Send a message → route to correct model → streamed response → web search toggleable.

## File Ownership During Phase 1

| Owner | Files |
|-------|-------|
| Task 1 (LiteLLM) | `app/config.py`, `pyproject.toml`, `litellm_config.yaml`, `app/model_scheduler.py`, `app/config_validation.py` |
| Task 2a (Embedding) | `app/protocols.py`, `app/types.py`, `app/adapters/embedding_nomic.py` |
| Task 2b (Qdrant) | `app/adapters/qdrant_store.py`, `docker-compose.yml` |
| Task 2c (Search) | `app/adapters/search_ddg.py` |
| Task 3 (Router) | `app/router.py`, `app/adapters/classifier_qwen.py` |
| Task 4 (Orchestrator) | `app/chat_orchestrator.py`, `app/main.py` |
| Task 5 (SSE) | `app/main.py` (extends Task 4's work) |
| Task 6 (MCP) | `app/tools/web_search.py` |
| Task 7 (Settings) | `app/settings_store.py`, `settings.json`, `web/src/components/SettingsModal.tsx` |
| Task 8 (Frontend) | `web/src/api/client.ts`, `web/src/components/ChatView.tsx`, `web/src/components/MessageBubble.tsx`, new TSX components |
