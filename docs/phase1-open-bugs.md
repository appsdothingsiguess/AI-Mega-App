# Phase 1 — Open Bugs (Handoff)

**Last updated:** 2026-06-06  
**Branch:** `main` (post-merge of `phase1/debug-trace-panel`)  
**Audience:** Agent implementing the remaining Phase 1 gaps

These are the open Phase 1 gaps: **Bugs 1–2** block end-to-end web search and graceful failure UX; **Bugs 3–4** are UI parity gaps vs Claude.ai (navigation + per-reply model label). Kimi model ID (`kimi-k2.6`) is already fixed on `main`.

---

## Bug 1 — Search service not wired at startup

### Symptom

Web search routes correctly and the model may return a `web_search` tool call, but dispatch returns:

```json
{"error": "Search service unavailable"}
```

Or the tool loop never reaches DuckDuckGo because the LLM call fails first (separate from this bug).

### Root cause

`ChatOrchestrator` accepts `search_service: SearchService | None` (default `None`). `_build_services()` in `app/main.py` **never** instantiates or injects a search adapter:

```187:195:app/main.py
    orchestrator = ChatOrchestrator(
        router=router,
        vector_store=vector_store,
        embedding_service=embedding,
        vision_service=None,
        model_scheduler=scheduler,
        settings=settings,
        projects=projects,
    )
```

`DuckDuckGoSearchAdapter` in `app/adapters/search_ddg.py` is implemented and tested; `app/tools/web_search.py` is implemented. Only production wiring is missing.

### Expected behavior

1. On startup, create a search adapter from `settings.search.providers` (e.g. `web_search` → `duckduckgo`).
2. Pass `search_service=...` into `ChatOrchestrator`.
3. When the model calls `web_search`, `web_search_tool.execute()` runs DDG and returns JSON results to the model for a second completion pass.

### Files to touch

| File | Change |
|------|--------|
| `app/main.py` | Import `DuckDuckGoSearchAdapter`, pass `search_service=` in `_build_services()` |
| `tests/` | Add or extend test that `_build_services()` (or app lifespan) provides non-None search service; optional integration test with mocked DDG |

### Config reference

`settings.json`:

```json
"search": {
  "providers": {
    "web_search": "duckduckgo",
    "deep_research": "tavily"
  }
}
```

Tavily is **not** required for this bug — wire DDG for `web_search` only unless you also implement Tavily adapter selection.

### Verification

1. Enable debug trace (`debug.sse_trace` in Settings).
2. Send: *"What's the weather today?"*
3. Debug panel should show `tool_dispatch` with search results JSON, not `"Search service unavailable"`.

---

## Bug 2 — LiteLLM / SSE errors crash the stream silently

### Symptom

Provider failures (auth, unsupported model, rate limit, network) abort the HTTP SSE stream. Server log shows `Exception in ASGI application`. UI often shows an **empty assistant bubble** with no error message.

Example (already fixed for Kimi slug, but same class of failure):

```
litellm.exceptions.AuthenticationError: OpenAIException - Model kimi-k2-6 is not supported
```

### Root cause

**Orchestrator** — `_execute_with_tools()` in `app/chat_orchestrator.py`:

- Catches `LitellmAliasError` at alias resolution → yields `{type: "error"}` ✓
- Does **not** catch exceptions from `litellm.acompletion()` or mid-stream `async for chunk in response`
- Does not guarantee `{type: "done"}` on failure paths

**SSE endpoint** — `event_generator()` in `app/main.py` (~line 810):

- Only catches `asyncio.CancelledError`
- Any other exception propagates → connection drops with no error SSE frame

**Frontend** — `web/src/components/ChatView.tsx`:

- Handles `{type: "error"}` when received ✓
- If stream ends without `done` or `error`, leaves a blank streaming bubble (no fallback message)

### Expected behavior

1. Catch LiteLLM/provider errors in `_execute_with_tools` (and optionally `handle_message`).
2. Log at `prompter.llm` with context (alias, resolved model).
3. Yield `{"type": "error", "message": "<user-safe summary>"}` then `{"type": "done", "usage": {}}`.
4. Optionally wrap `event_generator` in `main.py` as a safety net for uncaught exceptions.
5. (Nice-to-have) Frontend: if stream ends while `isStreaming` without `done`, show "Connection lost" on the bubble.

### Suggested implementation sketch

```python
# chat_orchestrator.py — around litellm.acompletion loop
try:
    response = await litellm.acompletion(...)
    async for chunk in response:
        ...
except Exception as exc:
    logger_llm.exception("LiteLLM completion failed alias=%s", model)
    yield json.dumps({"type": "error", "message": str(exc)})
    yield json.dumps({"type": "done", "usage": {}})
    return
```

Use `litellm.exceptions` types if you want cleaner user messages (don't expose raw API keys).

### Files to touch

| File | Change |
|------|--------|
| `app/chat_orchestrator.py` | try/except around completion + stream; always emit `done` after `error` |
| `app/main.py` | Optional outer catch in `event_generator` |
| `web/src/components/ChatView.tsx` | Optional stream-abort detection |
| `tests/test_chat_orchestrator.py` | Mock `litellm.acompletion` to raise; assert `error` + `done` events, no unhandled exception |

### Verification

1. Temporarily set invalid model in `litellm_config.yaml` or use empty `OPENCODE_API_KEY` for a remote route.
2. Send a message that hits that model.
3. UI should show inline error on assistant bubble; server should not log unhandled ASGI exception.
4. Debug trace may stop mid-flight — that's OK if `error` event is emitted.

---

## Bug 3 — Project-only UI; no Claude.ai-style navigation

### Symptom

On load the app shows **"Select or create a project to start chatting"** — there is no default new chat. Every conversation requires picking a project first. The left sidebar always shows a stacked **Projects + Chats** list and **Sources** below it; the right column always shows **Instructions**. There is no project grid view and no way to start a standalone chat outside a project.

**Claude.ai reference:** App opens to a default new chat; **Projects** is a sidebar nav item that leads to a project grid, then into a project workspace (sources, instructions, project-scoped threads).

### Root cause

**App shell** — `web/src/App.tsx`:

- `selectedProject` and `threadId` initialize to `null`; no default thread or home view.
- Layout is fixed three-column project workspace: `ProjectSidebar` + `SourcesPanel` (left), `ChatView` (center), `InstructionsPanel` (right).
- No view-mode state (e.g. `home | projects | project-workspace`).

**Chat gating** — `web/src/components/ChatView.tsx`:

```386:404:web/src/components/ChatView.tsx
  if (!projectId) {
    return (
      <div style={styles.empty}>
        ...
          Select or create a project to start chatting
        ...
      </div>
    );
  }

  if (!threadId) {
    return (
      <div style={styles.empty}>
        ...
        <div style={styles.emptyText}>Select or create a chat to begin</div>
      </div>
    );
  }
```

**Sidebar** — `web/src/components/ProjectSidebar.tsx`:

- Projects and threads share one sidebar column (projects capped at ~42% height, threads below).
- Selecting a project auto-creates a thread if none exist (`loadThreads` → `createThread`).
- No separate "Projects" nav entry or grid; no global chat list.

**Backend** — all chat routes are project-scoped:

- `POST /api/chat/{project_id}/{thread_id}` in `app/main.py` (~line 796)
- Threads/messages live under `/projects/{project_id}/threads/...`
- No API for a global/default chat outside a project

### Expected behavior

1. **Default landing:** Open app → new empty chat ready to type (Claude-style), not a project picker dead-end.
2. **Sidebar nav:** Primary items such as **Chats** (default) and **Projects** — not a permanent split project/thread list on every screen.
3. **Projects flow:** **Projects** → grid/card view of projects → click one → project workspace (sources, instructions, project threads).
4. **Scoped chats:** Global chats vs project-scoped chats; project workspace retains current sources/instructions/RAG behavior.

### Files to touch

| File | Change |
|------|--------|
| `web/src/App.tsx` | View-mode routing (`home` / `projects-grid` / `project-workspace`); default to new chat on load |
| `web/src/components/ProjectSidebar.tsx` | Nav-first sidebar (Chats / Projects); thread list only inside project workspace |
| New TSX (e.g. `ProjectGrid.tsx`, `HomeChatView.tsx`) | Project grid and standalone chat shell |
| `web/src/components/ChatView.tsx` | Support chat without `projectId` when in home mode (or thin wrapper) |
| `web/src/components/SourcesPanel.tsx`, `InstructionsPanel.tsx` | Show only in project workspace, not on default chat |
| `app/main.py` | Optional: global thread/chat endpoints if standalone chats need persistence |
| `app/project_manager.py` | **Frozen** — if global threads need filesystem layout, coordinate with owner before changing |

### Suggested implementation sketch

```tsx
// App.tsx — high-level state
type AppView = "home-chat" | "projects" | "project";
const [view, setView] = useState<AppView>("home-chat");
const [selectedProject, setSelectedProject] = useState<string | null>(null);

// home-chat: ChatView with no projectId (new ephemeral or global thread)
// projects: ProjectGrid → onSelect → view="project", setSelectedProject
// project: current 3-column workspace (sidebar threads + sources + instructions)
```

Backend option A (minimal): keep project-only persistence; home chat is ephemeral until user moves it into a project. Option B (full parity): add a `_global` or `__default__` project (requires `project_manager` owner approval).

### Verification

1. Fresh load → user can type immediately in a new chat without creating/selecting a project.
2. Sidebar **Projects** → grid of existing projects with create affordance.
3. Open a project → sources, instructions, and project thread list appear; chat is project-scoped.
4. Navigate back to default chat without losing expected Claude-like flow.

---

## Bug 4 — No model name on assistant message bubbles

### Symptom

Assistant replies show only a **timestamp** under the bubble. There is no model label (e.g. `remote/deepseek-v4-pro` or `local/qwen3-8b`). After reload, historical messages also lack model attribution.

**Claude.ai reference:** Model name appears **left of the timestamp** on each AI reply.

### Root cause

**UI** — `web/src/components/MessageBubble.tsx`:

- Assistant footer renders timestamp only; no `model` prop exists.

```101:101:web/src/components/MessageBubble.tsx
        {time && !isStreaming && <div style={styles.timeAssistant}>{time}</div>}
```

**Chat state** — `web/src/components/ChatView.tsx`:

- `StreamingMessage` has no `model` field.
- `toDisplayMessage()` maps only `role`, `content`, `created_at`.
- `MessageBubble` is never passed a model name (lines 432–442).

**API types** — `web/src/api/client.ts`:

```48:52:web/src/api/client.ts
export interface MessageRecord {
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}
```

**Persistence** — `app/project_manager.py` `append_message()` stores `role`, `content`, `created_at`, optional `attachments` — **no model field**.

**Orchestrator** — `app/chat_orchestrator.py`:

- Resolves `model_alias` via router (~line 128) and emits it only in debug `route` events when `sse_trace` is on.
- `model_loading` SSE uses Ollama physical name, not display alias.
- Final `append_message(..., "assistant", content)` (~line 222) does not pass model.
- `done` SSE is `{"type": "done", "usage": {}}` with no model metadata.

### Expected behavior

1. Each assistant bubble shows **model alias** (from `settings.json`) to the **left** of the timestamp, muted/small type — Claude.ai layout.
2. During streaming, set model as soon as route resolves (from SSE debug/route or a dedicated event).
3. **Persist** model on assistant records so reload via `GET .../messages` restores labels.
4. Use alias strings (`local/qwen3-8b`), not raw Ollama/LiteLLM provider IDs — same mapping as `StatusBar` / `ModelSelector`.

### Suggested implementation sketch

```python
# chat_orchestrator.py — after route resolves
yield json.dumps({"type": "routed", "model": model_alias, "intent": intent})

# on persist
self.projects.append_message(project_id, thread_id, "assistant", text, model=model_alias)
```

```tsx
// MessageBubble.tsx — assistant footer
<div style={styles.metaRow}>
  {model && <span style={styles.modelLabel}>{model}</span>}
  {time && <span style={styles.timeAssistant}>{time}</span>}
</div>
```

**Frozen file note:** `project_manager.append_message` must accept and store `model` (or equivalent metadata). That requires a coordinated change — `project_manager.py` is frozen; ask owner or user before editing.

### Files to touch

| File | Change |
|------|--------|
| `web/src/components/MessageBubble.tsx` | `model` prop; meta row with model + timestamp |
| `web/src/components/ChatView.tsx` | Track `model` on `StreamingMessage`; handle `routed`/`done` SSE; pass to bubble |
| `web/src/api/client.ts` | Extend `MessageRecord` and `SseEvent` with optional `model` |
| `app/chat_orchestrator.py` | Emit model in SSE; pass model into `append_message` |
| `app/project_manager.py` | Store optional `model` on message records (**frozen — owner task**) |
| `app/schemas.py` | Extend `MessageRecord` response schema if validated |
| `tests/test_chat_orchestrator.py` | Assert persisted assistant messages include model |

### Verification

1. Send a message routed to a known intent (e.g. general chat → `remote/...`).
2. Assistant bubble shows alias left of time; matches debug trace `model_alias` when trace enabled.
3. Refresh page / re-open thread → model labels still visible on old assistant messages.
4. Override model in header (when wired) → label reflects override for that reply.

---

## Related (out of scope for these bugs)

Documented for context — **do not expand scope** unless the fixing agent agrees with the user:

| Item | Notes |
|------|--------|
| Tool toggles UI ignored | `ChatView` sends `enabled_tools`; backend `MessageCreate` only has `content` — router always assigns tools from intent |
| `file_ops`, `bash`, etc. | Return `not_implemented` from `_dispatch_tool` — Phase 1 MCP incomplete |
| Models returning JSON as text | Model emits tool JSON in `chunk` instead of structured `tool_calls` — prompt/tooling issue, not these two bugs |
| `logs/prompter.log` | Settings UI has file logging toggle; not wired |
| Tavily / `deep_research` | Provider config exists; adapter selection not production-ready |

---

## Architecture reminder

```
User message
  → HybridRouter (intent + tools)
  → ChatOrchestrator.handle_message
  → _build_messages + optional RAG
  → _execute_with_tools
       → litellm.acompletion (with tool schemas)   ← Bug 2 crashes here
       → tool_call SSE → _dispatch_tool
            → web_search → SearchService.search     ← Bug 1 fails here (None)
       → second litellm pass with tool results
  → SSE chunks / done
```

**Rules:** Model aliases from `settings.json`; provider IDs in `litellm_config.yaml`; secrets in `.env` only. Do not modify `app/protocols.py`, `app/types.py`, or `project_manager.py`.

---

## Test baseline

Run before/after fixes:

```bash
pytest
cd web && npm run build
```

Current baseline on `main`: **203 tests passing**.
