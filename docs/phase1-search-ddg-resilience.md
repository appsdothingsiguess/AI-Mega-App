# Phase 1 — Search Resilience (DuckDuckGo Rate Limits)

**Last updated:** 2026-06-06  
**Branch:** `main` (post-merge of `phase1/bug1-search-wiring`)  
**Audience:** Agent implementing web search reliability  
**Related:** [phase1-open-bugs.md](phase1-open-bugs.md) — Bug 1 (startup wiring) is **fixed**; this doc covers the remaining search gap.

---

## Status

| Issue | State |
|-------|--------|
| Bug 1 — `search_service` not wired at startup | **Fixed** — `DuckDuckGoSearchAdapter` injected in `_build_services()` |
| DDG `202 Ratelimit` → empty results → model retry storm | **Open** |

---

## Symptom

Web search is wired and the model calls `web_search`, but results are empty and the assistant says search is unavailable or could not find current information.

**Server log (typical):**

```
Impersonate 'chrome_124' does not exist, using 'random'
DuckDuckGo search failed for query='current weather Orange County CA' after 903.0ms:
  https://links.duckduckgo.com/d.js?... 202 Ratelimit
```

**Debug trace (`debug.sse_trace`):**

```json
{
  "name": "web_search",
  "arguments": "{\"query\":\"current weather Orange County CA\"}",
  "result": "[]"
}
```

On rate-limited turns, Kimi may issue **multiple** `web_search` tool calls with rephrased queries in a **single** chat request — each failure adds more DDG traffic.

---

## Root cause

### 1. Single-path DDG adapter with silent failure

[`app/adapters/search_ddg.py`](../app/adapters/search_ddg.py) uses `AsyncDDGS().atext()` with default `backend="api"`, which hits:

1. `GET https://duckduckgo.com` (VQD token)
2. `GET https://links.duckduckgo.com/d.js` (JSON API)

On `DuckDuckGoSearchException` / `202 Ratelimit`, the adapter logs a warning and returns **`[]`** — indistinguishable from “query had no hits.”

### 2. Tool loop amplifies rate limits

[`app/chat_orchestrator.py`](../app/chat_orchestrator.py) `_execute_with_tools()` allows up to **5 iterations**. When the model receives `"[]"`, it often calls `web_search` again with a variant query. Each call costs ~2 DDG HTTP requests. Repeated user retries (same weather question across threads) add further load.

### 3. No search result cache

Every tool invocation hits DDG fresh. Near-duplicate queries during debugging or model retries do not dedupe.

### 4. No provider fallback for `web_search`

`settings.json` maps `deep_research` → `tavily`, but `web_search` has no fallback chain when DDG fails. Tavily is not consulted on DDG rate limit.

### Architectural comparison (design lessons only)

Production-grade search stacks typically layer:

| Layer | Purpose |
|-------|---------|
| **In-adapter recovery** | When `d.js` fails, retry via `backend="html"` or `html.duckduckgo.com/html/` in the **same** tool call — model never sees empty due to rate limit |
| **Result cache** | Dedupe near-identical queries (TTL: shorter for news/current-events queries) |
| **Provider fallback** | Primary DDG → optional Tavily/Brave/SearXNG when all DDG paths fail |
| **Explicit error to model** | Distinguish `rate_limited` from `no_results` so the model does not retry blindly |

Prompter currently has **none** of these layers.

**Note:** `AsyncDDGS.atext()` is a thin wrapper around sync `DDGS.text()` (same library, same endpoint). Sync vs async is not the fix.

---

## Expected behavior

1. When `d.js` returns `202 Ratelimit`, adapter tries at least one alternate DDG path (`backend="html"` or lite) before giving up.
2. On total DDG failure, return structured JSON to the model, e.g. `{"error":"rate_limited","provider":"duckduckgo"}` — not bare `[]`.
3. Optional: cache successful search results keyed by `(query, max_results)` with TTL from query type (news vs reference).
4. Optional: provider fallback from `settings.search.providers` / a new fallback chain for `web_search`.
5. Model should receive usable results or a clear error — not trigger a multi-call retry storm.

---

## Suggested implementation (Prompter-native)

### Change A — `app/adapters/search_ddg.py` (required)

- On library failure or empty API results, retry with `backend="html"` (library) or a minimal httpx HTML scrape fallback.
- Return structured error dict serialized as JSON when all paths fail (coordinate with `web_search_tool.execute()` return shape).
- Log which path succeeded (`api`, `html`, `lite`) at `prompter.search` INFO.

### Change B — `app/tools/web_search.py` (optional)

- Pass through structured errors instead of always wrapping as result list.
- Consider default `max_results=5` (already set) — avoid pagination (`max_results > 10` triggers extra `d.js` pages in the library).

### Change C — Search cache (optional, new module or adapter wrapper)

- Disk or in-memory cache under `data/` or `settings`-driven TTL.
- Key: hash of normalized query + max_results.

### Change D — Provider fallback (optional, larger scope)

- Extend `settings.search` with fallback chain for `web_search`.
- Wire Tavily adapter selection when DDG exhausted (reuse `deep_research` provider config pattern).

### Change E — Orchestrator (optional)

- On `rate_limited` tool result, do not encourage further `web_search` calls in the same turn (or cap to 1 DDG attempt per user message).

---

## Files to touch

| File | Change |
|------|--------|
| `app/adapters/search_ddg.py` | HTML/lite fallback; structured errors; path logging |
| `app/tools/web_search.py` | Error shape passthrough if needed |
| `tests/test_search_ddg.py` | Mock rate limit → assert HTML fallback or structured error |
| `tests/test_tool_web_search.py` | Assert rate-limit path does not return bare `[]` |
| `settings.json` | Optional: fallback chain keys (owner: Settings task) |
| `app/config.py` | Optional: validate new search settings |

**Do not modify:** `app/protocols.py`, `app/types.py`, `app/project_manager.py` unless a coordinated type change is approved.

---

## Verification

1. Restart server after changes.
2. Enable `debug.sse_trace` in Settings.
3. Send: *"What's the weather in Orange County CA today?"*
4. **Pass:** debug `tool_dispatch` shows non-empty results JSON **or** explicit `rate_limited` error (not `[]`).
5. **Pass:** server log shows fallback path attempt when `d.js` fails.
6. **Pass:** single user message does not produce 3+ DDG HTTP bursts unless results were returned.
7. Run `pytest` — all tests pass; new tests cover rate-limit recovery.

---

## Debug logging reminder

Two separate systems (see [phase1-open-bugs.md](phase1-open-bugs.md) architecture notes):

| System | Config | Destination |
|--------|--------|-------------|
| **SSE debug trace** | `debug.sse_trace` | UI panel only (in-memory) |
| **Python loggers** | `prompter.search`, etc. | Server stderr; `logging.file_enabled` not wired to disk yet |

DDG rate-limit failures appear in **`prompter.search` warnings on stderr**, not in the debug trace panel unless `tool_dispatch` captures the tool return value.

---

## Out of scope

- Copying code from external reference trees (see `.cursor/rules/009-no-ref-do-not-copy.mdc`).
- Tavily adapter for `deep_research` (separate task).
- SearXNG integration (Phase 2+ unless explicitly scoped).
