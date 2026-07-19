# Post-Mortem & Restart Plan

Evaluation of Prompter X (this repo) and a ground-up plan for the rebuild:
a Claude.ai web clone (chats, projects, artifacts, tool calls) + Claude Code via opencode,
running against a single Ubuntu 26.04 server with one RTX 3090 (24GB).

Confidence tags: [FACT] verifiable in this repo · [INFERENCE] reasoned · [UNCERTAIN] guess.

---

## Part 1 — Where it went wrong

Ranked by how much damage each caused.

### 1. The intent classifier/router was the root architectural mistake

[FACT] Every message went: user → keyword rules → a 3B classifier model with a ~4.4k-token
prompt → `{intent, model, tools, confidence}` → maybe a *different* model gets loaded → reply.

Consequences, all visible in the repo:

- **Misrouting was structural, not a tuning problem.** An entire eval harness
  (`scripts/eval_classifier.py`, `eval/classifier/`, 12+ prompt mutations "mut12", two
  handoff docs) existed just to make the classifier less wrong. Claude.ai has no such
  component: the *user* picks the model, and the model always has its tools. You rebuilt
  a hard research problem (zero-shot intent classification with a 3B model) as a
  prerequisite for every single chat turn.
- **Latency tax on every message.** Classify (3B inference) → possibly swap the reply
  model in/out of VRAM (`ModelScheduler`) → then answer. "Tool calls took too long"
  is mostly this: intent switch = model load = tens of seconds on first token.
- **Fragility.** README warns `num_ctx=4096` silently truncates the classifier prompt and
  the classifier "answers as chat" [FACT]. A misconfigured integer broke the entire app's
  dispatch layer.
- **Selective tool grants** (tools chosen per-intent) meant a misclassification didn't just
  pick a suboptimal model — it *removed capabilities* mid-conversation. You already
  identified this; it's correct.

### 2. Model zoo + VRAM swapping

[FACT] 16 model aliases in `litellm_config.yaml` (qwen3-8b, two coder sizes, two r1 sizes,
three gemma vision sizes, tiering aliases…) on what was originally an 8GB 3070. Every
intent flip could trigger an Ollama load/unload cycle. Commits like
`feat(ollama): idle-unload models after 5 minutes` and `feat(latency): stage timers for
classify, ensure_loaded, llm` are symptoms: you were instrumenting and managing a
scheduling problem that only exists because of decision #1.

### 3. Three layers of model-name indirection (alias → LiteLLM → Ollama tag)

[FACT] `settings.json` aliases → `litellm_config.yaml` → `ollama_model_names` mapping, with
sync code (`litellm_sync.py`, `litellm_resolver.py`) and bug-fix commits
(`fix(ollama-alias-mapping)`, `kimi-k2.6` slug bug). LiteLLM also swallowed provider
errors mid-stream: `docs/phase1-open-bugs.md` Bug 2 — provider failure = silent empty
bubble, `Exception in ASGI application`. LiteLLM bought you one thing (uniform API) that
you can get for free, because **Ollama, llama.cpp, and opencode zen all already speak
the OpenAI chat-completions API**. The abstraction cost more than it saved.

### 4. Tool calling via prompt injection + text parsing for models that can't do it

[FACT] `chat_orchestrator.py` contains `_extract_tool_calls_from_text`,
`_strip_tool_json_from_text`, `_format_deepseek_tool_appendix` ("Native tool_calls are
unavailable for this model… output ONLY one JSON object"), and deepseek-r1
special-casing. The known-issues table says it plainly: "Model emits tool JSON in
`chunk` instead of structured `tool_calls`."

The lesson is not "parse better." It's: **only give tools to models with native,
trained-in tool calling**, and pick ONE such model as the default. Regex-rescuing tool
JSON out of a reasoning model's prose is a permanent bug farm.

### 5. Parallel multi-agent build process shipped disconnected parts

[FACT] AGENTS.md defines builder waves, FILE SCOPE tables, frozen files, worktree-per-agent
rules. The flagship result: `DuckDuckGoSearchAdapter` was implemented *and tested* but
**never wired into the app** (Bug 1 — `search_service=None` at startup). Components
passed their unit tests in isolation while the product didn't work end-to-end.
240 passing tests, and web search returned "Search service unavailable" in production.
Process docs (spec is 54KB, plus AGENTS.md, 10 docs/) rivaled the code in volume.

[INFERENCE] Parallel agents with non-overlapping file scopes optimize for merge-conflict
avoidance, not for working software. Integration is exactly the part nobody owned.

### 6. Frontend: no real routing, state-in-one-component, features bolted on

[FACT] No router; `App.tsx` juggles view-mode state; the app originally *couldn't start a
chat without creating a project* (Bug 3); model labels, stop button, nav were all
retrofits; `SettingsModal.tsx` is 1,419 lines — the largest file in the frontend, bigger
than ChatView. The UI grew feature-by-feature with no upfront information architecture,
which matches your own description ("adding features as I went").

### 7. Scope explosion before the core worked

[FACT] Qdrant RAG, nomic embeddings, vision tiers, pdf_gen, deep research
(Tavily), a full CLI chat client with Windows clipboard paste helpers
(`terminal_input.py`, `paste_input.py`, `clipboard_paste.py` — ~740 lines), debug trace
panel, todo tool, ask_user tool… while "send message, get streamed reply, no crash" still
had open bugs. Each subsystem added config surface, health states, and degradation rules
(README documents `qdrant:down / degraded`).

### What was actually fine

- SSE streaming as the transport. Keep it.
- Filesystem-first projects (docs as real files). Keep the spirit.
- pytest discipline. Keep, but test end-to-end flows, not just parts.

---

## Part 2 — Restart plan

### Design principles (each one is an inversion of a failure above)

1. **No router, no classifier.** The user picks the model from a dropdown (default =
   one good local model). Claude.ai parity, zero misclassification, zero classify latency.
2. **One resident local model.** No swapping. VRAM is budgeted once, at startup.
3. **One API dialect: OpenAI chat-completions.** Backend speaks it to llama.cpp/Ollama
   (local) and opencode zen (remote) identically. No LiteLLM, no alias→alias→tag chains.
   A model entry is `{id, label, base_url, api_key?}` — one list, one hop.
4. **All tools, always, native only.** Every chat model in the dropdown must pass a
   tool-call smoke eval; tools are always in the request. The model decides whether to
   use them — that's what tool-trained models are for. No per-intent tool grants, no
   text-parsing fallbacks.
5. **Errors are events.** Any failure mid-stream emits an `error` SSE frame then `done`.
   The UI never shows a silently-empty bubble. This is a contract from day one, not Bug 2.
6. **Vanilla frontend, no build step.** HTML/CSS/JS ES modules served statically. You
   asked for it and it's the right call at this scope: one developer, one user, no team.
7. **Single process, single machine, SQLite.** FastAPI serves API + static files. No
   Docker requirement for the app itself, no Qdrant, no separate proxy.
8. **E2E-first testing.** The gate is "open browser, send message, tool runs, reply
   streams" scripted with Playwright — not 240 unit tests of unwired parts.
9. **Serial development.** One feature at a time, merged working. No agent waves, no
   FILE SCOPE tables, no frozen files.

### Tech stack

| Layer | Choice | Why |
|---|---|---|
| Inference (local) | **llama.cpp `llama-server`** (or Ollama if you prefer its model management) serving ONE model, OpenAI-compatible `/v1` | Single resident model; llama.cpp gives explicit `-ngl`, ctx size, and no surprise unloads. [INFERENCE] Ollama acceptable; llama.cpp removes its keep-alive/swap behaviors entirely. |
| Remote models | opencode zen (`https://opencode.ai/zen/go/v1`) direct | Already OpenAI-compatible [FACT — current config proves it]. |
| Backend | **Python 3.12 + FastAPI**, one process | You know it; async SSE is easy; serves static frontend. Target ≤ ~2,000 lines total. |
| HTTP client | `httpx` streaming straight to the model server | No SDK indirection; errors surface where they happen. |
| Storage | **SQLite** (stdlib `sqlite3`), one file | Chats, messages, projects metadata, artifacts. Project docs stay as real files on disk under `projects/<id>/files/`. |
| Frontend | **Vanilla HTML/CSS/JS**, ES modules, no bundler | Hash-based routing (`#/chat/:id`, `#/projects`, `#/project/:id`, `#/code`). `marked` + `highlight.js` + `DOMPurify` as three vendored static files — the only dependencies. |
| Claude Code | **opencode in server mode** (`opencode serve`), backend proxies it; UI gets a "Code" section | See below. |
| Tests | pytest for API + **Playwright** for the golden path | E2E is the gate. |

Explicitly dropped: React/Vite/TS, LiteLLM, Qdrant, embeddings/RAG (v1), vision tiers,
pdf_gen, CLI client, classifier + eval harness, ModelScheduler, docker-compose,
DebugTracePanel (replace with plain server logs + a collapsible "raw events" toggle).

### VRAM plan (24GB, single 3090)

Pick ONE resident chat model. Recommended candidates, all with native tool calling:

| Model | Quant | VRAM (weights) | Notes |
|---|---|---|---|
| **Qwen3-30B-A3B-Instruct (MoE)** — recommended default | Q4_K_M ~17–18GB | ~6–7GB left for KV cache → ~32k ctx comfortably. MoE = fast tokens/s (3B active). Good tool calling. [INFERENCE on exact fit — verify with `llama-server` at setup] |
| Qwen2.5-Coder-32B / Qwen3-32B dense | Q4 ~19GB | Stronger code, slower, tighter KV headroom |
| Smaller fallback: Qwen3-14B | Q5 ~11GB | If you want huge context or headroom |

Rules: model loads at boot, stays loaded forever. No second local chat model until the
second 3090 exists. Remote models (deepseek/kimi/etc. via zen) cover the "I need a
bigger brain" case — they cost $0 VRAM.

When dual 3090s + NVLink arrive: either one bigger model split across GPUs, or GPU0 =
chat model / GPU1 = coder model for opencode. Decide then; nothing in v1 depends on it.

**On needle (cactus-compute):** [UNCERTAIN — I can't verify its capabilities/maturity from
here]. Position: don't build v1 around a fine-tuned micro tool-caller. With no
classifier and one tool-native 30B-class model, the problem needle would solve
(small models executing tool calls consistently) doesn't exist in this architecture.
Revisit only if the chosen model measurably fails the tool eval, and even then prefer
swapping the main model before adding a second routing brain — that's how the last
project died.

### Architecture

```
Browser (vanilla JS, hash routing)
   │  fetch + SSE
   ▼
FastAPI  (one process, ~6 modules)
   ├─ /api/chats, /api/messages        → SQLite
   ├─ /api/chat/stream (POST, SSE)     → agent loop
   │     └─ httpx → OpenAI /v1/chat/completions
   │            ├─ local: llama-server (127.0.0.1:8080)
   │            └─ remote: opencode zen
   │     └─ tool executor (bash, read/write/edit file, grep, glob,
   │        web_search, web_fetch) — sandboxed to project dir or scratch
   ├─ /api/projects, /api/projects/{id}/files → disk + SQLite
   ├─ /api/artifacts                   → SQLite (versioned)
   └─ /api/code/*                      → reverse-proxy to opencode serve
```

**The agent loop** (the heart, keep it under ~200 lines):

```
messages = system + project_instructions + project_files_context + history + user_msg
loop (max 15 iterations):
    stream POST /v1/chat/completions  (tools=ALL_TOOLS, stream=true)
    forward text deltas as SSE {type:"text", delta}
    if finish_reason == tool_calls:
        for each call: emit {type:"tool_start", name, args}
                       run with asyncio.timeout(60)
                       emit {type:"tool_result", name, output (truncated)}
        append assistant tool_calls msg + tool results; continue loop
    else: break
persist assistant message (content + model + tool trace)
emit {type:"done", model, usage}
on ANY exception: emit {type:"error", message}, then {type:"done"}   ← contract
```

SSE event set (complete, fixed from day one):
`text` · `tool_start` · `tool_result` · `artifact` · `error` · `done`. Nothing else.
No debug/route/model_loading events — there is no routing and no loading.

**Tools (v1, always available):**

| Tool | Impl | Sandbox |
|---|---|---|
| `bash` | subprocess, 60s timeout, output cap 10KB | cwd = project dir (or per-chat scratch dir), non-root user |
| `read_file` / `write_file` / `edit_file` | direct fs | path must resolve inside sandbox root |
| `grep` / `glob` | `rg` / `pathlib` | same |
| `web_search` | SearXNG self-hosted on the server, or Tavily/Brave API key | n/a. [INFERENCE] DDG scraping rate-limits (you hit this — `phase1-search-ddg-resilience.md`); SearXNG on your own box is the reliable free option |
| `web_fetch` | httpx + readability extraction, size cap | n/a |
| `create_artifact` / `update_artifact` | writes artifact record, emits `artifact` SSE | n/a |

**Artifacts:** an artifact = `{id, chat_id, identifier, type (html|svg|markdown|code|mermaid), title, content, version}`.
The model creates/updates them via the two artifact tools (this is roughly how Claude.ai
does it, and it reuses the tool loop instead of inventing a parsing layer). UI renders in
a right-hand panel: `html/svg` in a sandboxed `<iframe srcdoc sandbox="allow-scripts">`,
code with highlight.js, markdown via marked+DOMPurify. Version stepper for history.

**Projects:** `projects/<id>/` on disk holds uploaded/created files; SQLite holds
metadata + custom instructions. Project context = instructions + a file manifest always
in the system prompt; small text files (< ~50KB total) inlined directly; bigger corpora
are reached by the model itself via grep/read tools. **No vector RAG in v1.** With 32k
ctx and grep-capable tools, agentic retrieval covers the personal-use case; vector search
is a v3 item if and only if inlining+grep provably falls short.

**Claude Code via opencode:** run `opencode serve` (headless HTTP server) as a systemd
unit on the same box, pointed at opencode zen and/or the local llama-server. FastAPI
mounts `/api/code/*` as a thin reverse proxy (auth + same-origin). The UI "Code" section
is a separate, minimal page: pick a directory/session, send prompts, render its event
stream. [UNCERTAIN: exact opencode server API surface/stability — verify `opencode serve`
endpoints against the installed version in Phase 4 before designing that page; if its API
is awkward, fallback is running opencode's own web UI on another port and linking to it.
Do NOT reimplement a coding agent inside your app — that was the "mega app" trap.]

**Frontend structure (no build step):**

```
static/
  index.html
  css/app.css              (one theme file, CSS custom properties, dark default)
  js/
    main.js                (hash router: chat / chats-list / projects / project / code / settings)
    api.js                 (fetch wrappers + SSE reader via fetch-streams)
    views/chat.js          (message list, composer, streaming render)
    views/projects.js      (grid, project workspace: files + instructions + project chats)
    views/artifacts.js     (panel, iframe sandbox, versions)
    views/code.js          (opencode session UI)
    markdown.js            (marked + DOMPurify + highlight wiring)
  vendor/ marked.min.js  dompurify.min.js  highlight.min.js
```

Routing is real (hash-based, linkable, back-button works) — fixes the App.tsx
view-state mess. Rendering: innerHTML-from-sanitized-markdown per message, streaming
message re-rendered at rAF cadence (you already learned this lesson —
`fix(chat-render-perf)` [FACT]).

**Data model (SQLite):**

```sql
chats(id, project_id NULL, title, model, created_at, updated_at)
messages(id, chat_id, role, content, model, tool_trace JSON, created_at)
projects(id, name, instructions, created_at)
artifacts(id, chat_id, identifier, type, title, created_at)
artifact_versions(artifact_id, version, content, created_at)
settings(key, value)          -- model list, api keys ref, defaults
```

Global chats have `project_id = NULL` — day-one Claude.ai parity on "open app, type
immediately" (was Bug 3 for weeks in the old repo).

### Feature list

**v1 (the gate: daily-driver replacement for basic Claude.ai use)**
- Streaming chat, stop button, error frames, markdown/code rendering
- Chat list, rename/delete, global chats without a project
- Model dropdown: 1 local + N remote (zen); label shown on each assistant message
- All tools always on: bash, file r/w/edit, grep, glob, web_search, web_fetch
- Tool activity shown inline (collapsible), like Claude.ai's tool blocks

**v2**
- Projects: grid, files upload, custom instructions, project-scoped chats, inline/agentic file context
- Artifacts: create/update tools, panel, iframe sandbox, versions

**v3**
- Code section via opencode serve
- Attachments/images in chat (needs a vision-capable model decision)
- Optional: vector RAG (only with evidence v2 retrieval fails), chat search, export

**Out of scope permanently (unless you personally miss them):** intent routing,
per-intent model tiers, pdf_gen tool, CLI chat client, vision model zoo, LiteLLM,
Qdrant, debug trace panel, multi-agent build process.

### Build phases with acceptance gates

Each phase ends with a working, demoable app. Serial. No phase 2 work while phase 1
has open bugs.

| Phase | Scope | Gate (Playwright-scripted where possible) |
|---|---|---|
| 0. Infra (½ day) | llama-server systemd unit with chosen model; `curl /v1/chat/completions` streams; tool-call smoke test (10 prompts → ≥9 well-formed native tool calls) | Model answers + tool-calls via curl. **If the model fails the smoke test, change model now, not architecture later.** |
| 1. Chat core (2–4 days) | FastAPI + SQLite + SSE agent loop (no tools yet) + vanilla chat UI + chat list + model dropdown + stop + error frames | E2E: send msg → streamed reply; kill llama-server mid-stream → visible error bubble, app fine after restart |
| 2. Tools (2–3 days) | All 7 tools + inline tool blocks + sandbox + timeouts | E2E: "what's in file X" (read), "search the web for Y" (search), "run ls" (bash) each round-trip < a few seconds overhead beyond model time |
| 3. Projects (2–3 days) | Grid, files, instructions, scoped chats, context injection | E2E: create project, upload doc, ask about doc content, correct answer |
| 4. Artifacts (2–3 days) | artifact tools + panel + versions + sandboxed preview | E2E: "make me an HTML snake game" → renders in panel; "make the snake blue" → v2 |
| 5. Code (2–4 days) | opencode serve + proxy + Code UI (after verifying its API) | Start a session on a repo dir, get a diff applied |

Timeboxes are working-session estimates, not calendar promises [INFERENCE].

### Rules for the rebuild (process)

1. One branch, serial merges. No parallel agent waves, no FILE SCOPE tables.
2. Nothing is "frozen." Refactor freely; the E2E suite is the safety net.
3. Every bug fix lands with the E2E repro that would have caught it.
4. New feature ideas go in `IDEAS.md`, not into the current phase. (This is the
   scope-creep valve the last project lacked.)
5. Hard line-count alarm: if the backend passes ~3k lines or any file passes ~500,
   stop and ask what's being over-built.
