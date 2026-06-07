# Phase 1 — Local Model Tool Loop Fix

**Last updated:** 2026-06-06  
**Branch:** `phase1/local-tools-integration`  
**Audience:** Debugging web-search turns with local Ollama models  
**Related:** [phase1-open-bugs.md](phase1-open-bugs.md) (models returning JSON as text), [phase1-search-ddg-resilience.md](phase1-search-ddg-resilience.md)

---

## Symptom

User asks a live-fact question (e.g. *"whats the weather in LA for tomorrow"*) with a **local model override** (`local/qwen2.5-coder-7b`). The UI shows an **empty assistant bubble** (model label + timestamp only). Debug trace or `logs/prompter.log` shows **multiple identical `web_search` dispatches** before the turn ends.

**Typical log pattern (`prompter.llm` at DEBUG):**

```
LLM response iteration=1 text= tool_calls=[web_search] fallback_used=True
LLM response iteration=2 text= tool_calls=[web_search] fallback_used=True
...
LLM response iteration=5 text= tool_calls=[web_search] fallback_used=True
```

Each iteration triggers `prompter.search` with the same query. No answer text is streamed to the client.

---

## Root cause

### 1. Local models emit tool JSON as plain text

Ollama models such as `qwen2.5-coder` and `qwen3-8b` often write:

```json
{"name": "web_search", "arguments": {"query": "LA weather tomorrow"}}
```

into `delta.content` instead of structured `delta.tool_calls`. The orchestrator's **text-JSON fallback** (`_extract_tool_calls_from_text`) correctly turns this into a dispatchable tool call.

### 2. Model never produces a final answer

After tool results are appended to the message history, the same model **calls the same tool again** with identical arguments instead of summarizing search results. Each loop iteration has `text=` empty and `fallback_used=True`.

### 3. Deferred streaming hides the failure

When tools are enabled, `_execute_with_tools` sets `defer_content=True` so raw tool JSON is not streamed to the user. Chunks are only yielded when the model stops calling tools. If it never stops, **zero `chunk` events** reach the UI.

### 4. Iteration cap ends the turn without text

The tool loop allows `max_iterations=5`. When exhausted, an error event is emitted (`Tool loop hit 5 iteration limit`), but the assistant message body remains empty.

### Contributing factor: project instructions

Default `__home__` project instructions still say *"Prefer answers grounded in files under `docs/`"*, which can reinforce doc-only behavior on local models even when `web_search` is routed. Platform prompt (`settings.assistant.system_prompt`) overrides this for tool turns, but per-project instructions below it may still add noise.

---

## Fix (2026-06-06)

Implemented in [`app/chat_orchestrator.py`](../app/chat_orchestrator.py):

### Single tool round, then synthesis

1. **`tools_round_complete` flag** — after the first tool dispatch round, subsequent LLM calls pass `tools=None` to LiteLLM so the model cannot register another structured tool call.

2. **Ignore post-round tool requests** — if the model still emits tool JSON (text fallback or structured deltas) after the tool round, those calls are dropped and logged at WARNING on `prompter.llm`.

3. **Synthesis pass** — when the tool round finishes and the model returns no answer text, `_stream_final_answer()` runs a text-only completion with a one-shot nudge:
   > Using the tool results above, answer the original question in plain text. Do not call any tools or output JSON.

   The nudge is appended only to the ephemeral API message list; it is **not** persisted to thread history.

4. **Fallback on max iterations** — if the loop hits the iteration limit after tools were used but synthesis has not run yet, synthesis is attempted once before surfacing the iteration-limit error.

### Behavior after fix

| Step | What happens |
|------|----------------|
| 1 | Model calls `web_search` (structured or text-JSON fallback) |
| 2 | DDG results appended to in-request messages |
| 3 | Tools disabled; model asked to answer from results |
| 4 | If still no text → synthesis pass streams answer chunks |
| 5 | If synthesis also fails → error: `Model did not produce an answer after tool results.` |

---

## How to verify

1. Enable debug: `settings.json` → `debug.sse_trace: true`, `logging.subsystems.llm: true`.
2. Override model to `local/qwen2.5-coder-7b` (or `local/qwen3-8b`).
3. Send: *"whats the weather in LA for tomorrow"*.
4. Expect in logs / Debug Trace:
   - **One** `prompter.mcp` / `prompter.search` dispatch for `web_search`
   - `llm_request` with `"synthesis": true` (if synthesis runs)
   - `chunk` events with answer text in the UI
5. Run tests:

```bash
pytest tests/test_chat_orchestrator.py -k "tool_loop or repeated_tool or text_json"
```

Key tests: `test_repeated_tool_json_triggers_single_search_then_synthesis`, `test_tool_loop_limit`, `test_text_json_tool_call_fallback`.

---

## Operational notes

| Topic | Guidance |
|-------|----------|
| **Best model for web_search** | Default `remote/kimi-k2-6` handles tool→answer loops natively. Use local override to test or when offline. |
| **Where to watch** | In-app **Debug trace** panel; `logs/prompter.log` (`prompter.llm`, `prompter.mcp`, `prompter.search`); `GET /debug/last-turn` (metadata only). |
| **Empty bubble still?** | Check synthesis error in bubble footer; confirm Ollama model is loaded; try a non-coder local model (`local/qwen3-8b`). |

---

## Files changed

| File | Change |
|------|--------|
| `app/chat_orchestrator.py` | `tools_round_complete`, `_stream_final_answer()`, synthesis nudge |
| `tests/test_chat_orchestrator.py` | `test_repeated_tool_json_triggers_single_search_then_synthesis`; updated `test_tool_loop_limit` |
