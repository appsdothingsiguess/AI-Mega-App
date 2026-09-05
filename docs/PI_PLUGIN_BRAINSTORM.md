# Pi Plugin Brainstorm — Retiring AI Mega App (except services)

**Date:** 2026-09-05
**Context:** Pi replaces the AI Mega App agent/UI layer. The services layer survives: llama-swap, model roster, GPU management, relay infrastructure, benchmarking. This doc identifies what Pi plugins/extensions to build to bridge Pi to those services and add capabilities Pi lacks.

## Pick the cheapest tier that works (added 2026-09-05)

Pi has four customization tiers; from `usage.md`: *"Pi keeps the core small and pushes workflow-specific behavior into extensions, skills, prompt templates, and packages."* Reach for them in this order — an **extension** is only justified by event hooks, custom tools, or custom TUI:

1. **Prompt template** (`~/.pi/agent/prompts/*.md`) — reusable prompts, `$1`/`$@`/`${1:-default}`, `argument-hint` autocomplete. Pi's own repo uses `/wr`, `/cl`, `/pr` this way. Right tier for: code review, PR review, changelog audit.
2. **Skill** (`SKILL.md` + scripts, progressive disclosure) — right tier for: design (see below), test runner, doc generation, worktree/git discipline.
3. **Extension** (TypeScript, event hooks) — right tier for: llama-swap group-aware provider, thinking-level router, sub-agents.
4. **Package** — bundle any of the above for distribution.

Several brainstorm entries below were originally specced one tier too high. Only `pi-llama-swap`, the thinking-level router, and sub-agents genuinely need extension tier.

### Design capability → skills, not a plugin (open-design)

A "Claude Design mimic" does NOT need a frontier model or an extension — Claude Code's design capabilities are **skills** (markdown instruction files), which Pi loads natively. [`nexu-io/open-design`](https://github.com/nexu-io/open-design) is an **Apache-2.0** ("open-source Claude Design alternative") repo containing **162 `SKILL.md` skills** + 154 brand design-system packages. Verified 2026-09-05: all 162 pass Pi's frontmatter rules (valid name + description); only 4 of 162 reference the daemon/MCP. Adopt as one settings line, skip the Electron/daemon/MCP layer:

```json
{ "skills": ["/path/to/open-design/skills"] }
```

Caveats: (1) skill *quality* is model-bound — output depends on Qwen3.8's design reasoning, so test a few before adopting all 162; (2) `artifacts-builder`-style skills that target claude.ai's Artifact tool won't work in Pi. Same mechanism works for `~/.claude/skills` (docx/pdf/pptx/xlsx are portable; those are Proprietary-licensed so local-use only, not bundling).

## What Pi already covers (no plugin needed)

- Agent loop, tool execution (read/write/edit/bash)
- Session management, branching, **compaction + durable memory (via goosedump — already has remember/recall/forget, 5 typed claims; makes `pi-memory` redundant, see §3)**
- Provider management (including llama.cpp router mode natively)
- Slash commands, keyboard shortcuts, **prompt templates, skills**
- Custom tools, UI interaction
- Model selection/switching per session (`--models` + Ctrl+P, `/scoped-models`), **`/thinking` levels (off→max, free — no swap)**
- **Context files** — walks up dirs loading `AGENTS.md`/`CLAUDE.md` + `.pi/SYSTEM.md` + `APPEND_SYSTEM.md` (makes `pi-project-context` §8 largely redundant)
- **Token/cost/context usage** shown in footer; `/session` totals; `--mode json` structured event stream (weakens the `pi-debug-trace` §7 case)
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

**Why:** Pi's model selection is manual (`/model` or Ctrl+L). AI Mega App's router automatically picks `chat-default` vs `coder` vs `reasoner` vs `vision` based on the prompt.

**Reassessment (2026-09-05) — mostly not worth building:** That router was built for a *chat* app where consecutive prompts genuinely varied (chit-chat → code → reasoning). Pi is a *coding agent*: you're in a repo, on a task, and nearly every turn is a code task. The classification distribution is close to degenerate, so a 91.76%-accurate classifier solves a problem we no longer have. The cost is worse: GPU0 holds one big model at a time, so every route change between big models is an unload + cold load (`chat-default` cold-loads in **12.47s**, warm 0.67s — Phase-0 `docs/phase0-measurements.md`) and discards the KV cache, forcing a full reprefill of system prompt + `AGENTS.md` + skills + tool history. Auto-routing would make Pi feel *slower* than manual `/model`, because a human switches a few times a day while a classifier switches on prompt phrasing. Pi also already ships `--models` + Ctrl+P cycling and `/scoped-models` for cheap manual switching.

**What survives: option C (thinking-level routing), which is free.** `/thinking` (off→max) changes no weights, triggers no swap, costs nothing. Per the 2026-08-24 finding, reasoning-off cut a matched workflow 205.5s→63.8s (3.2x) — a bigger lever than model selection. Map task shape to thinking level (off for trivial edits, high for debugging, max for architecture) and skip the model-switching classifier entirely.

**Design options:**

A. **Rule-based only (start here):** Keyword detection + attachment analysis. Code keywords → `coder`, image attachments → `vision`, "think through" / "reason" / "prove" → `reasoner`, everything else → `chat-default`. No classifier model needed. Deterministic, zero-latency.

B. **Classifier-assisted (later):** Use the `classifier` model (CPU-resident, already in the roster) via a tool call or direct HTTP to llama-swap. Returns `{class, confidence}` with the 6 taxonomy categories. Falls back to rule-based on timeout/error.

C. **Pi-native approach — thinking level as routing proxy:** Instead of switching models, map task type to thinking level: code tasks get `high` thinking, chit-chat gets `off`, reasoning gets `max`. This works if all tasks route through the same base model (Qwen3.8). Simpler than model switching but loses the coder-small/vision specialization.

**Recommendation:** Do NOT build the model-switching classifier (A/B). Build only C — a `before_agent_start` hook that sets thinking level from prompt shape. No swap cost, no classifier model, no KV-cache loss.

**Complexity:** Low (thinking-level only).

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
3. **Serialized sub-agent (`task` tool)** — extension registering a nested agent loop against the *current GPU0 model*; returns a summary. Zero GPU change, zero swap. Delivers context isolation, the primary sub-agent benefit. Build and measure this BEFORE any GPU1 work.
4. **Design + doc skills** — point Pi at open-design `skills/` (one settings line); test a few for local-model quality.
5. **goosedump tuning** — enable `enableMemory: true`, test with real workflows, evaluate auto-injection gap.
6. **GPU1 worker slot (only if step 3 proves serialization is the bottleneck)** — gate on GGUF size + `bench_server.py`; scope to retrieval/analysis, not code generation (see break-even below).
7. ~~pi-smart-router~~ → thinking-level hook only (see §2); skip the classifier.
8. **pi-bench** — convenience; not blocking daily use.
9. ~~pi-debug-trace~~ / ~~pi-project-context~~ — largely redundant with Pi built-ins (footer/`/session`/`--mode json`; context-file walking). Evaluate the actual gap before building.

~~pi-web-tools~~ — done (BrowserOS MCP). ~~pi-memory~~ — done by goosedump.

## Critical constraint: sub-agents require a second model slot

> **Superseded 2026-09-05.** See "Reassessment" + "GPU1 model sizing" under §3 above for the current analysis (serialization delivers context isolation with no GPU change; GPU1 is optional and, if used, sized for Qwen3.5-9B on a *fully free* 3070 = 8.00 GiB, not the 590 MiB-free framing below). The options below are kept for history; the VRAM figures predate the 8.00 GiB correction and the hybrid-architecture KV math.

Pi doesn't ship sub-agents — it's by design ("Pi ships with powerful defaults but skips features like sub agents and plan mode. Instead, you can ask pi to build what you want or install a third party pi package"). A sub-agent extension needs a second model to run alongside the primary model on GPU0.

**The problem:** GPU1 (3070, 8 GiB) currently hosts dispatcher (~1.3 GiB) + utility-gpu (~6.2 GiB) as residents, leaving ~590 MiB free. No room for a third model. GPU0 runs one big model at a time via llama-swap's swap group. So there's no concurrent second model slot for sub-agents.

**Options:**

A. **Kick utility-gpu off GPU1, load a small Qwen model instead.** utility-gpu is the summarizer fast path (~14x CPU decode). Losing it means summaries fall back to CPU utility (~5 tok/s decode). If goosedump handles compaction client-side (Pi's own model does the summarization), we may not need utility-gpu at all — it was built for AI Mega App's server-side background summarization which is being retired. This frees ~5 GiB on GPU1 for a small coding/agent model alongside dispatcher.

B. **Use coder-small (Qwen2.5-Coder-7B) on GPU1.** At Q4 it's ~4-5 GiB. Fits alongside dispatcher (~1.3 GiB) on the 8 GiB 3070. But this contradicts the current placement (coder-small is on GPU0's swap group) and the measured constraint that "coder-small must stay on GPU0, never GPU1" (from the co-residency testing). That constraint was about coder-small + dispatcher + utility-gpu all on GPU1 — without utility-gpu, it might fit. **Needs re-measurement.**

C. **Use an even smaller model.** Qwen3-4B, Qwen3-1.7B, or a small tool-calling model. ~2-3 GiB, easily fits on GPU1 alongside dispatcher. Lower quality but sufficient for sub-agent tasks like file search, simple edits, test running.

D. **CPU-only sub-agent model.** The box has 64 GB RAM and 32 cores. A Q4 7B model runs at ~5-15 tok/s on CPU with 8 threads. Slow but functional for background tasks that don't need interactive speed.

E. **Use the primary model via llama-swap.** Sub-agent calls go to the same model the main agent is using. No concurrent execution but serialized sub-agent turns work if the sub-agent tasks are short. This is the zero-hardware-cost option.

**Reassessment (2026-09-05) — the framing conflated two separable things.** "Sub-agents" and "a concurrent second model" are not the same. The primary benefit of sub-agents is **context isolation** (a worker greps 40 files, returns 3 lines; the main context never sees the other 37), and that benefit needs **no concurrency at all** — a serialized sub-agent hitting the *same already-loaded GPU0 model* delivers it at zero hardware cost and zero swap (option E). So the GPU1 work is optional, not the unlock.

**Break-even math for a DELEGATED (GPU1) coder, supervised by the orchestrator.** Decode is memory-bandwidth-bound; the 112 tok/s coder-small figure was measured on the 3090 (936 GB/s). Scaled to the 3070 (448 GB/s) that's **~54 tok/s** [INFERENCE — needs `bench_server.py` on GPU1 to confirm]. Supervision is not free: the orchestrator (27.7 tok/s measured) *decodes* its review reasoning. Modelling a 2000-token task (orchestrator baseline 72.2s):

| coder tok/s | verification | break-even acceptance | max speedup |
|---|---|---|---|
| 54 | mechanical (tests/compile, ~0 review tokens) | ~56% | 1.79x |
| 54 | read-and-judge (~300 review tokens) | ~71% | 1.41x |
| 112 (GPU0 fantasy) | ~0 | 30% | 3.30x |

**Consequence: supervision cost and savings are the same quantity.** The more carefully the orchestrator reviews, the less you save. Delegated code *generation* is the worst fit — thin margin, expensive supervision. The design only wins where verification is **mechanical** (execute tests / compile), not intellectual (read and judge).

**Better role for a GPU1 worker: retrieval and analysis, not generation.** Grep the repo, read 30 files, return a summary. There the acceptance-rate problem vanishes — the orchestrator consumes a summary it never had to page in; context isolation is pure win with no break-even to clear.

### GPU1 model sizing (Qwen3.5-9B, 2026-09-05)

Qwen3.5-9B (released Feb 2026) is a **hybrid** architecture: 32 layers = 8 full-attention + 24 Gated DeltaNet (linear attention). Only the 8 attention layers hold a growing KV cache (4 KV heads, head_dim 256) → **~32 KiB/token**, ~4.5x better than a dense 8B. This buys context, not throughput.

3070 = **8.00 GiB** (8192 MiB, binary — earlier drafts wrongly used 7.45). Weights est. 5.06 GiB at ~4.83 bpw Q4_K_M (UNVERIFIED — HF blocked from this env; the ~152k vocab may push the real GGUF to 5.4–5.8 GB).

| Config | Context ceiling (q8_0 KV) | Decode (realistic) |
|---|---|---|
| dispatcher resident (1.3 GiB) | ~40k tokens | ~48–56 tok/s @ short ctx |
| **3070 fully free** | **~130k tokens** (65k at fp16) | ~45–53 @ 32k, ~35–41 @ 128k |

Prefill is compute-bound: ~550–1100 tok/s (wide bracket — kernel-dependent). That's the number that matters for a retrieval worker (read 30 files in seconds).

**The real tradeoff:** "fully free" means dispatcher moves off GPU1 *too*. At 32k ctx the worker needs only ~5.6 GiB, so dispatcher can stay and you still get ~40k ctx. The jump 40k→130k costs the dispatcher — only worth it if tasks genuinely need >40k.

**Two cheap gates before any GPU reconfiguration (in order):**
1. Download the GGUF, `ls -l` — confirms 5.06 vs 5.4–5.8 GiB; this single number decides whether dispatcher stays.
2. `CUDA_VISIBLE_DEVICES=1 python3 scripts/bench_server.py --label coder-gpu1 --model <gguf> --model-class coder-small --ctx 32768` — confirms real tok/s.

If decode lands ~54, the delegated-generation design is DOA and the retrieval-worker design is the one to build. Leave utility-gpu resident until these gates pass.

## Open questions

1. **Mono-repo or separate repos?** Mono-repo (like pi itself) is simpler for cross-plugin types but heavier to install individually.
2. **Where does the classifier model live?** If Pi talks to llama-swap, the classifier is already running as a CPU resident. The router extension just needs to call it via the llama-swap endpoint. But should we use Pi's own model for classification instead (avoid an extra HTTP call)?
3. **Memory storage location?** `~/.pi/agent/memory.db` (global) vs `.pi/memory.db` (per-project) vs a central SQLite on ailab (survives machine changes). Probably global + per-project, like Pi's own extension locations.
4. **Do we need the AI Mega App web UI at all?** Pi's TUI covers coding. But a web UI for non-terminal users (phone, tablet) or for sharing sessions could still have value. Pi's experimental RPC mode + a web frontend is the path if needed.
5. ~~**BrowserOS integration**~~ — done.
6. **Goosedump modifications:** Source inspection shows goosedump uses a native binary for both compaction and memory — it does NOT call an LLM through llama-swap at all. This means: (a) utility-gpu can be retired immediately, freeing ~5 GiB on GPU1; (b) compaction/memory quality depends on the native binary's built-in model, not our local models; (c) the open question is whether goosedump's native compaction quality is good enough for our use case, or if we need to modify it to use llama-swap models instead. Test with real sessions before deciding.
7. **Sub-agent architecture:** Pi doesn't have sub-agents natively. Building a sub-agent extension requires: a second model slot (GPU1), a way to spawn parallel agent loops, and a protocol for the main agent to delegate tasks. This is likely the hardest plugin to build but the highest-impact for coding productivity.
