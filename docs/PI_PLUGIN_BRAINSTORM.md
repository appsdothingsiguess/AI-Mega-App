# Pi Plugin Brainstorm — Retiring AI Mega App (except services)

**Date:** 2026-09-05
**Context:** Pi replaces the AI Mega App agent/UI layer. The services layer survives: llama-swap, model roster, GPU management, relay infrastructure, benchmarking. This doc identifies what Pi plugins/extensions to build to bridge Pi to those services and add capabilities Pi lacks.

## What Pi already covers (no plugin needed)

- Agent loop, tool execution (read/write/edit/bash)
- Session management, branching, compaction
- Provider management (including llama.cpp router mode natively)
- Slash commands, keyboard shortcuts
- Custom tools, UI interaction
- Model selection/switching per session

## What AI Mega App has that Pi doesn't

| AI Mega App capability | Status | Pi equivalent |
|---|---|---|
| Smart router (override → keyword rules → classifier) | Proven, 91.76% accuracy | None — Pi uses manual `/model` selection |
| llama-swap orchestration (groups, GPU pinning, swapgen) | Live, critical | Pi has llama.cpp router mode but not llama-swap's group semantics |
| Hermes-style fact memories (extract → store → retrieve) | Built, partial | None |
| RAG (SQLite FTS5 + Qdrant vectors) | Built | None |
| Background summarization (token-pressure triggered) | Built, buggy but functional | Pi has compaction, different design |
| Debug trace/span system | Built | None — Pi has session entries but no structured tracing |
| GPU inventory + config generation | Built | None |
| Relay infrastructure (pi-capture-relay) | Live | N/A — network topology concern |
| Benchmark harnesses | Live, valuable | None |
| Web UI (chat, debug panel, settings) | Built | Pi has TUI; web via experimental RPC |

---

## Plugin candidates — priority order

### 1. `pi-llama-swap` — llama-swap provider + model management

**What:** A Pi extension that registers llama-swap as a provider with full group-aware model management.

**Why first:** This is the foundational bridge. Pi's built-in llama.cpp support assumes router mode (single server, `--models-dir`). Our setup uses llama-swap managing multiple llama-server processes with group semantics (resident CPU models, GPU0 swap slot, GPU1 residents). Without this, Pi can't use our infrastructure correctly.

**Capabilities:**
- `registerProvider("llama-swap", ...)` with `refreshModels` that queries llama-swap's `/v1/models` and maps aliases to the config.yaml roster
- `/llama-swap` command showing loaded/unloaded models, GPU memory, active group
- Model metadata from config.yaml: context windows, GPU placement, reasoning capability, vision support
- Automatic model-specific parameters (reasoning budget, temperature, thinking mode) based on alias role
- Health check on session start, notify if llama-swap is down

**Doesn't need:** swapgen (stays a server-side script), GPU inventory (stays server-side), config editing (stays manual). The extension is read-only against the infrastructure.

**Complexity:** Medium. Mostly a provider registration with `refreshModels` + a command.

---

### 2. `pi-smart-router` — Automatic model selection per prompt

**What:** An extension that intercepts `before_agent_start` or `input` events and selects the best model from the roster based on the prompt content.

**Why:** Pi's model selection is manual (`/model` or Ctrl+L). AI Mega App's router automatically picks `chat-default` vs `coder` vs `reasoner` vs `vision` based on the prompt. This is the single biggest UX gap — users shouldn't have to manually switch models for different tasks.

**Design options:**

A. **Rule-based only (start here):** Keyword detection + attachment analysis. Code keywords → `coder`, image attachments → `vision`, "think through" / "reason" / "prove" → `reasoner`, everything else → `chat-default`. No classifier model needed. Deterministic, zero-latency.

B. **Classifier-assisted (later):** Use the `classifier` model (CPU-resident, already in the roster) via a tool call or direct HTTP to llama-swap. Returns `{class, confidence}` with the 6 taxonomy categories. Falls back to rule-based on timeout/error.

C. **Pi-native approach — thinking level as routing proxy:** Instead of switching models, map task type to thinking level: code tasks get `high` thinking, chit-chat gets `off`, reasoning gets `max`. This works if all tasks route through the same base model (Qwen3.8). Simpler than model switching but loses the coder-small/vision specialization.

**Recommendation:** Start with A (rules), add B when the classifier model is proven stable. C is complementary — use thinking level AND model selection together.

**Implementation:**
- `pi.on("before_agent_start", ...)` inspects prompt text + images
- Calls `pi.setModel(...)` or `pi.setThinkingLevel(...)` based on classification
- Records decision in a custom entry for debugging
- `/route` command to show last routing decision and override
- Respects manual model override (if user picked a model, don't auto-switch)

**Complexity:** Low for rules, medium for classifier integration.

---

### 3. `pi-memory` — Hermes-style persistent fact memory

**What:** An extension that extracts, stores, and retrieves facts across sessions.

**Why:** Pi sessions are ephemeral — compaction summarizes but doesn't extract structured facts. AI Mega App's hermes-style memory extracts key facts, preferences, and decisions into a persistent store that survives session boundaries. This is critical for a personal AI that learns about your projects and preferences.

**Design:**
- `tool_result` / `agent_end` event hooks to extract facts from conversation
- SQLite store in `~/.pi/agent/memory.db` (or project-local `.pi/memory.db`)
- Fact extraction via the utility model (small, CPU-resident) or the active model itself
- `before_agent_start` hook injects relevant memories into system prompt
- Retrieval: keyword match (FTS5) + optional vector similarity if we add embedding
- `/memory` command: list, search, forget, export

**Memory types (from AI Mega App's design):**
- User preferences ("prefers snake_case", "uses pytest not unittest")
- Project facts ("uses FastAPI", "SQLite for storage", "deployed on ailab")
- Decisions ("chose Qdrant over sqlite-vec because p95 latency")
- Corrections ("the model name is Qwen3.8, not Qwen3.6")

**Risk:** Extraction quality depends heavily on the model doing the extraction. The AI Mega App summarizer had lossy problems (dropped numbers, collapsed indexed lists). Start conservative: extract only explicit user statements ("remember that...", "note that..."), not implicit facts.

**Complexity:** High. This is a real feature, not just plumbing.

---

### 4. `pi-relay-dashboard` — Capture relay monitoring

**What:** A command/tool extension for monitoring the pi-capture-relay traffic.

**Why:** The relay captures prompt-bearing traffic between Windows Pi and llama-swap. Currently monitoring requires SSH + journalctl + browsing `/tmp/pi-request-captures/`. An extension could surface this in Pi itself.

**Capabilities:**
- `/relay` command: show relay status, recent captures, error rate
- Tool for Pi to inspect captured requests (useful for debugging model behavior)
- Notification on relay errors or upstream failures

**Complexity:** Low. Mostly SSH/HTTP calls to ailab.

---

### 5. `pi-bench` — Benchmark harness integration

**What:** Commands to trigger and view benchmark results from the existing harness scripts.

**Why:** The bench_server, bench_context_depth, bench_sweep, and eval scripts are all CLI tools that require SSH + manual invocation. Wrapping them as Pi commands makes model evaluation accessible from any Pi session.

**Capabilities:**
- `/bench server <model> <ctx>` — trigger bench_server.py
- `/bench sweep <profile> --matrix ...` — trigger bench_sweep.py
- `/bench results` — show latest results from logs/benchmarks/
- Tool for Pi to read benchmark JSONL and summarize results

**Complexity:** Medium. Process management + result parsing.

---

### 6. `pi-web-tools` — Web search and fetch

**What:** Tools for web search and page fetching, layered: lightweight direct fetch for public docs, BrowserOS MCP for JS-heavy/auth pages.

**Why:** Pi has no built-in web access. The HANDOFF.md extension plan (item 3) identifies this as priority after memory and MCP bridge. Two layers avoid over-relying on BrowserOS for simple lookups.

**Design:**
- `web_search` tool: query → search API (SearXNG, Brave, or similar) → ranked results
- `web_fetch` tool: URL → clean text extraction (readability algorithm)
- Both return concise, source-linked results (not raw HTML dumps)
- BrowserOS escalation path for pages that need JS rendering or auth

**Complexity:** Medium. Search API integration + content extraction.

---

### 7. `pi-debug-trace` — Structured debug tracing

**What:** An extension that adds AI Mega App-style debug tracing to Pi sessions.

**Why:** AI Mega App's debug system records per-turn traces with spans for each pipeline stage (route, completion, tool execution, summary) including timing, token counts, and model-reported metrics. Pi has none of this — session entries show conversation but not the operational telemetry.

**Design:**
- Hook into `turn_start/end`, `tool_execution_start/end`, `before_provider_request`, `after_provider_response`
- Record spans with timing, token usage, model identity
- Store in a trace DB or as custom session entries
- `/debug` command to inspect recent traces
- `/debug trace <id>` for detailed span waterfall

**Complexity:** Medium-high. Lots of event hooks, storage design.

---

### 8. `pi-project-context` — Project-aware context injection

**What:** Automatic project context discovery and injection, similar to AI Mega App's project system.

**Why:** Pi has AGENTS.md/context files, but AI Mega App had richer project awareness: project-specific instructions, file-tree summaries, dependency analysis, and per-project model preferences. This extension would make Pi smarter about the project it's working in.

**Design:**
- `session_start` hook reads project markers (package.json, pyproject.toml, Cargo.toml, etc.)
- Injects relevant context into `before_agent_start` system prompt
- Project-specific model preferences (e.g., "this Python project prefers coder-small")
- `/project` command to view/edit project context

**Complexity:** Medium.

---

## Packaging strategy

Pi Packages are the distribution unit. Each plugin above should be a separate Pi Package installable via `pi install`. Development workflow:

```
earendil-works/pi-plugins/
├── packages/
│   ├── pi-llama-swap/
│   │   ├── package.json      # pi: { extensions: ["./src/index.ts"] }
│   │   └── src/index.ts
│   ├── pi-smart-router/
│   ├── pi-memory/
│   └── ...
```

Install: `pi install git:github.com/earendil-works/pi-plugins#packages/pi-llama-swap`

Or as npm packages: `pi install npm:@earendil-works/pi-llama-swap`

---

## What stays in AI Mega App (services layer)

These are NOT plugins — they stay as server-side infrastructure on ailab:

- **llama-swap.service** + config.yaml + swapgen.py — model serving
- **pi-capture-relay.service** — Windows→Ubuntu relay with capture
- **qwen36-ngram.service** — isolated worker mode
- **Benchmark scripts** (bench_server, bench_sweep, bench_context_depth, eval_*)
- **Ops scripts** (trace_inspect, incident_snapshot, model_state, config_drift_check)
- **GPU inventory** + nvidia-smi integration
- **Qdrant** vector store (if memory plugin uses it)

The web UI, FastAPI backend, chat orchestrator, and frontend TypeScript are retired. Their patterns inform the plugins but no code is carried forward.

---

## Implementation order

1. **pi-llama-swap** — must work before anything else; validates the Pi→llama-swap bridge
2. **pi-smart-router** — rules-based first; makes the system usable without manual `/model`
3. **pi-web-tools** — high daily utility; unblocks research tasks
4. **pi-memory** — high value but high complexity; start with explicit "remember X" before auto-extraction
5. **pi-debug-trace** — operational visibility; important for debugging model/routing issues
6. **pi-bench** — convenience; not blocking daily use
7. **pi-relay-dashboard** — niche; only matters during relay debugging
8. **pi-project-context** — Pi's existing context files may be sufficient; evaluate gap first

## Open questions

1. **Mono-repo or separate repos?** Mono-repo (like pi itself) is simpler for cross-plugin types but heavier to install individually.
2. **Where does the classifier model live?** If Pi talks to llama-swap, the classifier is already running as a CPU resident. The router extension just needs to call it via the llama-swap endpoint. But should we use Pi's own model for classification instead (avoid an extra HTTP call)?
3. **Memory storage location?** `~/.pi/agent/memory.db` (global) vs `.pi/memory.db` (per-project) vs a central SQLite on ailab (survives machine changes). Probably global + per-project, like Pi's own extension locations.
4. **Do we need the AI Mega App web UI at all?** Pi's TUI covers coding. But a web UI for non-terminal users (phone, tablet) or for sharing sessions could still have value. Pi's experimental RPC mode + a web frontend is the path if needed.
5. **BrowserOS integration:** Should this be a separate plugin or part of pi-web-tools? Separate is cleaner (BrowserOS is Windows-only, heavy dependency) but means two plugins for "web access."
