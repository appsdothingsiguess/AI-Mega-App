# The Build Book — Claude.ai Clone + Local Code Agent

A complete, step-by-step construction document for the rebuild. Written so that a
competent developer with no prior context could follow it start-to-finish and produce
the working app. **No code** — every step describes *what* to build, *how it behaves*,
*why*, and *how to verify it before moving on*.

Companion document: `RESTART_PLAN.md` (post-mortem of the previous attempt — read it
first; every rule here exists because of a specific failure there).

Confidence tags: [FACT] verifiable · [INFERENCE] reasoned · [UNCERTAIN] verify at build time.

---

## 0. Decisions Register (what changed since RESTART_PLAN.md, and why)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Inference: llama.cpp `llama-server` instances managed by `llama-swap`** (Ollama dropped) | llama-swap is a single Go binary, zero deps, YAML config; it spawns/kills llama-server processes on demand, is OpenAI-compatible, supports TTL unload and parallel model groups [FACT — repo docs]. Gives explicit control Ollama hides (quant choice, ctx size, `-ngl`, sampler flags). LM Studio rejected: GUI-first, closed-source server, wrong fit for a headless Ubuntu box. |
| D2 | **Model swapping is allowed again — but only user-driven.** | The old sin wasn't swapping; it was *implicit per-message* swapping driven by a classifier. When the *user* picks a different model from a dropdown, a 15–30s one-time load is acceptable and expected. Default model stays resident (no TTL); secondaries get a TTL. |
| D3 | **Coding agent: qwen-code (primary), opencode (fallback)** | qwen-code supports OpenAI-compatible/local endpoints with runtime switching, has a headless mode, a daemon mode (`qwen serve`, HTTP+SSE/ACP) and Python/TS SDKs [FACT — repo]. It's maintained by the Qwen team, so its prompt templates and tool-call parsing track qwen-coder models — which is what will run locally. opencode remains a fine TUI to keep installed; the *web-embedded* code agent uses qwen-code's daemon. [UNCERTAIN: exact `qwen serve` API surface — verified in Phase 8, step 1, before any UI is built on it.] |
| D4 | **hermes-agent: adopted as reference architecture, not as the core.** | It exposes an OpenAI-compatible API intended for custom frontends, with built-in memory, skills, sessions [FACT — docs]. But a Claude-clone UI needs fine-grained streaming events (tool blocks, artifact updates) that a generic chat-completions stream does not carry [INFERENCE], and coupling the whole app to a fast-moving v0.2.x platform recreates the "mega-app of other people's moving parts" failure. Instead: (a) copy its memory model (persistent facts + session summaries + history search), (b) optionally mount Hermes later as one entry in the model dropdown — it speaks OpenAI format, so this costs nothing to leave open. Re-evaluate after v1 ships: if its streaming turns out to expose tool events cleanly, switching brains is a contained change because *our UI's SSE protocol is ours*. |
| D5 | **Memory & long-conversation handling are first-class v1 features**, not add-ons. | See §6. Design: hybrid buffer-summary per chat + global memory store with a `remember` tool + retrieval into the system prompt. Industry consensus is sliding window + hierarchical summarization first, vector RAG only when proven necessary [INFERENCE from current practice]. |
| D6 | **No intent router, no classifier, no per-intent tool grants. Ever.** | Root cause of the last failure. All tools always available; user picks models. |
| D7 | **Vanilla HTML/CSS/JS frontend, no build step; FastAPI + SQLite backend; one box.** | Unchanged from RESTART_PLAN.md. |
| D8 | **Considered and rejected: don't-build option (Open WebUI / LibreChat + hermes-agent).** | Honest note: that combo delivers ~80% of the feature list with zero code. Rejected because (a) artifacts + Claude-style projects + embedded code agent are exactly the missing 20%, (b) Open WebUI is a large Svelte codebase — customizing it is harder than building the thin app, (c) the point of this project is a UI you fully control. If you ever just want "working chat with memory today," that stack is the escape hatch. |
| D9 | **Benchmark harness is a deliverable** (Phase 9), because hardware will change (2nd 3090, NVLink). | Model choices must be re-decidable from data, not vibes. |

---

## 1. What We Are Building — Feature Specification

A single self-hosted web app ("the App") on the Ubuntu server, reachable over LAN,
that replaces Claude.ai for personal use:

1. **Chats** — streaming conversations, markdown + syntax highlighting, stop button,
   visible error states, per-message model attribution, rename/delete, instant
   new-chat on load.
2. **Model picker** — dropdown in the composer; local models (via llama-swap) and
   remote models (opencode zen or any OpenAI-compatible endpoint) in one list;
   switchable mid-conversation (§5).
3. **Tools, always on** — bash, read/write/edit file, grep, glob, web_search,
   web_fetch, remember, artifact create/update; rendered as collapsible tool blocks
   in the transcript, like Claude.ai.
4. **Projects** — grid view; each project = instructions + files + its own chats;
   project context available to the model.
5. **Artifacts** — model-created documents/HTML/SVG/code rendered in a side panel,
   versioned, live-previewed in a sandbox.
6. **Attachments** — images (to vision-capable models) and documents (text-extracted,
   stored per chat/project) via the composer.
7. **Memory** — the App remembers durable facts about you across all chats, and
   individual chats never die from context overflow (§6).
8. **Code** — a "Code" section embedding qwen-code sessions against local repos.
9. **Bench** — a page + CLI to benchmark any configured model on fixed suites and
   compare results across models/hardware/time.

Out of scope, permanently unless re-justified in writing: intent routing, model
auto-selection, per-intent tool grants, PDF generation tool, CLI chat client, LiteLLM,
Qdrant (v1), multi-user auth (LAN app, single user; a shared-secret header is enough).

---

## 2. System Architecture

```
Browser — vanilla JS, hash routes: #/chat/:id #/chats #/projects #/project/:id #/code #/bench #/settings
   │ REST + one SSE stream per active generation
   ▼
The App — FastAPI, one process, one SQLite file, serves the static frontend
   ├─ Chat engine: agent loop → OpenAI chat-completions over HTTP
   │      ├─ local models  → llama-swap (127.0.0.1:9292) → llama-server instances
   │      └─ remote models → opencode zen / any OpenAI-compatible URL
   ├─ Tool executor (sandboxed, timeboxed)
   ├─ Context manager (buffer + rolling summary per chat)
   ├─ Memory service (facts store + retrieval + extraction)
   ├─ File service (uploads, text extraction, per-chat/per-project dirs)
   ├─ Artifact service (versioned records + SSE events)
   ├─ Code proxy → qwen-code daemon (`qwen serve`)
   └─ Bench runner (suites → any endpoint → results in SQLite)

Sidecar processes (systemd units, same box):
   llama-swap · qwen-code daemon · SearXNG (search) — each independently restartable
```

Design invariants (violating one requires editing this document first):

- **I1** Every model, local or remote, is reached through the same OpenAI
  chat-completions dialect. A model entry is `{id, label, base_url, api_key?,
  capabilities: {vision, tools}, context_length}`. One list, one hop, no aliases-of-aliases.
- **I2** The browser↔App SSE protocol is owned by the App and fixed at:
  `text · tool_start · tool_result · artifact · status · error · done`. Backends may
  change; this protocol may only grow, never mutate.
- **I3** Any failure anywhere mid-generation emits `error` then `done`. A blank
  assistant bubble is a release-blocking bug.
- **I4** All tools are offered on every request to a tools-capable model. Models
  that fail the tool smoke eval (Phase 0) are marked `tools: false` in the catalog
  and the UI shows a badge; they still chat, they just can't act.
- **I5** SQLite is the single source of truth for chats/messages/memory/artifacts/
  bench results; real files live on disk under a single data root. Backup = copy one
  directory.
- **I6** The frontend has no build step. Dependencies are vendored static files
  (markdown renderer, sanitizer, highlighter). Nothing else.

---

## 3. Hardware & Model Catalog

Current: 1× RTX 3090 (24GB), Ubuntu 26.04 LTS. Later: +3070 (8GB), then 2×3090 NVLink.

### Resident vs on-demand (llama-swap policy)

- **Default model — always resident, no TTL:** one strong generalist with native tool
  calling. Recommended: **Qwen3-30B-A3B-Instruct, Q4 GGUF** (~17–18GB weights, MoE with
  ~3B active → fast tokens/s, leaves ~6GB for ≥32k KV cache). [INFERENCE — confirm
  exact fit in Phase 0; the current best qwen generalist in this size class at build
  time wins, chosen via the Phase 9 bench.]
- **On-demand, TTL ~10 min (llama-swap unloads after idle):**
  - a coder model (e.g. Qwen3-coder 30B class) — also the model qwen-code uses;
  - a vision model (Qwen-VL class, smallest acceptable) — used only when images are attached;
  - anything you're evaluating.
- **Remote:** zen models (deepseek/kimi/etc.) — zero VRAM, in the same dropdown.
- Swap cost is paid exactly once, at the moment the *user* switches; the UI shows a
  "loading model…" status event (I2's `status`) so it never looks hung.

When the 3070 arrives: pin the vision model or the embedding model (if v2 RAG
happens) to it via llama-swap's per-model command flags — small models fit in 8GB and
stop evicting the main model. When dual 3090s arrive: either split a bigger model
across both, or dedicate GPU1 to the coder model so chat and code never contend.
The bench suite (Phase 9) is what decides — rerun it, compare, choose.

---

## 4. Data Layout

One data root, e.g. `/srv/app-data/`:

```
app.db                          # SQLite — everything structured
chats/<chat_id>/files/          # attachments + tool workspace for global chats
projects/<project_id>/files/    # project documents (real files)
artifacts are DB-only (content is text)
models/                         # GGUF files
bench/                          # bench suite definitions (YAML/JSON) + raw outputs
```

SQLite tables (columns descriptive, not exhaustive):

- `chats` — id, project_id (nullable), title, default_model, created/updated, summary
  (rolling context summary, §6), summary_upto_message_id.
- `messages` — id, chat_id, role, content, model (attribution), tool_trace (JSON of
  tool calls/results for transcript rendering), token_count (estimate), created_at.
- `projects` — id, name, instructions, created_at.
- `artifacts` / `artifact_versions` — identifier, type (`html|svg|markdown|code|mermaid`),
  title; versions hold full content.
- `memories` — id, content (one fact per row), category (`identity|preference|project|
  fact`), source_chat_id, created_at, archived (soft delete). Indexed with **FTS5**.
- `models` — the catalog from I1, editable in Settings.
- `bench_runs` / `bench_results` — run metadata (model, quant, hardware note, date) and
  per-case scores/latencies.

---

## 5. Answers to the Hard Questions (design, before build steps)

### 5.1 Mid-conversation model switching

Because history is stored as plain messages (text + tool traces) and every backend
speaks the same dialect (I1), switching is trivial and completely stateless: the next
request simply carries the newly selected model id and the same message history.
Behavior spec:

- The dropdown selection applies to the **next message onward**; each assistant message
  permanently records which model produced it (small label under the bubble).
- Real use cases this must serve, end-to-end:
  1. *Brainstorm escalation* — chatting on the local 30B, hit a hard strategy question,
     switch to a big remote model for three turns, switch back. Nothing to migrate;
     the remote model sees the full (context-managed) history.
  2. *Code drop-in* — mid-chat "write me the SQL for this," switch to the coder model;
     llama-swap loads it (status event shown), answers, TTL later returns VRAM.
  3. *Vision moment* — attach a screenshot; if the current model lacks `vision`, the
     composer shows a one-click "switch to <vision model>" suggestion. No auto-switch
     (that's routing by the back door); it's one click, user-initiated.
  4. *Second opinion* — a per-message "regenerate with…" menu re-runs the last user
     message on a different model, replacing the draft answer. (v1.1, cheap once
     switching works.)
- Edge rule: when switching to a model with a *smaller* context window, the context
  manager (§6) simply compresses harder — same mechanism, no special case.

### 5.2 Attachments & images

- **Images** → stored under the chat's files dir; sent as OpenAI image content parts
  (base64) to `vision: true` models. Non-vision model + image = composer warning +
  suggested switch (see above). Thumbnails render in the transcript.
- **Documents** (txt/md/code/pdf/docx) → stored in the chat or project files dir; text
  extracted on upload (plain readers + a PDF text extractor; OCR out of scope v1).
  Small extractions (≤ ~8k tokens) are injected inline with the message; larger ones
  are announced to the model as available files ("`report.pdf`, 84 pages, at
  files/report.txt") that it reads via its file tools — agentic retrieval instead of
  blind stuffing.
- **Project files** work identically but persist for all chats in the project.

### 5.3 Long conversations & context management (the summarizer question — yes, you need one)

Per chat, the context manager assembles every request as:

```
system prompt (app + project instructions + memory block §5.4)
+ rolling summary of everything older        (from chats.summary)
+ last N messages verbatim                   (the "fresh window")
+ current user message (+ attachments)
```

Mechanics:

- Token budget per request = model's context_length minus a fixed reply+tools reserve
  (~25%). Message token counts are estimated at write time (chars/4 is fine to start).
- When history exceeds the budget, the oldest messages beyond the fresh window (keep
  roughly the last 10–15 exchanges verbatim) are folded into the rolling summary by a
  **background summarization call to the local default model** — never in the hot path
  of a user turn; it runs after the response completes. The summary prompt demands:
  decisions made, facts established, open questions, user preferences expressed, and
  current task state — not prose recap.
- The summary is versioned with `summary_upto_message_id`, so it's incremental: each
  update folds only newly-expired messages into the existing summary.
- Tool traces compress first and hardest (a 10KB grep dump becomes "searched X, found
  Y in file Z") — tool output is the bulkiest, least-rereadable content.
- UI honesty: a thin marker in the transcript shows where summarized history begins
  ("older messages summarized — expand to view raw"), raw messages always remain in
  the DB and visible on demand.
- **No vector RAG over conversation history in v1.** The rolling summary + a plain
  FTS5 search over the user's own past messages (exposed both as a UI search box and
  as a `search_history` tool the model can call) covers the realistic personal-use
  cases. Vector embeddings are a v2 item behind evidence — this mirrors what
  hermes-agent does (history search as a tool) and what current practice recommends
  (window + summarization first, retrieval when proven needed).

### 5.4 Memory (crucial — design in full)

Three layers, deliberately boring:

1. **Durable facts (`memories` table).** Short, atomic, human-readable statements
   ("Runs Ubuntu 26.04 server with one RTX 3090", "Prefers terse, skeptical answers",
   "Building a Claude.ai clone, plan in BUILD_GUIDE.md"). Written two ways:
   - **Explicit tool:** the model has a `remember` tool and is instructed to use it when
     the user states something durably true or says "remember this."
   - **Background extraction:** when a chat goes idle (or on close), a background call
     to the local model reviews the new turns and proposes 0–3 memory candidates;
     near-duplicates (FTS match above a threshold) are dropped or merged.
2. **Injection.** Every request's system prompt carries a memory block: all `identity`
   and `preference` memories (they're small), plus top-K FTS5 matches of other
   categories against the current user message. Hard cap ~1.5k tokens. If the cap is
   hit, oldest-least-matched drop out — memory must never crowd out the conversation.
3. **User control (non-negotiable).** A Memory page in Settings lists every fact with
   edit/archive; every memory shows which chat created it; the chat transcript shows a
   small "memory updated" chip whenever `remember` fired. Silent memory is how trust
   dies and how prompt-injection persists — anything a *web page or file* caused to be
   remembered must be visible and deletable.

This is deliberately the hermes-agent memory shape (facts + session summaries +
history search) [INFERENCE from its docs] implemented in ~three small modules on
SQLite instead of importing the platform.

### 5.5 What others have done (survey conclusions)

- **Open WebUI / LibreChat / LobeChat** — mature chat UIs over OpenAI-compatible
  backends; validate the architecture (thin UI over a standard dialect) but are
  frameworks-heavy and don't do Claude-parity artifacts/projects. Rejected as base (D8),
  kept as reference for UX details (message virtualization, stop handling).
- **hermes-agent** — validates "agent behind an OpenAI-compatible API + memory as
  facts/skills"; adopted as reference architecture (D4).
- **llama-swap** — the community-standard answer to multi-model on one GPU without
  Ollama; adopted (D1).
- **Community consensus on 24GB coding models** is qwen-coder-class models run via
  llama.cpp behind agent CLIs — which is exactly the qwen-code pairing (D3).

---

## 6. The SSE Protocol (frozen surface between backend and UI)

Fields shown as name — meaning. This is the whole protocol.

| Event | Payload | UI behavior |
|---|---|---|
| `status` | phase (`loading_model`, `summarizing`, `thinking`), detail | subtle inline status line under the draft bubble |
| `text` | delta string | append to draft bubble, re-render markdown at animation-frame cadence |
| `tool_start` | call id, tool name, pretty-printed args | insert collapsed tool block, spinner |
| `tool_result` | call id, truncated output, is_error | fill tool block, red styling on error |
| `artifact` | artifact identifier, version, title, type | open/refresh artifact panel |
| `error` | user-safe message | red banner on the bubble; draft text kept |
| `done` | model id, usage, memory_updates (list of remembered facts, may be empty) | finalize bubble: model label, timestamps, memory chips |

The stop button aborts the fetch stream; the backend detects disconnect, cancels the
model request and any running tool, and persists the partial transcript with a
`stopped` marker.

---

## 7. Build Phases — Step by Step

Rules of engagement (from the post-mortem): **serial phases; each ends demoable; a
phase with open bugs blocks the next; every bug fix lands with the automated repro
that would have caught it; new ideas go to `IDEAS.md`, not into the current phase.**
Line-count alarms: backend > ~4k lines or any file > ~500 lines ⇒ stop, simplify.

### Phase 0 — Iron: server, inference, model verification (≈1 day)

1. OS prep: NVIDIA driver + CUDA runtime; verify with the GPU visible in `nvidia-smi`.
   Create a dedicated non-root service user; create the data root (§4).
2. Build/install **llama.cpp** with CUDA; download candidate GGUFs (default generalist,
   coder, vision — §3) into `models/`.
3. Manually run llama-server with the default model; confirm: streams completions via
   its OpenAI endpoint; note VRAM used and tokens/s at your target context size.
   *This is where the recommended model either fits or gets replaced — decide now.*
4. Install **llama-swap**; write its YAML: one entry per model with the exact
   llama-server command (quant file, ctx size, `-ngl`, port var); default model in a
   resident group (no TTL), others TTL ~600s. Verify: requesting model A then model B
   through llama-swap's single endpoint swaps correctly; requesting A again reloads it.
5. **Tool-call smoke eval** (the I4 gate): 10 fixed prompts that each demand a tool call
   (given a small tool schema). Pass = ≥9 well-formed native tool calls, no
   JSON-in-prose. Run per model; record results in a text ledger; set each model's
   `tools` capability accordingly. A default model that fails is replaced, today.
6. systemd unit for llama-swap (auto-restart, boot-enabled). Install **SearXNG** (its
   own unit) and confirm JSON search responses locally.

**Gate:** curl-level streaming chat + verified tool calls through llama-swap surviving
a reboot. No app code exists yet, and that's correct.

### Phase 1 — Skeleton: backend, storage, model catalog (≈1–2 days)

1. FastAPI app skeleton: health endpoint; static file serving; SQLite schema (§4)
   created via a tiny migration mechanism (numbered migration files, applied at boot,
   version recorded — enough forever, no ORM).
2. Model catalog: seed from a config file into the `models` table (llama-swap base_url
   for locals; zen base_url + key env-var name for remotes); CRUD endpoints; a
   capability-probe action that pings an endpoint and runs the mini tool eval.
3. Chats/messages REST: create/list/rename/delete chat; list messages. Deleting a chat
   removes its files dir.
4. Shared-secret auth header for everything (LAN app, single user); bind to LAN
   interface; systemd unit for the App.

**Gate:** API-level chat/message CRUD works via curl; server survives reboot; DB file
and dirs appear where §4 says.

### Phase 2 — Chat core: agent loop + UI shell (≈3–4 days)

1. **Agent loop** (the heart; budget ~200 lines): assemble messages (system + history;
   context manager arrives Phase 4 — for now, raw history), stream from the selected
   model's endpoint, forward `text` deltas, handle `tool_calls` finish-reason (tools
   arrive Phase 3 — for now, loop is single-pass), emit `done`; *any* exception →
   `error` + `done` (I3). Persist assistant message with model attribution.
2. Frontend shell: hash router; layout (sidebar: new chat, chat list, nav; main:
   transcript + composer); dark theme via CSS custom properties; vendored markdown +
   sanitizer + highlighter wired into a message renderer (sanitize *after* markdown,
   always).
3. Streaming UX: draft bubble fed by the SSE reader; rAF-throttled re-render (lesson
   already paid for in the old repo); stop button with backend cancellation; error
   banner; auto-scroll with a "stick to bottom unless user scrolled up" rule.
4. Model dropdown in composer (from catalog, capability badges); per-message model
   labels; mid-chat switching per §5.1 including the `status: loading_model` display.
5. Chat title auto-generation (background call to local model after first exchange).
6. **E2E harness starts now:** Playwright script — open app, send message, see streamed
   reply, kill llama-swap mid-stream, see error banner, restart, chat again. Runs
   headless; this suite is the permanent regression gate and grows every phase.

**Gate:** the E2E script passes; a full day of casual use produces zero empty bubbles.

### Phase 3 — Tools (≈2–3 days)

1. Tool registry: one module per tool, each declaring JSON-schema + an async execute
   with its own timeout and output cap. v1 set: `bash`, `read_file`, `write_file`,
   `edit_file`, `grep`, `glob`, `web_search` (SearXNG), `web_fetch` (readability-style
   extraction, size cap), `search_history` (FTS over own messages).
2. Sandbox: every file/exec tool resolves paths inside the chat's (or project's) files
   dir only; bash runs as the service user, cwd pinned there, 60s hard kill, output
   truncated with a "truncated" marker the model can see.
3. Loop upgrade: multi-iteration (cap ~15), tool_calls executed with per-call
   `tool_start`/`tool_result` events, results appended in OpenAI tool-message format,
   loop continues until a text-only finish.
4. Transcript tool blocks (collapsed by default, expandable, error styling).
5. Prompt-injection posture: web_fetch/web_search output is data, not instructions —
   the system prompt says so explicitly, and the `remember` tool (Phase 5) will surface
   visible chips precisely so injected "remember that…" attempts are seen and killable.
6. E2E adds: a read-file task, a bash task, a web-search task, each asserting the tool
   block renders and the answer uses the result.

**Gate:** tool E2Es pass; per-tool overhead beyond model time is sub-second except
network tools.

### Phase 4 — Context manager (≈2 days)

Implement §5.3 exactly: token estimates on write; budget math per model; fresh window +
rolling summary; background incremental summarization after responses; tool-trace
compression first; transcript marker for the summary boundary.
Test with a deliberately tiny fresh-window setting so summarization triggers fast.
**Gate:** a 200-message synthetic chat stays coherent (asks about facts stated early get
correct answers via the summary) and every request stays under budget; switching to a
small-context model mid-chat compresses harder and still works.

### Phase 5 — Memory (≈2 days)

Implement §5.4: `memories` table + FTS5; `remember` tool; background extraction on
chat idle with dedupe; system-prompt memory block with the token cap; Settings →
Memory page (list/edit/archive, provenance); `done`-event memory chips in the UI.
**Gate:** state a preference in chat A ("answer in metric units"); new chat B honors
it; the fact is visible and deletable in Settings; deleting it stops the behavior.

### Phase 6 — Projects & attachments (≈2–3 days)

1. Projects: grid page, create/edit (name, instructions), project workspace = its
   chats + file list; project chats get instructions + file manifest in the system
   prompt; file tools scoped to the project dir.
2. Uploads: composer attach + drag-drop; extraction pipeline per §5.2; images as
   content parts with the vision-capability warning/suggestion flow.
3. E2E: create project, upload a doc, ask a question answerable only from the doc,
   assert correctness; attach an image on a vision model, assert a grounded answer.

**Gate:** the E2Es pass; a global chat can be "moved into" a project (chat's
project_id updated, files relocated).

### Phase 7 — Artifacts (≈2–3 days)

1. `create_artifact` / `update_artifact` tools (identifier, type, title, full content;
   updates create versions).
2. Side panel: opens on `artifact` events; renderers — html/svg in a sandboxed iframe
   (srcdoc, scripts allowed, no same-origin), code w/ highlighting, markdown, mermaid;
   version stepper; copy/download.
3. System-prompt guidance on *when* to use artifacts (substantial standalone content,
   not chat snippets) — cloned in spirit from Claude.ai's behavior.
4. E2E: "build a small HTML game" → panel renders v1; "change X" → v2 renders; stepper
   returns to v1.

**Gate:** E2Es pass; a malicious artifact (script trying to reach the App's API)
provably can't — the sandbox blocks it.

### Phase 8 — Code section via qwen-code (≈2–4 days, starts with verification)

1. **Verify first, design second:** install qwen-code; point it at the local coder
   model through llama-swap; use it in the terminal on a real repo for an hour. Then
   stand up `qwen serve` and map its actual HTTP+SSE/ACP surface: session create,
   prompt send, event stream shape, approval/permission flow. [UNCERTAIN today — this
   step converts it to FACT.] If the daemon proves awkward, fallback: opencode
   (equivalent role), or headless one-shot invocations per prompt. The App's Code UI
   is designed against whichever surface survives this hour.
2. App-side: a Code proxy (auth + same-origin wrapper around the daemon), a workspace
   registry (allow-listed repo directories), and a Code page: pick workspace → session
   transcript with the agent's plan/diff/command events rendered → approve/deny
   dangerous actions if the protocol exposes approvals.
3. Boundary rule: the Code section is a *window onto qwen-code* — the App does not
   reimplement any coding-agent logic (that's the mega-app trap).

**Gate:** from the browser: open a repo, ask for a small change, watch the agent work,
see the diff applied on disk, run the repo's tests from the same session.

### Phase 9 — Bench harness (≈2 days)

1. Suites as data files: `toolcall` (the Phase 0 eval, formalized), `code` (N small
   tasks w/ pass-fail checks), `longctx` (needle-style retrieval at several depths),
   `speed` (TTFT + tokens/s at fixed prompt sizes), `chat` (a few judged-by-you
   prompts, scored manually in the UI).
2. Runner: pick model(s) + suite(s) → executes via the same OpenAI dialect →
   `bench_runs`/`bench_results` with a free-text hardware note ("1×3090", "2×3090
   NVLink") — that note is what makes upgrade comparisons meaningful later.
3. Bench page: run, watch progress, compare any two runs side-by-side.
4. Standing rule: any model-choice change (new default, new quant, new hardware) is
   justified by a bench comparison, recorded in `IDEAS.md` or a decisions log.

**Gate:** two models benchmarked end-to-end from the UI; comparison view shows the
deltas; rerunning a suite on the same model reproduces scores within noise.

### Ops wrap-up (half day, alongside Phase 9)

systemd units for all four services, boot-enabled; nightly cron backup of the data
root (SQLite backup + rsync of files, 7-day rotation); logrotate on App logs; a
Settings → Status card showing each sidecar's health (llama-swap, SearXNG, qwen-code
daemon) with degraded-not-dead behavior (search down ⇒ tool returns a clean error,
chat unaffected).

---

## 8. Verification Ledger (all [UNCERTAIN] items and where they get resolved)

| Item | Resolved in |
|---|---|
| Qwen3-30B-A3B Q4 + 32k KV fits comfortably in 24GB | Phase 0.3 |
| Chosen default model passes the tool smoke eval | Phase 0.5 |
| llama-swap swap latency acceptable for user-driven switching | Phase 0.4 |
| `qwen serve` API is usable for an embedded web UI (else opencode/headless fallback) | Phase 8.1 |
| SearXNG result quality sufficient (else add a paid search key as provider #2) | Phase 3 use |
| FTS5-only memory retrieval is good enough (else embeddings, v2, on the 3070) | Phase 5 gate + lived use |
| hermes-agent as optional dropdown "brain" — streaming/tool-event fidelity | post-v1 experiment, contained by I2 |

## 9. Rough Timeline

Phases 0–2: first daily-drivable chat (~1 week of working sessions). Phases 3–5: tools,
context, memory (~1 week). Phases 6–7: projects + artifacts (~1 week). Phases 8–9: code
+ bench (~1 week). [INFERENCE — working-session estimates, not calendar promises.]

The order is deliberate: the highest-risk unknowns (model fit, tool calling, swap
behavior) burn down in Phase 0 before a single line of app code, and the two features
whose absence killed daily use of the old app — visible errors and instant chat — land
in the first buildable phase.
