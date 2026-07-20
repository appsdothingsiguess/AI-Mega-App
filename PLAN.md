# AI Mega App — Build Plan v2

**Date:** 2026-07-20 (rev 2 — owner feedback applied: TypeScript, hardware confirmed 3090+3070, hermes-style memory, claude.ai 1:1 UI, no auth, opencode/BrowserOS division of labor)
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
│  Browser → Web UI (TypeScript→ES modules + CSS, SSE)                   │
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
| Frontend | **TypeScript compiled with plain `tsc` → native ES modules. No React, no bundler, no framework.** | Owner approved TS ("light"). `tsc` is the entire build: `src/*.ts` → `web/js/*.js`, browser loads modules directly. Each view = one TS module + one CSS file; Rule 3 (no 1000-line files) enforced by module-per-component. Markdown render: `marked` + DOMPurify (small vendored libs); highlight: `highlight.js`. |
| Storage | **SQLite (WAL) + sqlite-vec extension** — one file for chats, messages, memories, vectors, debug traces, settings-overlay | Single-user scale. Qdrant was a second service to babysit; sqlite-vec removes Docker dependency for the data plane. Escape hatch: `VectorStore` interface so Qdrant can return if corpus outgrows sqlite-vec (>~1M vectors). [INFERENCE — sqlite-vec comfortably handles personal-scale corpora; verify with a 100k-chunk benchmark in Phase 0] |
| Projects | Filesystem-first (`projects/<id>/instructions.md`, `docs/`) — the one part of the old app that worked | Keep, but thread/message storage moves to SQLite (filesystem JSON threads made model-attribution and search painful). |
| Inference | llama.cpp `llama-server` instances, managed by **llama-swap** | Spec §1. See §4.1. |
| Config | `config.yaml` (one file, checked in with defaults) + `.env` (secrets only) + generated `llama-swap.yaml` (machine-written, never hand-edited) | Two hand-edited files instead of three. Settings UI writes a `settings.local.yaml` overlay. |

If DOM complexity grows past ~30 components, revisit with lit-html (still no bundler) before ever reaching for React.

---

## 4. Feature designs (Critical list, spec order)

### 4.1 llama.cpp + llama-swap (spec §1, §2, §14)

**Decision: llama-swap in front of plain `llama-server` instances — not llama.cpp's native router mode.** Rationale: router mode keeps one resident model per worker and lacks llama-swap's group semantics (pin classifier+embedder+needle resident while big models swap); llama-swap also fronts any OpenAI/Anthropic-compatible backend if we ever add vllm/whisper/SD servers, and has TTL, metrics, and a monitoring UI we'd otherwise write. [FACT re: capabilities; INFERENCE re: choice]

**Hardware (confirmed):** RTX 3090 (24GB) + RTX 3070 (8GB), 64GB system RAM, Ryzen 9. Future: dual 3090 + dual 3070 split across **two computers** — llama-swap only manages processes on its own machine, so the two-box future means one llama-swap per box and a `model → endpoint` map in our config; the backend already selects by model name, so this is additive, not a redesign. [FACT re: llama-swap scope]

**Placement strategy (rev 3 — owner: old tier list was for the lone-3090/Ollama era; consolidate):**

- **CPU (Ryzen 9):** classifier + Needle. Owner confirms the old classifier ran fine on CPU; Needle is 26M — trivial. This frees the 3070 entirely.
- **3070 (8GB):** embedder (resident) + **`utility` small model** (Qwen3-8B-class, Q4, resident) for titles, summaries, compaction, memory review — and instant fallback replies while the 3090 swaps.
- **3090 (24GB):** the big-model slot, one at a time, llama-swap `swap: true`.

**Consolidated roster** — the light/medium/heavy triads collapse to ~one model per class. Rationale: the triads existed to dodge Ollama swap costs on a shared card; with llama-swap + a dedicated utility card, fewer/better models win. Speed floor: **nothing under ~25 tok/s at its working quant** (owner: "no 10 tok/s"); Q4_K_M is the default quant, Q5/Q6 only where headroom allows. 2026 references for 24GB: Qwen3.6-27B dense ≈ 30–40 tok/s; 35B-A3B MoE ≈ 50 tok/s; Qwen3-32B Q4_K_M ≈ 20GB. [FACT — published 3090 benchmarks; exact numbers re-measured in Phase 0]

| Alias | Candidate (GGUF, Q4_K_M) | Where | Role |
|---|---|---|---|
| `chat-default` | Qwen3.6-35B-A3B (MoE) or Qwen3.6-27B | 3090, **always loaded** | General chat + native tool calling. MoE preferred: ~50 tok/s. |
| `coder` | Qwen3-Coder-30B-A3B (MoE) | 3090 swap | All coding tiers. Ctx variants (16k/24k) = two llama-swap entries, same weights file. |
| `reasoner` | DeepSeek-R1-32B **or** Qwen3.6 thinking variant | 3090 swap | Owner confirms R1-32B fits. Dense 32B ≈ 25–35 tok/s — at the speed floor; Phase 0 A/Bs it against the MoE thinking variant, keep one. |
| `vision` | Gemma4-27B-class + mmproj | 3090 swap | Owner confirms Gemma sizes fit. One vision model, not three. |
| `utility` | Qwen3-8B Q4 | 3070 resident | Titles/summaries/compaction/memory reviewer/instant fallback. |
| `embed` | nomic-embed-v2 / Qwen3-embedding | 3070 resident | |
| `classifier` | Qwen3-1.7B-class | CPU resident | Router (grammar-constrained). |
| `needle` | Cactus Needle 26M | CPU resident | Tool-call dispatcher. |

Old tier aliases (`coding-light/medium/heavy`, `reasoning-*`, `vision-*`) remain as **routing labels in config that point at this roster** — the router/Settings vocabulary survives, the model count drops from ~12 to ~8 (4 big, 4 small). Users can still add models per class in Settings.

**Default model / always-loaded (owner §4):** `chat-default` has no TTL and the backend re-warms it whenever the 3090 has been idle on another model for N minutes (config, default ~10) — a ~20-line deterministic policy, so a fresh chat almost always gets an instant first token. `utility` on the 3070 answers trivial/background tasks with zero swap wait regardless.

**Per-chat model switching (owner §5) — this is already solved by the architecture, no new machinery:** each chat row stores `model_override` (null = router decides). The composer's model picker writes it; every turn resolves `override ?? router(intent)` and passes that name in the OpenAI request's `model` field; **llama-swap does the actual load/swap transparently**. The only UX work: an SSE `model_loading` status event so the UI shows "loading <model>…" during a 3090 swap (3–10s typical reload [FACT — llama.cpp reload benchmarks]), and the per-message model label. Mid-chat switches are just the next turn's field value.

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

- **Design target: mirror claude.ai web 1:1** (owner directive) — collapsible left sidebar (new chat, Chats, Projects, recents), centered chat column, right-side artifact/context panel, model picker in the composer, per-message model label, plus a Settings area and the Debug view (our additions). Build a static HTML/CSS mock of the claude.ai layout *first* and get it approved before any logic — UI parity was where the last build quietly diverged (Bug 3). Theme via one `theme.css` of custom properties (Future §3 themes = swap that file).
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

### 4.4 opencode integration (spec §6) — division of labor, with corrections to the owner's proposal

Owner's proposal and where I push back:

1. **"In-chat coding artifacts (simple python script, debugging a file) go through opencode on Ubuntu."** — **Pushback: don't.** A simple script or single-file debug is one completion by the chat/coding model + a sandbox run; routing it through opencode adds a full agent loop (session create → agent plans → tool calls → file writes) for a task with no repo, multiplying latency and failure points for zero gain. The dividing line that works: **no workspace → chat model + artifact sandbox; real directory/repo → opencode session scoped to it.** "Debug this file" sits on the line: if the file was pasted/attached, it's chat-side; if it lives in a project/repo path on the box, delegate to opencode. [INFERENCE — this is also how Claude.ai itself splits artifacts vs. Claude Code]
2. **"App can use opencode's tool calling to search files etc (not sure)."** — **Correct to: no.** opencode's API is session-based (create session → prompt → agent acts); it is not a tool-RPC you call for one `grep`. [FACT — OpenAPI surface is sessions/messages/events] Our own `file_ops` tool (~100 lines, project-scoped) does file search directly — faster, deterministic, debuggable (Key Rule 1: if plain programming does the task, no AI in the loop).
3. **VS Code integration is indeed separate:** opencode's IDE extension talks to its own opencode instance; nothing for our backend to do beyond docs + a "open this project in VS Code + opencode" workflow doc.

Design:
- `opencode serve` as a systemd unit on the Ubuntu box (primary — spec: generation happens on Ubuntu); optionally also user-launched on the Windows host for host-side repos. Both registered in Settings by URL. No auth (owner: open LAN).
- opencode's `opencode.json` on both machines points its provider at llama-swap's `/v1` (custom OpenAI-compatible provider) — same local models. Written by our config generator, not by hand and not by AI (Future §8 rule).
- **Docs deliverable (owner §9):** `docs/opencode.md` includes switching opencode's provider between local llama-swap and **opencode zen** (its hosted gateway, the old "OpenCode Go") — an `opencode.json` provider/model edit + API key; document both directions.
- Web app surface: a "Code" area that (a) lists sessions via the OpenAPI API, (b) creates a session against a chosen directory, (c) streams session events into a viewer, (d) "Open in VS Code" deep-link. The router can *suggest* delegation; the user confirms — agent loops never nest silently.
- opencode's server API: OpenAPI 3.1 spec published at `/doc`, official (alpha) Python client `opencode-ai` on PyPI — use it if the Phase 4 smoke test finds it stable, else generate/hand-roll a thin client from the spec. Pin both. [FACT]

**Harness decision — opencode vs qwen-code (researched 2026-07-20, primary sources only):** qwen-code was evaluated as a possible replacement and **deferred**. Findings:
- The "optimized for Qwen3-Coder" harness advantage is a **[VENDOR CLAIM]** with no reproducible eval behind it; both harnesses exhibit the *identical* tool-call failure classes with local Qwen3-Coder (stringified arrays, malformed JSON — opencode #1809 / qwen-code #528), and a neutral cross-harness report shows all harnesses breaking identically until the server-side template is fixed. Qwen's own technical report benchmarks Qwen3-Coder with SWE-Agent, not qwen-code. [FACT]
- **Tool-call reliability lives in llama.cpp, not the harness:** Qwen3-Coder emits a custom XML-ish tool format with Python-literal args; llama.cpp ships a dedicated Qwen3-Coder tool-call parser and `--jinja` is mandatory. Run a recent llama.cpp build and Phase 0 must verify *streaming* tool calls through llama-swap end-to-end. [FACT — llama.cpp #15012/#19382, unsloth docs]
- **Integration surface decides it:** `opencode serve` = long-documented OpenAPI 3.1 + Python client; `qwen serve` = 2-month-old Stage-1 alpha, no OpenAPI, single-shared-session default, and its PyPI SDK is a CLI-subprocess wrapper that can't do stateful multi-turn and doesn't talk to the daemon at all. [FACT]
- **Zen has no client lock-in** (documented) — plain endpoints (`https://opencode.ai/zen/v1/chat/completions` for DeepSeek V4 Flash), so the escalation path is harness-independent anyway; auth header needs one verification curl. [FACT / UNCERTAIN re: header]
- **Operational guardrails from opencode's issue tracker:** pin the version (weekly churn broke llama.cpp grammar once — #28502, closed "not planned"); declare the local model's context limit explicitly in generated `opencode.json` (#31433); watch compaction with local Qwen3-Coder (#22792 open). Maintainers close local tool-call issues as "model-problem" — expect no in-harness compensation. [FACT]
- **Revisit trigger:** re-evaluate qwen-code when `qwen serve` reaches Stage 2 (OpenAPI spec + multi-session). The integration is a thin session-lifecycle client behind one module, so switching later is cheap by design (Key Rule 6); owner's "can always Phase 0 test" stands — the Phase 0 tool-call verification script is harness-agnostic and can be pointed at either.

### 4.5 Projects (spec §7)

Mirror Claude.ai: project grid → project workspace (instructions, sources/files, project chats, project memory). Filesystem layout stays (`instructions.md`, `docs/`); ingestion → chunker → sqlite-vec, incremental on file mtime. Fixes old Bug 3 by design: app opens to a plain new chat; Projects is a nav item, not a gate.

### 4.6 Artifacts + sandboxed execution (spec §8)

Two tiers, both toggleable:

- **Tier 1 (client, Phase 3):** artifact panel rendering markdown/HTML/SVG/JS in a sandboxed `iframe` (`sandbox="allow-scripts"`, no same-origin), Python via **Pyodide** in a web worker. Zero server risk, covers Claude.ai-artifact parity. [FACT — this is Claude.ai's and Open WebUI's model]
- **Tier 2 (server, Phase 4):** `POST /api/exec` runs code in a **short-lived Docker container** on the box (`--network none`, mem/cpu/pids limits, read-only rootfs + tmpfs workdir, 30s timeout). Used by the `bash`/`run_code` tool and artifacts needing real deps. Images: `sandbox-python`, `sandbox-node`.

### 4.7 Tool calls (spec §9)

- Primary path: llama.cpp native tool calling (`--jinja` + model chat template — for Qwen3-Coder specifically, a recent build with its dedicated tool-call parser is **required**, since the model emits an XML-ish format with Python-literal args that generic parsing mangles [FACT — llama.cpp #15012]) through the OpenAI `tools` API; orchestrator runs the accumulate-deltas → dispatch → append-result loop (max N iterations, N in config). The old spec's delta-merge logic was correct — reuse the *pattern*.
- **Needle assist (spec §9.1):** for models tagged `tool_call: weak` in config, the orchestrator routes the *call-emission step* to resident Needle: query + tool schemas → Needle emits the JSON call → orchestrator executes → result to the main model. Toggleable per model; debug panel marks Needle-assisted turns.
- **On the owner's "Needle is fast enough to chain many calls" idea — half right.** Speed is genuinely not the constraint (6000 tok/s prefill; repeated invocation is nearly free). The constraint is that Needle emits **one call per inference with no conditional planning** — "tool chaining is not in scope" per Cactus themselves. [FACT] So chains work exactly when *deciding what's next* doesn't require reasoning: either (a) a bigger model plans the step list and Needle fills each call's arguments, or (b) the next step is mechanically determined (fixed pipelines like search→fetch→fetch). Anything with branching on results stays with the main model. Architecture: Needle is the **dispatcher**, never the **planner** — and the debug panel will show per-step who decided vs. who emitted.
- **Custom training is real and cheap** [FACT]: Cactus ships a local fine-tuning playground — tool spec → synthetic data (Gemini API) → train → eval in ~10 min, ~120 examples/tool recommended. Plan: once our tool registry stabilizes (end of Phase 3), fine-tune Needle on our exact schemas and re-run the router/tool eval; adopt only if it beats the untuned baseline. In-context learning is not yet supported, so schema changes mean retraining — another reason to wait for a stable registry. [FACT]
- Tools are one module each under `tools/`, self-describing (`name, schema, execute()`, `enabled` flag) — registry auto-discovers; toggling a tool off = config flag (Key Rule 6).
- Initial set: `web_search`, `fetch_url`, `file_ops` (project-scoped), `run_code` (Tier 2 sandbox), `browser` (BrowserOS MCP), `memory_save/search`.

### 4.8 RAG + memory (spec §10) — reference confirmed: **hermes-agent**

How hermes actually does it [FACT — hermes docs]: fact-based "holographic memory" in **SQLite + FTS5** (not a vector DB!), memories injected into the user message inside a tagged `<memory-context>` block, pluggable memory providers, and a **background self-improvement review** that after a turn may quietly save a memory or update a skill. Our adaptation:

- **RAG (documents):** per-project ingestion → heading-aware chunking (~512 tokens, 20% overlap; AST chunking for code via tree-sitter later) → embeddings (resident embed model via llama-swap `/v1/embeddings`) → sqlite-vec + FTS5 → **hybrid retrieval** (vector + BM25, reciprocal-rank fusion) → top-k with source citations in the UI.
- **Memory (hermes-style facts):** discrete fact rows in SQLite (FTS5 + optional embedding), three scopes — user preferences/custom prompts (always injected, spec §17), project memories, global memories. Injected as a tagged context block, hermes-fashion. All visible/editable in Settings → Memory; nothing invisible.
- **Self-improvement loop (the part the owner flagged as "super cool"):** after a turn completes, a background job on the utility model reviews the transcript and may *propose* a memory write or an update to a skill/instruction file — proposals land in a review queue in the UI, auto-accept optional per scope. Phased: manual `memory_save` tool in Phase 3; background reviewer in Phase 5; skill *creation* (hermes' full loop) belongs with Future §7 custom skills. [INFERENCE — hermes writes silently; queue-first is safer for a system you're also debugging]
- Chat history embedded per-message-batch → "search my past chats" (spec §13).

### 4.9 Attachments (spec §11)

Upload endpoint → type sniff → extractor registry: text/code (direct), pdf (pymupdf), docx/xlsx/pptx (python-docx etc. or markitdown), images (→ vision model path), audio [Future]. Extracted text goes to context if small, to RAG-on-the-fly if large. Each extractor is one module (Key Rule 6).

### 4.10 Testing suite (spec §12)

- **Unit/integration:** pytest against the FastAPI app with a fake llama-swap (canned OpenAI responses) — router decisions, tool loop, SSE framing, extractors, swapgen output.
- **Contract tests:** golden SSE transcripts (a turn's full event sequence) diffed on change.
- **Router eval:** keep the old repo's one good idea — a labeled prompt→expected-route CSV + `eval_router.py` scoring script, run on classifier prompt/model changes.
- **E2E smoke:** Playwright, ~10 flows (send message, switch model, upload file, artifact render, debug panel populates), run against a fake-LLM backend so CI needs no GPU.
- **Live hardware check:** `scripts/preflight.py` — nvidia-smi present, llama-swap up, each configured model loads and answers 1 token, embeddings endpoint alive. Run on the box, not CI.
- Gate: no feature merges without its tests; CI = lint (ruff) + `tsc --noEmit` typecheck + pytest + Playwright-vs-fake.

### 4.11 Vector DB (spec §13) — covered in §3.1/§4.8 (sqlite-vec; Qdrant behind interface if needed).

### 4.12 BrowserOS (spec §15) — placement and role, with corrections to the owner's proposal

1. **Placement: host machine (Windows), not Ubuntu.** BrowserOS is a GUI Chromium fork — on the headless GPU box it would need a virtual display and you'd never see what the agent is doing. Host is where you already browse and stay logged in; that's the whole value (authenticated sessions, watching the agent). [INFERENCE from FACT that it's a desktop browser]
2. **"Browser agent so the app can do deep research" — pushback: browser-driving is the *wrong primary* for deep research.** Perplexity-style deep research is search-API fan-out + parallel HTTP fetch + synthesis — dozens of sources a minute, headless, robust. A browser agent reads one page at a time through a GUI. So: deep research (Future §1) runs on `search` + `fetch_url`; the **BrowserOS toolset is the escalation path** for what fetch can't do — JS-heavy pages, logged-in content (Gmail, dashboards), interactive tasks, and MCP-connector-style automation. Both exposed as tools; the model (or user toggle) picks.
3. Backend ships a generic **MCP client** (`tools/browser.py`) connecting to BrowserOS's built-in MCP server (31+ tools: navigate, click, scrape, screenshot) over LAN; exposed to capable models as the `browser` toolset, off by default, per-chat toggle (browser actions are consequential). BrowserOS can also independently point its own in-browser agent at llama-swap `/v1`.
4. [UNCERTAIN — MCP transport BrowserOS exposes (SSE vs streamable-HTTP vs stdio-only) and whether its MCP server accepts non-localhost connections; if localhost-only, a tiny relay on the host or an SSH tunnel bridges it. Verify in Phase 5 before UI work. Note: spec says "BrowserClaw" once — assuming BrowserOS per the URL given.]

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

**Phase 0 — Ground truth (box + inference).** Ubuntu box: drivers/CUDA, build llama.cpp, install llama-swap as systemd unit, download initial model set (one per class + classifier + embedder + Needle), hand-write first `llama-swap.yaml`, verify swap latency and concurrent resident group with `curl`, and **verify streaming tool calls from the coder model through llama-swap** (recent llama.cpp with the Qwen3-Coder tool-call parser + `--jinja` — this is the single highest-leverage check per the harness research). Benchmark sqlite-vec at 100k chunks. Deliverable: a doc of measured facts (VRAM per model, load times, tok/s) replacing all guessed budgets. *No app code.*

**Phase 1 — Skeleton with eyes.** FastAPI app: config load/validate, `/health`, `llm_client`, SQLite schema, SSE chat endpoint against a fixed model, minimal chat UI (send/stream/history), **debug trace store + Debug panel**, error-path contract (`done`/`error` always). Testing harness + CI from day one. Exit: chat with any manually-picked model, every turn fully traced.

**Phase 2 — Routing + models control.** GPU inventory, swapgen (generated llama-swap config + reload), Settings UI (models, GPU assignment, toggles), router layers 1–3 with grammar-constrained classifier, router eval harness, auto-title/summaries. Exit: correct model auto-selected ≥90% on eval set; GPU reassignment without restart.

**Phase 3 — Substance.** Tools framework + web_search/fetch/file_ops, Needle assist, attachments pipeline, Projects (grid/workspace), RAG hybrid retrieval + citations, memory tiers, Tier-1 artifacts (iframe/Pyodide), chat compaction. Exit: Claude.ai-parity daily-driver for chat work.

**Phase 4 — Code.** Docker exec sandbox + `run_code` tool, Tier-2 artifacts, opencode serve on box (+ optional host), Code area UI (sessions, delegation flow), opencode config generator. Exit: replaces Cursor for small/medium tasks.

**Phase 5 — Reach + hardening.** BrowserOS MCP tool, memory self-improvement reviewer, Tavily fallback polish, `llama-bench` panel, preflight script, sandbox audit (no backend auth — owner: trusted LAN, fully open; the *sandbox* still gets locked down because tool-executed code is not the owner), full docs (per-feature `docs/<feature>.md`: what/why/how-to-extend — Key Rule 7).

**Future (unordered, post-5):** **remote model providers (owner v2: opencode zen/"Go", Anthropic, Kimi — each is one entry in a provider registry with base URL + key in `.env`; llama-swap and remotes are all OpenAI-compatible endpoints to `llm_client`, Anthropic gets a thin adapter, so no LiteLLM needed even then)**, deep research pipeline, open-design, themes, benchmark suite, Obsidian, Google integrations, custom skills (+ hermes-style skill creation loop), one-click MCP add (programmatic `opencode.json`/our-config writer — already designed via generators).

---

## 6. Build discipline (Cursor + Claude Code)

- **Rules files** (rewrite, don't reuse old ones): `001-stack` (TypeScript+tsc only, no frameworks/bundlers, no Ollama, no LiteLLM — affirmatively phrased), `002-modularity` (module-per-feature, `enabled` flags, file size cap ~300 lines), `003-config` (all model/provider names from config; generated files never hand-edited), `004-observability` (every new pipeline stage must write a debug span + emit terminal SSE event), `005-integration` (a feature PR must include wiring + test + docs page — "built but not injected" is a rejected PR), `006-git` (worktrees, FILE SCOPE, no `git add .` — carry over, it worked).
- **Per-feature workflow:** design note (½ page: interfaces, config keys, debug spans, toggle) → approved → build in worktree → tests → wire → demo checklist → docs. The old repo died in the gap between "built" and "wired"; rule 005 exists to close it.
- **AI-generation guardrail (Key Rule 1):** generators (swapgen, opencode config, MCP registration), extractors, and SQL schema are deterministic hand-written code; agents implement against written interfaces, never invent config formats.

---

## 7. Resolved decisions (owner answers, 2026-07-20) + remaining unknowns

Resolved:
1. **Hardware:** 3090 + 3070, 64GB RAM, Ryzen 9 (future: 2×3090 + 2×3070 across two boxes → per-box llama-swap, §4.1).
2. **Memory reference:** hermes-agent, including the self-improvement idea (§4.8).
3. **Host:** Windows now, maybe Linux later — BrowserOS + optional opencode live there; backend stays on Ubuntu.
4. **Per-task model config:** user-configurable in Settings, same pattern as the current app's tier table (§4.1 roster keeps the aliases).
5. **Auth:** none — fully open on trusted LAN.
6. **Frontend:** TypeScript (tsc-only, no bundler). **UI:** claude.ai 1:1 mirror + Settings.
7. **Remote providers** (opencode zen, Anthropic, Kimi): v2/Future.

Remaining unknowns (Phase 0/4/5 verification tasks, not blockers):
- Exact quant + ctx fits on 24GB for `reasoning-heavy` and `vision-heavy` (§4.1 table).
- llama.cpp `--device` flag spelling per installed build; llama-swap reload endpoint name.
- opencode API/event-stream stability across versions (pin it).
- BrowserOS MCP transport + non-localhost reachability from the Ubuntu backend.
- sqlite-vec performance at 100k chunks (benchmark, else Qdrant returns behind the interface).
