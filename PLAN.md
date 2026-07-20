# AI Mega App — Build Plan v2

**Date:** 2026-07-20
**Status:** Planning. Supersedes `prompter_x_complete_spec.md` (kept only as a post-mortem reference).
**Confidence tags:** [FACT] verifiable · [INFERENCE] reasoned · [UNCERTAIN] guess, verify before building.

---

## 1. Why the old plan and codebase are not the base

The repo is evidence of what went wrong, not a foundation. Lessons extracted:

1. **Ollama was the wrong center of gravity.** The whole model layer (ModelScheduler, `ollama_model_names` alias mapping, keep_alive semantics, warmup quirks) was custom code compensating for Ollama's weak programmatic model management. The new spec bans Ollama. [FACT — visible across `app/model_scheduler.py`, `HANDOFF_OLLAMA_INTEGRATION.md`, README]
2. **The classifier became a project of its own.** A ~4.4k-token prompt for a 1.5–3B model, truncation bugs (`num_ctx=4096` silently broke it), an eval ledger, and ~a third of recent commits are classifier fixes. Free-text JSON from a small model is fragile. [FACT — git log, `docs/classifier_prompt*.md`]
3. **Components built but never wired.** DDG search adapter existed, tested, and was never injected at startup (Bug 1). The plan had per-file task ownership but no integration gate per feature. [FACT — `docs/phase1-open-bugs.md`]
4. **Error paths were an afterthought.** Provider failures killed SSE streams silently (Bug 2). Debug tracing arrived late instead of being the first thing built — the new spec correctly makes the debug panel critical.
5. **UI never reached Claude.ai parity** because the plan treated frontend as "extend existing" last-mile work (Bugs 3–4: no home chat, no project grid, no model labels).
6. **Three config files + frozen-file contracts** (`settings.json`, `litellm_config.yaml`, `.env`, frozen `project_manager.py`) created coordination overhead that blocked simple fixes (Bug 4 needed a "frozen file owner approval" to add one field).
7. **LiteLLM added a layer without earning it.** Its value was multi-provider abstraction; the new spec is local-first with one OpenAI-compatible endpoint (llama-swap), so LiteLLM is dead weight. [INFERENCE]
8. **Old spec's hardware assumptions are stale.** It targeted an 8GB RTX 3070 on WSL2; the actual environment became a dedicated LAN GPU box, and the new spec is multi-GPU Ubuntu 26.04. Every VRAM budget in the old spec is void. [FACT]

Carry-forward that *did* work: SSE streaming contract, filesystem-first projects, intent→model mapping in config not code, keyword-rules-before-classifier ordering, graceful degradation rules, Cursor rules discipline (worktrees, FILE SCOPE, no `git add .`).

---

## 2. Prior art (how others built this)

| Project | What to take | What to avoid |
|---|---|---|
| **Odysseus** (spec's stated near-identical target) | Python backend + **vanilla JS/CSS/HTML frontend — no React** [FACT — repo language stats]. Module structure: chat, agents, deep research, documents, notes, model "cookbook" (hardware-aware model serving), blind model comparison. Docker compose variants per GPU vendor. | It's a monolith of many productivity modules (email, calendar, notes) — those are Future-tier for us, don't clone wholesale. |
| **hermes-agent** (Nous Research) | One agent core serving every surface (CLI/API/gateway) — platform differences live at the entry point. Plugin system where memory providers and context engines are swappable single-select components. Skills-from-experience loop. [FACT — repo docs] | It's TypeScript/Ink and agent-first, not chat-app-first. Take the architecture shape, not the code. |
| **Open WebUI / LibreChat** | Proven patterns: chat title auto-generation via a designated small "task model", client-side Pyodide code execution for artifacts, hybrid RAG (BM25 + vectors), per-chat model override UI. | Both are large frameworks (SvelteKit/React, heavy plugin systems) — exactly what Key Rule 2 forbids copying. |
| **llama-swap** (mostlygeek) | Go proxy, zero deps, OpenAI+Anthropic compatible, on-demand model start, **groups** (`swap: false` = pinned residents like embedder/classifier; `swap: true` = exclusive big-model slot), TTL auto-unload, per-model macros incl. `${PORT}`, web UI with metrics. Actively released (v201, Apr 2026). [FACT] | — |
| **llama.cpp router mode** (`--models-dir`, `--models-preset`, `--models-max`) | Native multi-model switching landed in llama-server; one resident model per worker, full unload/reload on switch. [FACT] | Doesn't replace llama-swap for us: no group pinning semantics as rich, and llama-swap also fronts non-llama.cpp backends later. See §4.1 decision. |
| **vLLM Semantic Router / RouteLLM** | Validates the thesis: route with a **tiny dedicated classifier** (they use ModernBERT), not a chat model with a giant prompt. Confirms reasoning-vs-non-reasoning split is the highest-value routing axis. [FACT] | Full Rust/Envoy deployment is massive overkill for single-user. |
| **Cactus Needle** | 26M single-shot function-calling model, distilled from Gemini; emits one JSON tool call from query+tool list; trivially CPU-hostable. [FACT] | It is **single-shot only** — it picks and fills one tool call; it does not do multi-step tool loops or reasoning. Use as a *dispatcher assist*, never the agent loop. [FACT — Cactus's own framing] |
| **BrowserOS** | Chromium fork with built-in **MCP server exposing 31+ browser tools** (navigate, click, scrape, screenshot) to any MCP client; supports local model providers. Integration = we are an MCP client of the browser. [FACT] | Don't build browser automation ourselves; also don't depend on it for core chat (it's a separate install). |
| **opencode** | `opencode serve` = headless HTTP server with OpenAPI spec, basic-auth option, CORS flags; official `@opencode-ai/sdk` (TS) generated from that spec; config is plain `opencode.json` (custom providers = any OpenAI-compatible `/v1` base URL). [FACT] | Don't wire opencode *inside* the chat tool loop (the old handoff's open question). It stays a delegated, separately-surfaced coding agent. See §5.6. |

---

## 3. Target architecture

```
┌────────────────────────── Any machine on LAN ──────────────────────────┐
│  Browser → Web UI (vanilla JS modules + CSS, SSE)                      │
│  BrowserOS (optional, host machine) ← MCP → backend                    │
│  opencode serve (optional, host machine, for host-side coding)         │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ HTTP/SSE (LAN)
┌────────────────────── Ubuntu 26.04 GPU box ────────────────────────────┐
│  FastAPI backend (Python 3.12) — the only "app"                        │
│   ├─ chat orchestrator (stream loop, tool loop)                        │
│   ├─ router (override → rules → classifier)                            │
│   ├─ tools/ (search, files, bash-sandbox, browser-mcp, …) toggleable   │
│   ├─ rag/ + memory/  ── SQLite + sqlite-vec (chats, memories, chunks)  │
│   ├─ gpu/ (nvidia-smi inventory → llama-swap config generator)         │
│   ├─ debug/ (per-turn trace store + SSE tap)                           │
│   └─ static file serving for web/                                      │
│                                                                        │
│  llama-swap (:8080) ── groups ──► llama-server instances (llama.cpp)   │
│   ├─ group "resident" swap:false → classifier, embedder, needle (CPU/  │
│   │                                 small-GPU, always loaded)          │
│   └─ group "main-gpuN" swap:true → one big model per GPU slot          │
│                                                                        │
│  opencode serve (:4096) — coding agent on the box                      │
│  Artifact sandbox: Docker containers (server-side exec) + iframe/      │
│  Pyodide (client-side)                                                 │
└────────────────────────────────────────────────────────────────────────┘
```

**Deployment decision:** the backend runs *on* the Ubuntu box; the "application that communicates to the Ubuntu computer" (spec §3) is the browser. This removes an entire cross-machine API surface. Host-machine needs (opencode on host, BrowserOS) are reached from the backend over LAN as clients of *their* servers. [INFERENCE — simplest topology satisfying spec §3, §4, §6, §15]

**Web vs desktop (spec §4):** web app. Reasons: zero packaging, updates instantly, reachable from any device, and Odysseus proves the pattern. Desktop wrapper (Tauri) only if a Future need demands OS integration. [INFERENCE]

### 3.1 Tech stack (per Key Rules 1–3)

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI + httpx + uvicorn | Async SSE-native, already known, minimal deps. No LiteLLM, no LangChain, no agent framework. |
| Frontend | **Vanilla ES modules + CSS custom properties + `<template>` elements. No React, no Vite, no build step.** | Key Rule 2 verbatim; Odysseus does exactly this at far larger scope. Each view = one JS module + one CSS file; Rule 3 (no 1000-line files) enforced by module-per-component. Markdown render: `marked` + DOMPurify (two small vendored libs); highlight: `highlight.js`. |
| Storage | **SQLite (WAL) + sqlite-vec extension** — one file for chats, messages, memories, vectors, debug traces, settings-overlay | Single-user scale. Qdrant was a second service to babysit; sqlite-vec removes Docker dependency for the data plane. Escape hatch: `VectorStore` interface so Qdrant can return if corpus outgrows sqlite-vec (>~1M vectors). [INFERENCE — sqlite-vec comfortably handles personal-scale corpora; verify with a 100k-chunk benchmark in Phase 0] |
| Projects | Filesystem-first (`projects/<id>/instructions.md`, `docs/`) — the one part of the old app that worked | Keep, but thread/message storage moves to SQLite (filesystem JSON threads made model-attribution and search painful). |
| Inference | llama.cpp `llama-server` instances, managed by **llama-swap** | Spec §1. See §4.1. |
| Config | `config.yaml` (one file, checked in with defaults) + `.env` (secrets only) + generated `llama-swap.yaml` (machine-written, never hand-edited) | Two hand-edited files instead of three. Settings UI writes a `settings.local.yaml` overlay. |

**Frontend risk flag** [UNCERTAIN]: no-build vanilla JS means no TypeScript. Mitigation: JSDoc type annotations + `tsc --checkJs` in CI as a *linter* (types without a build step). If DOM complexity grows past ~30 components, revisit with lit-html (still no build) before ever reaching for React. This is the single decision most likely to get re-litigated; committing to it now per Key Rule 2.

---

## 4. Feature designs (Critical list, spec order)

### 4.1 llama.cpp + llama-swap (spec §1, §2, §14)

**Decision: llama-swap in front of plain `llama-server` instances — not llama.cpp's native router mode.** Rationale: router mode keeps one resident model per worker and lacks llama-swap's group semantics (pin classifier+embedder+needle resident while big models swap); llama-swap also fronts any OpenAI/Anthropic-compatible backend if we ever add vllm/whisper/SD servers, and has TTL, metrics, and a monitoring UI we'd otherwise write. [FACT re: capabilities; INFERENCE re: choice]

**GPU delegation (spec §14):** at backend startup, run `nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv` → GPU inventory endpoint → Settings UI lets the user assign each model to a GPU (or CPU for <2B models, spec §2.1). A deterministic Python module (`gpu/swapgen.py`) renders `llama-swap.yaml` from `config.yaml` model entries + GPU assignments:

```yaml
# generated — do not hand-edit
macros:
  llama: /opt/llama.cpp/build/bin/llama-server --host 127.0.0.1 --port ${PORT} --jinja
models:
  classifier:
    cmd: ${llama} -m /models/qwen3-1.7b-q8.gguf --device none -ngl 0 -c 4096   # CPU
  embed:
    cmd: ${llama} -m /models/nomic-embed-v2.gguf --embeddings --device CUDA1 -c 2048
  needle:
    cmd: ${llama} -m /models/needle-q8.gguf --device none -ngl 0
  chat-large:
    cmd: ${llama} -m /models/qwen3-32b-q4.gguf --device CUDA0 -ngl 999 -c 32768
  coder:
    cmd: ${llama} -m /models/qwen3-coder-30b-q4.gguf --device CUDA0 -ngl 999
groups:
  resident: { swap: false, exclusive: false, members: [classifier, embed, needle] }
  gpu0-main: { swap: true, members: [chat-large, coder, reasoner, vision] }
```

Changing assignments → regenerate file → llama-swap config reload. **This is programmatic config writing, not AI-generated** (Key Rule 1 / Future §8 principle). [UNCERTAIN — exact `--device`/`-ts` flag spelling per llama.cpp build; verify against the installed version's `llama-server --help` in Phase 0, and llama-swap's reload endpoint name against current docs]

**Model classes (spec §2):** general, coding, tool-call, reasoning, vision — all are just entries in `config.yaml` with a `class:` tag and a `gpu:` assignment; vision models add `--mmproj`. Benchmarks: `llama-bench` wrapped by a script in Phase 1 of the testing suite (spec Future §4 does the full suite; Critical only needs per-model tok/s sanity numbers shown in the debug panel).

**Client:** one thin `llm_client.py` speaking OpenAI chat-completions to llama-swap (`model` field selects the model; llama-swap handles load/swap). No scheduler code in our app at all — the entire old ModelScheduler problem is deleted. [FACT — this is llama-swap's core function]

### 4.2 Web application (spec §4)

- Layout: left nav (Chats / Projects / Settings / Debug), center chat, right context panel (project sources / artifact view), Material-ish flat design via one `theme.css` of custom properties (Future §3 themes = swap that file).
- Every view is a JS module exporting `mount(el, state)` / `unmount()`. A 200-line `router.js` (hash-based) and a 150-line `store.js` (pub/sub state) — hand-written, no framework.
- SSE client with auto-reconnect and a hard rule learned from Bug 2: **every stream must terminate with `done` or `error`; UI shows "connection lost" if neither arrives.**

### 4.3 Smart router (spec §5)

Three layers, strictly ordered; every decision emitted to the debug panel with source + latency:

1. **Manual override** — per-chat model picker always wins (spec: "user can manually select").
2. **Deterministic rules** — attachments force intents (image→vision, code file→coding); config keyword rules (word-boundary, 2+ words). Cheap, transparent, no model.
3. **Classifier** — small resident model (~1.7B, CPU or spare-GPU) via llama.cpp with **`response_format: json_schema` (GBNF-enforced)**. This is the key fix over the old build: llama.cpp grammar constraints make malformed JSON structurally impossible, so the 4.4k-token prompt defense and parse-failure fallbacks shrink to a ~600-token prompt + few-shots. [FACT — llama.cpp supports schema-constrained sampling]
   - Output: `{class: general|coding|tool|reasoning|vision, effort: light|heavy, needs_tools: [...]}` → model resolved from `config.yaml` `routing:` table. Classifier never names models (old spec's mistake — it baked model names into the prompt, so every model change meant prompt surgery).
   - Timeout 2s → default chat model. Confidence < threshold → default chat model, flagged in debug panel.
4. **Optional upgrade path** [INFERENCE, Phase 6+]: a ModernBERT-style fine-tuned classifier head (vLLM Semantic Router's approach) if the generative classifier's accuracy plateaus; the router interface doesn't change.

### 4.4 opencode integration (spec §6)

- `opencode serve` runs as a systemd unit on the Ubuntu box; optionally also on the host machine (user-launched). Both registered in Settings with URL + basic-auth password.
- opencode's `opencode.json` on both machines points its provider at llama-swap's `/v1` (custom OpenAI-compatible provider) — so opencode uses the same local models. Written by our config generator, not by hand and not by AI (Future §8 rule).
- Web app surface: a "Code" area that (a) lists opencode sessions via its OpenAPI API, (b) creates a session against a chosen directory, (c) streams session events into a viewer, (d) "Open in VS Code" deep-link for full projects. The chat router can *suggest* delegating a coding request to opencode but never silently does it — user confirms. [INFERENCE — keeps agent loops from nesting, the failure mode the old handoff warned about]
- [UNCERTAIN]: opencode's event-stream endpoint shape and session API stability across versions — pin the opencode version, smoke-test in Phase 4 before building UI on it. The TS SDK is official; from Python we call the HTTP API directly (it's OpenAPI-specified).

### 4.5 Projects (spec §7)

Mirror Claude.ai: project grid → project workspace (instructions, sources/files, project chats, project memory). Filesystem layout stays (`instructions.md`, `docs/`); ingestion → chunker → sqlite-vec, incremental on file mtime. Fixes old Bug 3 by design: app opens to a plain new chat; Projects is a nav item, not a gate.

### 4.6 Artifacts + sandboxed execution (spec §8)

Two tiers, both toggleable:

- **Tier 1 (client, Phase 3):** artifact panel rendering markdown/HTML/SVG/JS in a sandboxed `iframe` (`sandbox="allow-scripts"`, no same-origin), Python via **Pyodide** in a web worker. Zero server risk, covers Claude.ai-artifact parity. [FACT — this is Claude.ai's and Open WebUI's model]
- **Tier 2 (server, Phase 4):** `POST /api/exec` runs code in a **short-lived Docker container** on the box (`--network none`, mem/cpu/pids limits, read-only rootfs + tmpfs workdir, 30s timeout). Used by the `bash`/`run_code` tool and artifacts needing real deps. Images: `sandbox-python`, `sandbox-node`.

### 4.7 Tool calls (spec §9)

- Primary path: llama.cpp native tool calling (`--jinja` + model chat template) through the OpenAI `tools` API; orchestrator runs the accumulate-deltas → dispatch → append-result loop (max N iterations, N in config). The old spec's delta-merge logic was correct — reuse the *pattern*.
- **Needle assist (spec §9.1):** for models tagged `tool_call: weak` in config, the orchestrator can route the *tool-selection step* to resident Needle: query + tool schemas → Needle emits the JSON call → orchestrator executes → result handed to the main model as context for the answer. Toggleable per model; debug panel marks turns that used it. Constraint honored: Needle is single-shot — multi-step loops still require a competent tool model or the router escalating model class. [FACT re: Needle's scope]
- Tools are one module each under `tools/`, self-describing (`name, schema, execute()`, `enabled` flag) — registry auto-discovers; toggling a tool off = config flag (Key Rule 6).
- Initial set: `web_search`, `fetch_url`, `file_ops` (project-scoped), `run_code` (Tier 2 sandbox), `browser` (BrowserOS MCP), `memory_save/search`.

### 4.8 RAG + memory (spec §10) — **spec sentence is truncated ("similar to …")**

The spec cuts off mid-sentence. Building the consensus design (Open WebUI/hermes-style) unless you name the reference: [UNCERTAIN — confirm the intended reference app]

- **RAG:** per-project doc ingestion → heading-aware chunking (~512 tokens, 20% overlap; AST chunking for code via tree-sitter later) → embeddings (resident embed model via llama-swap `/v1/embeddings`) → sqlite-vec + SQLite FTS5 → **hybrid retrieval** (vector + BM25, reciprocal-rank fusion) → top-k into context with source citations in the UI.
- **Memory:** three stores — (a) user preferences/custom prompts (spec §17): plain editable text, always injected; (b) project memories: model-proposed facts, user-approved, injected in that project; (c) global memories: `memory_save` tool + retrieval by embedding at chat start. All visible/editable in Settings → Memory (no invisible memory).
- Chat history itself is embedded per-message-batch → enables "search my past chats" (spec §13).

### 4.9 Attachments (spec §11)

Upload endpoint → type sniff → extractor registry: text/code (direct), pdf (pymupdf), docx/xlsx/pptx (python-docx etc. or markitdown), images (→ vision model path), audio [Future]. Extracted text goes to context if small, to RAG-on-the-fly if large. Each extractor is one module (Key Rule 6).

### 4.10 Testing suite (spec §12)

- **Unit/integration:** pytest against the FastAPI app with a fake llama-swap (canned OpenAI responses) — router decisions, tool loop, SSE framing, extractors, swapgen output.
- **Contract tests:** golden SSE transcripts (a turn's full event sequence) diffed on change.
- **Router eval:** keep the old repo's one good idea — a labeled prompt→expected-route CSV + `eval_router.py` scoring script, run on classifier prompt/model changes.
- **E2E smoke:** Playwright, ~10 flows (send message, switch model, upload file, artifact render, debug panel populates), run against a fake-LLM backend so CI needs no GPU.
- **Live hardware check:** `scripts/preflight.py` — nvidia-smi present, llama-swap up, each configured model loads and answers 1 token, embeddings endpoint alive. Run on the box, not CI.
- Gate: no feature merges without its tests; CI = lint (ruff) + `tsc --checkJs` + pytest + Playwright-vs-fake.

### 4.11 Vector DB (spec §13) — covered in §3.1/§4.8 (sqlite-vec; Qdrant behind interface if needed).

### 4.12 BrowserOS (spec §15)

Backend ships a generic **MCP client** (`tools/browser.py`) connecting to BrowserOS's built-in MCP server on the host machine; its 31+ tools are exposed to capable models as a `browser` toolset (off by default — browser actions are consequential; per-chat toggle). BrowserOS itself can also point its own agent at llama-swap `/v1` independently. [UNCERTAIN — MCP transport BrowserOS exposes (SSE vs streamable-HTTP vs stdio-only) and its LAN reachability; verify in Phase 5 before UI work. Note: spec says "BrowserClaw" once — assuming BrowserOS per the URL given.]

### 4.13 Search (spec §16)

`search/` provider chain: **DDG primary** (`ddgs` lib, no key) → on rate-limit/empty → **Tavily** (key in `.env`). Old build's DDG-resilience doc showed DDG throttling is real — the fallback is automatic per-query, with the provider used shown in citations + debug panel. (Spec's "taily" read as Tavily.)

### 4.14 Custom prompts / preferences / project memories (spec §17) — see §4.8 memory tiers; prompt templates (system prompt per model class, per project) live in `config.yaml` + Settings UI.

### 4.15 Chat summaries, auto-title, compaction (spec §18)

A designated **utility model** (small resident or the classifier model) handles: title after first exchange; rolling summary stored per chat; compaction when context exceeds threshold (summarize oldest turns, keep recent verbatim + summary block — Claude Code's own pattern). All background tasks; failures never block chat.

### 4.16 Debug panel (spec §19 — critical, built FIRST not last)

- Every turn gets a `trace_id`; every stage (route, rag, llm request/response, tool dispatch, swap wait, SSE emit) writes a span row to SQLite: timestamps, model, full prompt (toggle), token counts (from llama.cpp `usage` + `timings`), latency, GPU snapshot.
- `/api/debug/stream` SSE feeds a live Debug view: per-turn waterfall, route decision + why, raw prompts/responses, tok/s, llama-swap state (proxied from its API), nvidia-smi poll.
- This is infrastructure other features must call — building it in Phase 1 forces every later feature to be observable (the old build proved retrofitting this is painful).

---

## 5. Phases

Each phase ends with: features **wired end-to-end** (no "adapter exists but not injected"), tests green, docs page written, demo checklist run on real hardware. No phase starts on top of an unintegrated one.

**Phase 0 — Ground truth (box + inference).** Ubuntu box: drivers/CUDA, build llama.cpp, install llama-swap as systemd unit, download initial model set (one per class + classifier + embedder + Needle), hand-write first `llama-swap.yaml`, verify swap latency and concurrent resident group with `curl`. Benchmark sqlite-vec at 100k chunks. Deliverable: a doc of measured facts (VRAM per model, load times, tok/s) replacing all guessed budgets. *No app code.*

**Phase 1 — Skeleton with eyes.** FastAPI app: config load/validate, `/health`, `llm_client`, SQLite schema, SSE chat endpoint against a fixed model, minimal chat UI (send/stream/history), **debug trace store + Debug panel**, error-path contract (`done`/`error` always). Testing harness + CI from day one. Exit: chat with any manually-picked model, every turn fully traced.

**Phase 2 — Routing + models control.** GPU inventory, swapgen (generated llama-swap config + reload), Settings UI (models, GPU assignment, toggles), router layers 1–3 with grammar-constrained classifier, router eval harness, auto-title/summaries. Exit: correct model auto-selected ≥90% on eval set; GPU reassignment without restart.

**Phase 3 — Substance.** Tools framework + web_search/fetch/file_ops, Needle assist, attachments pipeline, Projects (grid/workspace), RAG hybrid retrieval + citations, memory tiers, Tier-1 artifacts (iframe/Pyodide), chat compaction. Exit: Claude.ai-parity daily-driver for chat work.

**Phase 4 — Code.** Docker exec sandbox + `run_code` tool, Tier-2 artifacts, opencode serve on box (+ optional host), Code area UI (sessions, delegation flow), opencode config generator. Exit: replaces Cursor for small/medium tasks.

**Phase 5 — Reach + hardening.** BrowserOS MCP tool, Tavily fallback polish, `llama-bench` panel, preflight script, security pass (LAN auth on backend, sandbox audit), full docs (per-feature `docs/<feature>.md`: what/why/how-to-extend — Key Rule 7).

**Future (unordered, post-5):** deep research pipeline, open-design, themes, benchmark suite, Obsidian, Google integrations, custom skills, one-click MCP add (programmatic `opencode.json`/our-config writer — already designed via generators).

---

## 6. Build discipline (Cursor + Claude Code)

- **Rules files** (rewrite, don't reuse old ones): `001-stack` (vanilla JS/no frameworks/no Ollama/no LiteLLM — affirmatively phrased), `002-modularity` (module-per-feature, `enabled` flags, file size cap ~300 lines), `003-config` (all model/provider names from config; generated files never hand-edited), `004-observability` (every new pipeline stage must write a debug span + emit terminal SSE event), `005-integration` (a feature PR must include wiring + test + docs page — "built but not injected" is a rejected PR), `006-git` (worktrees, FILE SCOPE, no `git add .` — carry over, it worked).
- **Per-feature workflow:** design note (½ page: interfaces, config keys, debug spans, toggle) → approved → build in worktree → tests → wire → demo checklist → docs. The old repo died in the gap between "built" and "wired"; rule 005 exists to close it.
- **AI-generation guardrail (Key Rule 1):** generators (swapgen, opencode config, MCP registration), extractors, and SQL schema are deterministic hand-written code; agents implement against written interfaces, never invent config formats.

---

## 7. Open questions — need your answers before Phase 0 ends

1. **GPU inventory** [UNCERTAIN]: spec says multi-GPU; the old box had one RTX 3090. Exact cards + VRAM decide the model roster and whether a `resident` group fits on a spare GPU or CPU. List them.
2. **Spec §10 truncation**: "RAG and memory system similar to ___" — name the reference (hermes-agent memory? Open WebUI? Claudian?). §4.8 builds the consensus design meanwhile.
3. **Host machine role**: is the host Windows? Determines opencode-on-host and BrowserOS setup instructions (both run there, backend stays on Ubuntu).
4. **Model roster**: which specific GGUFs per class (chat/coder/reasoner/vision/classifier). Phase 0 benchmarks decide, but candidates should be picked deliberately, not by an agent.
5. **Auth**: backend is LAN-only — is single-password auth enough (recommended), or fully open on trusted LAN?
