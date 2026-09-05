# Pi Plugin Brainstorm — Retiring AI Mega App (except services)

**Date:** 2026-09-05
**Context:** Pi replaces the AI Mega App agent/UI layer. The services layer survives: llama-swap, model roster, GPU management, relay infrastructure, benchmarking. This doc identifies what Pi plugins/extensions to build to bridge Pi to those services and add capabilities Pi lacks.

## What Pi already covers (no plugin needed)

- Agent loop, tool execution (read/write/edit/bash)
- Session management, branching, compaction (via **goosedump**: https://pi.dev/packages/pi-goosedump — may need modification for our use case)
- Provider management (including llama.cpp router mode natively)
- Slash commands, keyboard shortcuts
- Custom tools, UI interaction
- Model selection/switching per session
- **Web access via BrowserOS MCP** — already done, working
- **MCP bridge** via pi-mcp-extension — already done

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

### 3. ~~`pi-memory`~~ — REDUNDANT (goosedump already has durable memory)

**Status: NOT NEEDED as a separate plugin.** Source inspection of goosedump v0.12.62 reveals it already has a full durable memory system built in:

**What goosedump already provides:**
- `goose_remember` — extracts facts from completed exchanges into persistent storage (runs via native binary, not an LLM call through llama-swap)
- `goose_recall` — retrieves memories by query, filterable by type, project-scoped or global, with limit/history options
- `goose_forget` — removes specific memory entries
- `goose_memory_status` — shows storage health and type counts
- `/goose-forget` and `/goose-memory-status` slash commands
- Memory types: `decision`, `fact`, `preference`, `procedure`, `lesson` (with `active`/`superseded` status)
- Project-scoped memory (per `ctx.cwd`) plus cross-project queries via `allProjects` flag
- Memory is opt-in via `enableMemory` setting (default: false)
- Memory extraction is coordinated with compaction via `InferenceCoordinator` — remember and compact don't run simultaneously

**What goosedump's memory does NOT cover (potential extension points if needed):**
- Auto-injection of relevant memories into `before_agent_start` system prompt (goosedump registers the tool routing policy, but doesn't auto-recall relevant memories per prompt)
- Vector similarity search (goosedump uses its native binary's matching, not Qdrant)
- Memory import/export between machines
- Memory sharing across users

**Recommendation:** Enable goosedump's memory (`enableMemory: true`), test it with real workflows, and evaluate the gap before building anything. If auto-injection of relevant memories into context is needed, that's a thin wrapper around `goose_recall` in a `before_agent_start` hook — not a full plugin.

**Key finding for GPU1:** Goosedump's compaction and memory both use a native binary, NOT an LLM call through llama-swap. This confirms utility-gpu's entire purpose (server-side background summarization for AI Mega App) is retired. That frees ~5 GiB on GPU1.

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

### ~~6. `pi-web-tools`~~ — DONE (BrowserOS MCP)

BrowserOS MCP is already working. No plugin needed.

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
3. **GPU1 sub-agent slot** — retire utility-gpu (goosedump uses native binary, not llama-swap for compaction/memory), re-measure coder-small on GPU1 w/ dispatcher only
4. **goosedump tuning** — enable `enableMemory: true`, test with real workflows, evaluate auto-injection gap
5. **pi-debug-trace** — operational visibility; important for debugging model/routing issues
6. **pi-bench** — convenience; not blocking daily use
7. **pi-relay-dashboard** — niche; only matters during relay debugging
8. **pi-project-context** — Pi's existing context files may be sufficient; evaluate gap first

~~pi-web-tools~~ — done (BrowserOS MCP)

## Critical constraint: sub-agents require a second model slot

Pi doesn't ship sub-agents — it's by design ("Pi ships with powerful defaults but skips features like sub agents and plan mode. Instead, you can ask pi to build what you want or install a third party pi package"). A sub-agent extension needs a second model to run alongside the primary model on GPU0.

**The problem:** GPU1 (3070, 8 GiB) currently hosts dispatcher (~1.3 GiB) + utility-gpu (~6.2 GiB) as residents, leaving ~590 MiB free. No room for a third model. GPU0 runs one big model at a time via llama-swap's swap group. So there's no concurrent second model slot for sub-agents.

**Options:**

A. **Kick utility-gpu off GPU1, load a small Qwen model instead.** utility-gpu is the summarizer fast path (~14x CPU decode). Losing it means summaries fall back to CPU utility (~5 tok/s decode). If goosedump handles compaction client-side (Pi's own model does the summarization), we may not need utility-gpu at all — it was built for AI Mega App's server-side background summarization which is being retired. This frees ~5 GiB on GPU1 for a small coding/agent model alongside dispatcher.

B. **Use coder-small (Qwen2.5-Coder-7B) on GPU1.** At Q4 it's ~4-5 GiB. Fits alongside dispatcher (~1.3 GiB) on the 8 GiB 3070. But this contradicts the current placement (coder-small is on GPU0's swap group) and the measured constraint that "coder-small must stay on GPU0, never GPU1" (from the co-residency testing). That constraint was about coder-small + dispatcher + utility-gpu all on GPU1 — without utility-gpu, it might fit. **Needs re-measurement.**

C. **Use an even smaller model.** Qwen3-4B, Qwen3-1.7B, or a small tool-calling model. ~2-3 GiB, easily fits on GPU1 alongside dispatcher. Lower quality but sufficient for sub-agent tasks like file search, simple edits, test running.

D. **CPU-only sub-agent model.** The box has 64 GB RAM and 32 cores. A Q4 7B model runs at ~5-15 tok/s on CPU with 8 threads. Slow but functional for background tasks that don't need interactive speed.

E. **Use the primary model via llama-swap.** Sub-agent calls go to the same model the main agent is using. No concurrent execution but serialized sub-agent turns work if the sub-agent tasks are short. This is the zero-hardware-cost option.

**Recommendation:** Option A is most promising. If goosedump owns compaction, utility-gpu's purpose (server-side background summarization) is retired. Free that GPU1 slot for a small sub-agent model. Re-measure coder-small on GPU1 alongside dispatcher-only. If it fits, that's 112 tok/s coding capability available as a sub-agent while the main model runs on GPU0.

**What sub-agents would do:**
- Parallel file search/analysis (grep + read + summarize)
- Run tests in background while main agent continues
- Code review of a diff while main agent works on next task
- RAG retrieval + context assembly

This is the key unlock for making Pi competitive with Claude Code's sub-agent architecture on local hardware.

## Open questions

1. **Mono-repo or separate repos?** Mono-repo (like pi itself) is simpler for cross-plugin types but heavier to install individually.
2. **Where does the classifier model live?** If Pi talks to llama-swap, the classifier is already running as a CPU resident. The router extension just needs to call it via the llama-swap endpoint. But should we use Pi's own model for classification instead (avoid an extra HTTP call)?
3. **Memory storage location?** `~/.pi/agent/memory.db` (global) vs `.pi/memory.db` (per-project) vs a central SQLite on ailab (survives machine changes). Probably global + per-project, like Pi's own extension locations.
4. **Do we need the AI Mega App web UI at all?** Pi's TUI covers coding. But a web UI for non-terminal users (phone, tablet) or for sharing sessions could still have value. Pi's experimental RPC mode + a web frontend is the path if needed.
5. ~~**BrowserOS integration**~~ — done.
6. **Goosedump modifications:** Source inspection shows goosedump uses a native binary for both compaction and memory — it does NOT call an LLM through llama-swap at all. This means: (a) utility-gpu can be retired immediately, freeing ~5 GiB on GPU1; (b) compaction/memory quality depends on the native binary's built-in model, not our local models; (c) the open question is whether goosedump's native compaction quality is good enough for our use case, or if we need to modify it to use llama-swap models instead. Test with real sessions before deciding.
7. **Sub-agent architecture:** Pi doesn't have sub-agents natively. Building a sub-agent extension requires: a second model slot (GPU1), a way to spawn parallel agent loops, and a protocol for the main agent to delegate tasks. This is likely the hardest plugin to build but the highest-impact for coding productivity.
