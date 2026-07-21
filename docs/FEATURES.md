# AI Mega App — FEATURES.md (Full Build Document)

**Source of truth:** `/home/user/AI-Mega-App/PLAN.md` (Build Plan v2, 2026-07-20). This document expands every feature in PLAN.md into buildable detail. Where this file and PLAN.md disagree, PLAN.md wins.
**Post-mortem reference only:** `prompter_x_complete_spec.md` — its decisions (Ollama, LiteLLM, React, WSL2/8GB budgets) are banned.
**Confidence tags:** [FACT] verifiable · [INFERENCE] reasoned from PLAN.md · [UNCERTAIN] verify before building.

## Global conventions

- Backend: Python 3.12 + FastAPI + httpx + uvicorn under `app/`. Frontend: TypeScript compiled with plain `tsc` to native ES modules under `web/src/` → `web/js/`; no bundler, no framework. [FACT — PLAN §3.1]
- Every file ≤ ~300 lines (rule `002-modularity`). One module per feature; every feature has an `enabled:` flag in `config.yaml`.
- Every pipeline stage writes a debug span and every SSE stream terminates with `done` or `error` (rules `004-observability`, Bug-2 lesson). A feature PR without wiring + tests + docs is rejected (rule `005-integration` — "built but not injected" killed the old project).
- All model/provider names come from config, never code (rule `003-config`). Generated files (`llama-swap.yaml`, `opencode.json`) are machine-written, never hand-edited.
- No auth anywhere (trusted LAN) [FACT — owner decision]. Remote providers (opencode zen, Anthropic, Kimi) are v2/Future and appear here only as extension notes.
- Build order follows PLAN §5 phases: Part A (infrastructure) lands in Phase 1; features note their phase.

Section map: **Part A** = cross-cutting infrastructure PLAN.md defines (config, storage, SSE/orchestrator, frontend shell, debug tracing). **Part B** = the 19 critical spec items in spec order (F1–F19, per PLAN §4.1–§4.16).

---

# Part A — Cross-cutting infrastructure

## A1. Configuration system

**What.** `config.yaml` (checked-in **defaults** only) + `.env` (secrets only) + a machine-written `settings.local.yaml` overlay. **The Settings UI is the authoritative surface for every user change and writes the overlay (and secrets to `.env`); you never have to hand-edit a file — anything in the file is editable in the UI, and the UI wins.** This designs out the old "edit-a-file-and-a-menu" split. The one thing the UI writes to `.env` (not the overlay) is **API keys** for remote providers (Anthropic, opencode zen, Kimi, Tavily), redacted on read-back. Settings must "account for different models and configurations" — model roster + per-model device/tensor-split assignment (Models tab → swapgen), per-box `model → endpoint` map (multi-computer, future), BrowserOS MCP URL, tool toggles — each a tab-driven overlay edit, no code change. [FACT — PLAN §3.1, §4.14]

**Modules & files.**
- `app/config/loader.py` — load `config.yaml`, deep-merge `settings.local.yaml`, env-substitute from `.env`; expose typed `Config` object.
- `app/config/schema.py` — pydantic models for every section; validation errors fail startup loudly with the offending key path.
- `app/config/overlay.py` — read/write `settings.local.yaml` atomically (write-temp + rename); only writer is the Settings API.
- `config.yaml` — repo root, all defaults.

**Interfaces.**
- `load_config(path: Path = CONFIG_PATH) -> Config` — raises `ConfigError(key_path, reason)`.
- `apply_overlay(patch: dict) -> Config` — validates merged result before persisting; returns new live config.
- `GET /api/settings` → merged effective config (secrets redacted); `PATCH /api/settings` → overlay write + in-process hot-reload broadcast (`config_changed` event on an internal pub/sub).
- Every feature section carries `enabled: bool` — the registry pattern in A-modules checks it at startup *and* per request (hot toggle).

**Config keys.** Top-level sections (each detailed in its feature): `server`, `llm`, `models`, `routing`, `tools`, `rag`, `memory`, `projects`, `attachments`, `artifacts`, `sandbox`, `opencode`, `browseros`, `search`, `summaries`, `debug`, `ui`. Plus `server: {host: 0.0.0.0, port: 8000, static_dir: web/}`.

**Integration points.** Called by literally everything at startup; Settings UI (F4) writes it; swapgen (F14) and opencode config generator (F6) read it as input. Emits span `config.load` at startup with validation duration.

**Build steps.** 1) pydantic schema for Phase-1 sections only; 2) loader + merge + env substitution; 3) fail-loud validation with key paths; 4) overlay writer; 5) `/api/settings` GET/PATCH; 6) hot-reload pub/sub; 7) grow schema per phase.

**Tests.** pytest: valid config loads; unknown key rejected with path; overlay merge precedence (overlay > base); secret redaction in GET; PATCH round-trip changes live config object; malformed overlay leaves base intact.

**Failure modes.** Bad `config.yaml` → startup abort with exact key (never half-boot). Bad overlay → ignored with warning span + UI banner, base config used. `.env` missing a referenced secret → the *feature* using it disables itself (e.g. Tavily), app still boots.

---

## A2. SQLite storage core

**What.** One SQLite file (WAL mode) holds chats, messages, memories, chunks, vectors (sqlite-vec), FTS5 indexes, attachments metadata, review queue, and debug traces. Single-user scale; Qdrant deliberately removed. [FACT — PLAN §3.1]

**Modules & files.**
- `app/db/engine.py` — connection factory (WAL, `busy_timeout`, sqlite-vec extension load), one writer + reader pool via `aiosqlite`. [INFERENCE — aiosqlite fits FastAPI async; any thin async wrapper is acceptable]
- `app/db/schema.py` — `CREATE TABLE` DDL + versioned migrations (`PRAGMA user_version`), deterministic hand-written SQL (Key Rule 1).
- `app/db/chats.py`, `app/db/messages.py`, `app/db/memories.py`, `app/db/chunks.py`, `app/db/traces.py`, `app/db/attachments.py` — one repository module per aggregate, plain functions taking a connection.
- `app/db/vectors.py` — `VectorStore` interface (`upsert`, `query`, `delete_by_source`) + `SqliteVecStore` impl; Qdrant can return behind this interface if corpus outgrows sqlite-vec (>~1M vectors). [FACT — PLAN escape hatch]

**Interfaces.** Core tables (columns typed as SQLite affinities):
```sql
chats(id TEXT PK, project_id TEXT NULL, title TEXT, model_override TEXT NULL,
      summary TEXT NULL, created_at INT, updated_at INT, archived INT DEFAULT 0)
messages(id TEXT PK, chat_id TEXT, role TEXT, content TEXT, model TEXT NULL,
         trace_id TEXT NULL, tokens_in INT NULL, tokens_out INT NULL,
         attachments_json TEXT NULL, created_at INT)
memories(id TEXT PK, scope TEXT CHECK(scope IN ('user','project','global')),
         project_id TEXT NULL, content TEXT, tags TEXT, source TEXT,
         status TEXT DEFAULT 'active', created_at INT, updated_at INT)
chunks(id TEXT PK, project_id TEXT, source_path TEXT, heading TEXT NULL,
       content TEXT, token_count INT, mtime INT, chunk_index INT)
attachments(id TEXT PK, chat_id TEXT, filename TEXT, mime TEXT, stored_path TEXT,
            extracted_chars INT, status TEXT, created_at INT)
review_queue(id TEXT PK, kind TEXT CHECK(kind IN ('memory','instruction')),
             scope TEXT, project_id TEXT NULL, proposed TEXT, rationale TEXT,
             source_chat_id TEXT, status TEXT DEFAULT 'pending', created_at INT)
traces(trace_id TEXT PK, chat_id TEXT NULL, started_at INT, finished_at INT NULL, status TEXT)
spans(id INTEGER PK, trace_id TEXT, name TEXT, start_ms INT, end_ms INT NULL,
      model TEXT NULL, prompt TEXT NULL, response TEXT NULL,
      tokens_in INT NULL, tokens_out INT NULL, meta_json TEXT)
-- FTS5: messages_fts(content), memories_fts(content, tags), chunks_fts(content, heading)
-- sqlite-vec: vec_chunks(embedding float[DIM]), vec_messages(embedding float[DIM])  -- DIM from embed model config
```
`VectorStore`: `async upsert(ids: list[str], vecs: list[list[float]], meta: list[dict])`, `async query(vec, k: int, filter: dict) -> list[Hit]` where `Hit = (id, score, meta)`.

**Config keys.** `db: {path: data/app.db, wal: true, vec_extension: auto, backup_dir: data/backups}` (no `enabled:` — storage is not optional; the toggle rule applies to features, not the substrate).

**Integration points.** Every feature. FTS triggers keep `*_fts` in sync with base tables. Debug spans: `db.migrate` at startup. Phase 0 gate: sqlite-vec benchmarked at 100k chunks before committing [FACT — PLAN Phase 0].

**Build steps.** 1) engine + extension load + WAL pragmas; 2) v1 schema (chats/messages/traces/spans) for Phase 1; 3) FTS triggers; 4) repository modules; 5) migration runner; 6) vec tables + `VectorStore` in Phase 3; 7) 100k-chunk benchmark script `scripts/bench_sqlitevec.py` (Phase 0 deliverable).

**Tests.** pytest with tmp-file DB: migration idempotence; FTS trigger sync on insert/update/delete; vector round-trip (upsert → query returns nearest); concurrent writer+reader under WAL; `VectorStore` conformance suite runnable against any future impl.

**Failure modes.** sqlite-vec extension missing → vector features (RAG semantic, chat semantic search) disable with UI banner; FTS/BM25 path still works [INFERENCE — hybrid design degrades to lexical]. DB locked → `busy_timeout` retry; persistent lock → 503 with `error` SSE, never a hung stream.

---

## A3. Debug tracing core (backend half of spec §19 — BUILT FIRST)

**What.** Per-turn `trace_id`; every stage (route, rag, llm request/response, tool dispatch, swap wait, SSE emit) writes a span row to SQLite and mirrors it to a live SSE tap. This is Phase-1 infrastructure other features must call — retrofitting it killed the old build. [FACT — PLAN §4.16]

**Modules & files.**
- `app/debug/tracer.py` — `Tracer` (per-request), `span()` async context manager, writes rows + publishes to tap.
- `app/debug/tap.py` — in-process fan-out queue feeding `/api/debug/stream` subscribers; bounded, drop-oldest.
- `app/debug/api.py` — REST for stored traces + the SSE tap endpoint.
- `app/debug/gpu_poll.py` — background `nvidia-smi` poller (interval from config) publishing `gpu` events.
- `app/debug/swap_proxy.py` — proxies llama-swap's status/metrics API into the tap. [UNCERTAIN — exact llama-swap status endpoint path; pin against installed version in Phase 0]

**Interfaces.**
- `tracer = Tracer.begin(chat_id) -> Tracer` (mints `trace_id: str` uuid4); `async with tracer.span(name, **meta) as sp: sp.set(model=..., prompt=..., tokens_in=...)`. Span auto-records start/end ms and exception info on error.
- `GET /api/debug/traces?chat_id=&limit=` → trace list; `GET /api/debug/traces/{trace_id}` → full span tree (waterfall-ready: name, start_ms, end_ms, meta).
- `GET /api/debug/stream` (SSE) event types: `span` (a finished span row), `span_start`, `gpu` (`{index, name, mem_total_mb, mem_used_mb, util_pct}` per card), `swap_state` (llama-swap loaded/loading models), `log` (warnings). Terminates with `error` on server shutdown only.
- Canonical span names (contract, used everywhere): `route.override`, `route.rules`, `route.classifier`, `rag.retrieve`, `memory.inject`, `llm.request`, `llm.first_token`, `swap.wait`, `tool.<name>`, `needle.dispatch`, `sse.emit`, `title.generate`, `summary.update`, `compact.run`, `attach.extract`, `search.<provider>`, `browser.<tool>`, `opencode.session`, `memory.review`.

**Config keys.**
```yaml
debug:
  enabled: true            # off = spans not persisted; tap still emits errors
  store_prompts: true      # full prompt/response text in spans (toggle, PLAN §4.16)
  retention_days: 14
  gpu_poll_seconds: 5
  tap_buffer: 500
```

**Integration points.** Called by: orchestrator (A4), router (F5), RAG/memory (F10), tools (F9), llm client (F1), search (F16), attachments (F11), summaries (F18), opencode (F6), browser (F15). Consumed by: Debug view (F19). Rule 004: a PR adding a pipeline stage without a span is rejected.

**Build steps.** 1) span table + `Tracer` with context-manager API; 2) tap + `/api/debug/stream`; 3) wire into the Phase-1 chat path (route→llm→sse) so the first feature is born traced; 4) trace REST; 5) gpu poller; 6) swap proxy; 7) retention sweeper (daily, deletes old traces unless `store_prompts` archived); 8) span-name contract doc comment in `tracer.py`.

**Tests.** pytest: span nesting produces correct parent ordering by time; exception inside `span()` records error and re-raises; tap subscriber receives span within 100ms; retention deletes only expired; `debug.enabled: false` still lets chat flow (no-op tracer). Contract test: a fake chat turn produces the golden span-name sequence `route.* → llm.request → sse.emit`.

**Failure modes.** Trace write failure must never fail the turn — tracer catches, logs, continues (chat > observability). Tap backpressure → drop-oldest, `log` event notes drops. `nvidia-smi` absent → `gpu` events omitted, Debug view shows "no GPU telemetry".

---

## A4. SSE streaming contract + chat orchestrator

**What.** The core turn loop: resolve model → inject context (memory/RAG) → stream completion → run tool loop → persist → emit SSE. One orchestrator serving every surface (hermes lesson: platform differences live at the entry point). Hard rule from Bug 2: **every stream terminates with `done` or `error`.** [FACT — PLAN §4.2, §4.7]

**Modules & files.**
- `app/chat/orchestrator.py` — the turn loop (no tool logic, no routing logic — calls them).
- `app/chat/sse.py` — event encoder, heartbeat, terminal-event guarantee (finally-block emitter).
- `app/chat/context.py` — assembles system prompt + `<memory-context>` block + RAG block + compaction summary into the message list.
- `app/chat/api.py` — chat/message REST + the stream endpoint.
- `app/llm/client.py` — thin OpenAI chat-completions client to llama-swap (`model` field selects; llama-swap swaps). No scheduler code — the old ModelScheduler is deleted. [FACT — PLAN §4.1]

**Interfaces.**
- REST: `POST /api/chats` `{project_id?}` → chat; `GET /api/chats?project_id=`, `GET /api/chats/{id}/messages`, `PATCH /api/chats/{id}` (title, model_override, archived), `DELETE /api/chats/{id}`.
- Stream: `POST /api/chats/{id}/stream` body `{content: str, attachments: [id], model?: str}` → SSE. Event shapes (each `event:` + JSON `data:`):
  - `message_start {trace_id, message_id, model}`
  - `route {source: "override"|"rules"|"classifier"|"default", class, effort, model, latency_ms}`
  - `model_loading {model}` — emitted while llama-swap swaps the 3090 slot (3–10s typical [FACT — PLAN §4.1])
  - `delta {text}` · `thinking_delta {text}` (reasoner models)
  - `tool_call {id, name, args_json}` · `tool_result {id, name, summary, is_error}`
  - `artifact {id, kind, title}` (F8) · `citations {items: [{source, title, url?}]}` (F10/F16)
  - `title {chat_id, title}` · `usage {tokens_in, tokens_out, tok_per_s}`
  - `done {}` **or** `error {code, message, recoverable}` — exactly one, always, enforced in `sse.py` `finally`.
- `llm/client.py`: `async stream_chat(model: str, messages: list[dict], tools: list[dict]|None, response_format: dict|None) -> AsyncIterator[Chunk]`; `async embed(texts: list[str]) -> list[list[float]]`; `Chunk = delta|tool_call_delta|usage|done`.

**Config keys.**
```yaml
llm:
  base_url: http://127.0.0.1:8080/v1   # llama-swap
  timeout_s: 120
  first_token_timeout_s: 30            # covers swap wait; then error event
chat:
  enabled: true
  max_context_tokens: 32768
  heartbeat_seconds: 15
```

**Integration points.** Calls: router (F5), context/memory (F10, F17), tool loop (F9), tracer (A3), summaries (F18, fire-and-forget post-turn). Called by: web UI (F4). Spans: `llm.request` (with full prompt when enabled), `llm.first_token`, `swap.wait` (time between request and first token when llama-swap reports loading), `sse.emit`.

**Build steps.** 1) `llm/client.py` against real llama-swap with `curl`-verified contract; 2) SSE encoder with terminal guarantee + heartbeats; 3) minimal orchestrator (no tools/rag): route→stream→persist; 4) chat REST; 5) tracer wiring; 6) context assembler (Phase 3 grows it); 7) tool-loop hook (F9); 8) golden-transcript contract test.

**Tests.** pytest vs fake llama-swap (canned OpenAI responses): full turn emits golden event sequence; provider 500 mid-stream → `error` event, stream closed, message row marked failed (Bug-2 regression test); first-token timeout fires; heartbeats present in slow stream; `model_loading` emitted when fake reports loading. Playwright: send message, see streamed text, refresh, history persists.

**Failure modes.** llama-swap down → immediate `error {code: "llm_unreachable"}` + UI banner with retry. Mid-stream disconnect → partial message persisted with `status` note; UI shows "connection lost" if neither `done` nor `error` arrived (client-side rule, F4). Tool loop exceeding max iterations → loop stops, model asked to answer with what it has. [INFERENCE — standard cap behavior]

---

## A5. Frontend shell (TS modules, router, store, theme)

**What.** Hand-written SPA scaffolding: hash router, pub/sub store, SSE client with auto-reconnect, theme via CSS custom properties. Every view is a module exporting `mount(el, state)` / `unmount()`. [FACT — PLAN §4.2]

**Modules & files.**
- `web/src/router.ts` (~200 lines) — hash routes → view modules; `web/src/store.ts` (~150 lines) — typed pub/sub state.
- `web/src/sse.ts` — `EventSource`-style client over `fetch` (POST streams need fetch+ReadableStream [INFERENCE — native EventSource is GET-only]), auto-reconnect with backoff, terminal-event watchdog ("connection lost" if no `done`/`error`).
- `web/src/api.ts` — typed fetch wrappers for every REST endpoint; `web/src/types.ts` — shared interfaces mirroring backend event/DTO shapes.
- `web/src/markdown.ts` — `marked` + DOMPurify + `highlight.js` (vendored under `web/vendor/`); `web/css/theme.css` — all custom properties (Future themes = swap this file); `web/css/base.css`.
- `web/index.html` — loads `js/main.js` as `type="module"`; served by FastAPI static mount.

**Interfaces.** `interface View { mount(el: HTMLElement, state: Store): void; unmount(): void }`; `store.subscribe<K>(key, cb)` / `store.set(key, value)`; `sse.stream(url, body, handlers: Partial<Record<EventName, (data) => void>>) -> {abort()}`. Build: `tsc -p web/tsconfig.json` (`target: ES2022`, `module: ES2022`); CI runs `tsc --noEmit`.

**Config keys.** `ui: {enabled: true, theme: default, show_thinking: true}` (served to client via `GET /api/settings`).

**Integration points.** Hosts every view (F4, F19, Settings). Consumes A4 SSE contract verbatim — `types.ts` is the single place event shapes are duplicated; the golden-transcript contract test guards drift.

**Build steps.** 1) tsconfig + static serving; 2) router + store; 3) sse client with watchdog; 4) api/types; 5) markdown pipeline (sanitize-always); 6) theme.css tokens extracted from the approved static mock (F4); 7) wire Phase-1 chat view.

**Tests.** `tsc --noEmit` in CI; Playwright vs fake backend: route navigation mounts/unmounts views without leaks (event-listener count stable); killed stream shows "connection lost"; XSS payload in a message renders inert (DOMPurify proof).

**Failure modes.** SSE drop → reconnect with backoff, resume via history refetch (streams are not resumable; the message list is the source of truth [INFERENCE]). tsc output missing → FastAPI serves a "run `make web`" placeholder page rather than a blank screen.

---

# Part B — The 19 critical features (spec order)

## F1. llama.cpp + llama-swap inference layer (spec §1)

**What.** All inference is llama.cpp `llama-server` instances fronted by **llama-swap** (:8080): group `resident` (`swap: false`) pins classifier + embedder + needle + utility; group `gpu0-main` (`swap: true`) gives the 3090 an exclusive big-model slot. The backend speaks plain OpenAI chat-completions; llama-swap does every load/swap. Native router mode rejected (no group pinning). [FACT — PLAN §4.1]

**Modules & files.**
- `app/llm/client.py` — (A4) the only code path touching :8080.
- `app/llm/warmkeeper.py` — ~20-line policy: re-warm `chat-default` when the 3090 has idled on another model N minutes. [FACT — PLAN §4.1 "always loaded" policy]
- `app/llm/health.py` — startup + periodic: llama-swap reachable, resident group answering.
- `ops/llama-swap.service`, `ops/llama-server-build.md` — systemd unit + build notes (Phase 0 deliverables).
- Generated `llama-swap.yaml` — written only by swapgen (F14).

**Interfaces.** Upstream: `POST {base_url}/chat/completions` (stream), `POST {base_url}/embeddings`; llama-swap admin/status API proxied by `app/debug/swap_proxy.py` [UNCERTAIN — endpoint names; verify Phase 0]. Downstream: `GET /api/models` → `[{alias, class, device, resident, loaded, ctx}]` (merges config roster + live swap state).

**Config keys.**
```yaml
llm:               # see A4
  base_url: ...
warmkeeper:
  enabled: true
  model: chat-default
  idle_minutes: 10
```

**Integration points.** Everything model-shaped flows through here. Spans: `llm.request`, `swap.wait` (backend measures request→first-token and tags turns that crossed a swap), `llm.first_token`. `model_loading` SSE event sourced from swap state. Phase 0 measures real load times / tok/s and replaces guessed budgets. [FACT — PLAN Phase 0]

**Build steps.** 1) Phase 0: build llama.cpp, install llama-swap systemd, hand-write first `llama-swap.yaml`, curl-verify swap + concurrent residents; 2) record measured VRAM/load/tok-s doc; 3) `llm/client.py` + streaming parse; 4) health checks; 5) warmkeeper; 6) `/api/models`; 7) swap-state proxy into debug tap; 8) hand-written yaml replaced by swapgen output (F14) with byte-diff check.

**Tests.** pytest vs fake swap server: streaming parse incl. tool-call deltas and `usage`/`timings`; warmkeeper fires only after idle threshold; health degradation states. Live (`scripts/preflight.py`, not CI): every configured model loads and answers 1 token; embeddings endpoint alive. [FACT — PLAN §4.10]

**Failure modes.** llama-swap down → chat errors fast with actionable message; resident model crashed → llama-swap restarts it [FACT — llama-swap process management]; swap slower than `first_token_timeout_s` → `error {recoverable: true}` and UI offers retry; utility model (3070) serves instant fallback replies while 3090 swaps when router allows [FACT — PLAN §4.1 roster].

## F2. Model classes & roster (spec §2)

**What.** Consolidated ~8-model roster (4 big on the 3090 swap slot, 4 small residents) tagged with a `class:` (general, coding, tool, reasoning, vision) and placement. Old tier aliases (`coding-light/medium/heavy`, …) survive as routing labels pointing at this roster. Speed floor: nothing under ~25 tok/s at working quant. [FACT — PLAN §4.1 roster table]

**Modules & files.**
- `app/models/registry.py` — parse `models:` config into `ModelEntry` records; resolve routing label → alias; validate classes/placements.
- `app/models/api.py` — `/api/models` detail + per-model tok/s sanity numbers (from `llama-bench` script output file).
- `scripts/bench_models.py` — wraps `llama-bench` per configured model, writes `data/bench.json` (Critical scope = sanity numbers in debug panel; full suite is Future §4). [FACT — PLAN §4.1]

**Interfaces.** `ModelEntry {alias, class, gguf_path, device: "cuda:0"|"cuda:1"|"cpu", resident: bool, ctx: int, quant, tool_call: "native"|"weak", mmproj?: path, extra_args: []}`. `resolve_label(label: str) -> alias`. Roster defaults per PLAN: `chat-default` (Qwen3.6-35B-A3B or 27B, 3090 always-loaded), `coder` (Qwen3-Coder-30B-A3B, 16k/24k ctx = two entries same weights), `reasoner` (R1-32B vs thinking-MoE, Phase 0 A/B keeps one), `vision` (Gemma4-27B + mmproj), `utility` (Qwen3-8B, 3070), `embed` (3070), `classifier` (Qwen3-1.7B, CPU), `needle` (Cactus 26M, CPU).

**Config keys.**
```yaml
models:
  chat-default: {class: general, path: /models/qwen3.6-35b-a3b-q4.gguf, device: "cuda:0", resident: true, ctx: 32768, tool_call: native}
  coder:        {class: coding,  path: /models/qwen3-coder-30b-q4.gguf, device: "cuda:0", ctx: 16384}
  coder-24k:    {class: coding,  path: /models/qwen3-coder-30b-q4.gguf, device: "cuda:0", ctx: 24576}
  reasoner:     {class: reasoning, path: /models/r1-32b-q4.gguf, device: "cuda:0", ctx: 16384}
  vision:       {class: vision, path: /models/gemma4-27b-q4.gguf, mmproj: /models/gemma4-mmproj.gguf, device: "cuda:0"}
  utility:      {class: general, path: /models/qwen3-8b-q4.gguf, device: "cuda:1", resident: true}
  embed:        {class: embed, path: /models/nomic-embed-v2.gguf, device: "cuda:1", resident: true, embeddings: true}
  classifier:   {class: classifier, path: /models/qwen3-1.7b-q8.gguf, device: cpu, resident: true, ctx: 4096}
  needle:       {class: tool, path: /models/needle-q8.gguf, device: cpu, resident: true}
routing_labels: {coding-light: coder, coding-heavy: coder-24k, reasoning-heavy: reasoner, ...}
```
Per-model toggling = removing/commenting the entry or `enabled: false` on it; registry skips disabled entries and swapgen omits them.

**Integration points.** Input to swapgen (F14), router (F5), model picker (F4), Needle assist (`tool_call: weak` tag, F9). Users add models per class in Settings [FACT — owner decision 4]. Spans: none of its own; registry data annotates `route.*` and `llm.request` spans.

**Build steps.** 1) `ModelEntry` schema + validation (paths exist, one always-loaded per swap group); 2) label resolution; 3) `/api/models`; 4) Settings editor section (add/edit/disable model → overlay → swapgen regen); 5) bench script + `data/bench.json`; 6) Phase-0 A/B note for `reasoner`.

**Tests.** pytest: label→alias resolution; disabled model invisible to router and swapgen; duplicate device/resident conflicts rejected; ctx-variant entries share one weights path. Router eval (F5) exercises class coverage.

**Failure modes.** Missing GGUF path → that entry disabled with banner, others unaffected. A class with zero enabled models → router maps that class to `chat-default` and flags it in the debug panel. [INFERENCE — graceful-degradation carry-over]

## F3. Deployment topology & LAN access (spec §3)

**What.** The FastAPI backend runs *on* the Ubuntu 26.04 GPU box and serves the web UI; "the application that communicates to the Ubuntu computer" is the browser. Host-machine services (BrowserOS, optional opencode) are reached from the backend as clients of *their* servers over LAN. Web app, not desktop. No auth. [FACT — PLAN §3 deployment decision]

**Modules & files.**
- `app/main.py` — FastAPI app factory: config, DB, tracer, routers, static mount, lifespan startup/shutdown ordering.
- `ops/megaapp.service` — systemd unit (uvicorn); `ops/install.md` — box setup runbook (Phase 0/1 doc).
- `scripts/preflight.py` — live hardware check (nvidia-smi, llama-swap, models answer, embeddings). [FACT — PLAN §4.10]

**Interfaces.** `GET /health` → `{status, db, llama_swap, models_loaded, gpu: [...], versions}` with per-dependency `ok|degraded|down`. Static: `/` serves `web/`; `/api/*` is the whole API surface.

**Config keys.** `server: {host: 0.0.0.0, port: 8000}`; host-side service URLs live in their own sections (`opencode.hosts`, `browseros.url`).

**Integration points.** Startup order: config → db → tracer → llm health → registries → routes. Everything below assumes this topology; the two-box future adds a `model → endpoint` map in config, not a redesign. [FACT — PLAN §4.1 hardware note]

**Build steps.** 1) app factory + lifespan; 2) `/health`; 3) static serving; 4) systemd units; 5) preflight script; 6) install runbook; 7) firewall note (LAN-open by design, documented explicitly).

**Tests.** pytest: `/health` reflects injected fake dependency states; app boots with llama-swap down (degraded, not dead). Live: preflight green on the box before each phase demo.

**Failure modes.** Any dependency down → `/health` says which; UI banner driven by `/health` polling. Backend restart → SSE clients reconnect (A5); chats intact in SQLite.

## F4. Web application — claude.ai 1:1 UI (spec §4)

**What.** Mirror claude.ai web 1:1: collapsible left sidebar (new chat, Chats, Projects, recents), centered chat column, right-side artifact/context panel, model picker in the composer, per-message model label — plus Settings and Debug views (our additions). Static HTML/CSS mock built and approved **before any logic** (Bug-3 lesson). [FACT — PLAN §4.2]

**Modules & files.** (each view = one TS module + one CSS file, ≤300 lines)
- `web/src/views/chat.ts` — chat column, streaming render, message actions; `web/src/views/sidebar.ts`; `web/src/views/composer.ts` — input, model picker, attach button, tool toggles; `web/src/views/artifact_panel.ts` (F8); `web/src/views/projects.ts` + `project_detail.ts` (F7); `web/src/views/settings/*.ts` — one module per settings tab (models, routing, tools, memory, search, opencode, browseros, debug); `web/src/views/debug.ts` (F19); `web/src/views/code.ts` (F6).
- `web/mock/` — the approved static mock, kept as the parity reference.

**Interfaces.** Consumes A4 REST+SSE verbatim. Composer model picker: `GET /api/models` → dropdown; selection `PATCH /api/chats/{id} {model_override}` (null = "auto"). Per-message model label reads `messages.model`. `model_loading` event → composer shows "loading <model>…". Home = plain new chat (Bug-3 fix: Projects is a nav item, not a gate).

**Config keys.** `ui: {enabled: true, theme: default, show_thinking: true, artifact_panel_default: auto}`.

**Integration points.** Front door for every feature. Emits nothing server-side itself; client logs surface via `POST /api/debug/client_log` → `log` tap events [INFERENCE — cheap and consistent with debug-first].

**Build steps.** 1) static mock of claude.ai layout → owner approval gate; 2) extract `theme.css` tokens from mock; 3) sidebar + chat + composer with live SSE (Phase 1); 4) model picker + per-message labels + `model_loading` UX (Phase 2); 5) Settings shell + models tab (Phase 2); 6) projects/artifacts/memory views (Phase 3); 7) Code area (Phase 4); 8) parity audit vs mock each phase exit.

**Tests.** Playwright vs fake backend (~10 flows, PLAN §4.10): send/stream/persist; switch model mid-chat and see label change; new chat from home; sidebar recents update; settings PATCH round-trip; artifact renders; debug view populates; upload flow; "connection lost" on killed stream; project create→chat.

**Failure modes.** Any API 5xx → non-blocking toast + banner from `/health`; UI never white-screens on missing data (empty-state components per view). Old-browser (no ES2022) → unsupported-browser notice. [INFERENCE]

## F5. Smart router (spec §5)

**What.** Three strictly ordered layers resolve the model per turn: (1) per-chat manual override always wins; (2) deterministic rules (attachment types force intents; config keyword rules, word-boundary, 2+ words); (3) grammar-constrained classifier (~1.7B CPU resident) emitting schema-enforced JSON. Every decision emitted to the debug panel with source + latency. [FACT — PLAN §4.3]

**Modules & files.**
- `app/router/router.py` — the ordered pipeline, single entry `route()`.
- `app/router/rules.py` — attachment→intent forcing + keyword rule engine (compiled word-boundary regexes from config).
- `app/router/classifier.py` — prompt build (~600 tokens + few-shots), llama.cpp `response_format: json_schema` call, timeout, confidence gate.
- `app/router/resolve.py` — `{class, effort}` → model alias via `routing:` table; classifier **never names models** (old spec's mistake). [FACT — PLAN §4.3]
- `eval/router_eval.csv` + `scripts/eval_router.py` — labeled prompt→expected-route set + scorer.

**Interfaces.**
- `async route(chat: Chat, user_msg: str, attachments: list[Attachment], tracer) -> RouteDecision`
- `RouteDecision {model: str, source: "override"|"rules"|"classifier"|"default", class: str, effort: "light"|"heavy", needs_tools: list[str], confidence: float|None, latency_ms: int}`
- Classifier JSON schema (GBNF-enforced by llama.cpp — malformed JSON structurally impossible [FACT]):
```json
{"class": "general|coding|tool|reasoning|vision", "effort": "light|heavy", "needs_tools": ["web_search", "..."], "confidence": 0.0}
```
- SSE `route` event (A4) mirrors `RouteDecision`.

**Config keys.**
```yaml
routing:
  enabled: true                  # off = chat-default for everything (override still honored)
  default_model: chat-default
  classifier:
    enabled: true
    model: classifier
    timeout_ms: 2000             # timeout → default model
    confidence_threshold: 0.6    # below → default model, flagged in debug
  rules:
    - {keywords: ["write code", "stack trace"], class: coding}
    - {attachment: image, class: vision}       # forced intents
    - {attachment: code_file, class: coding}
  table: {general.light: utility, general.heavy: chat-default, coding.light: coder,
          coding.heavy: coder-24k, reasoning.heavy: reasoner, vision.heavy: vision,
          tool.light: chat-default}
```

**Integration points.** Called by orchestrator before `llm.request`. Reads model registry (F2), attachment types (F11). Spans: `route.override` / `route.rules` / `route.classifier` (with raw classifier output + confidence). Phase-2 exit gate: ≥90% on eval set. Future upgrade path: ModernBERT-style head behind the same `route()` interface [INFERENCE — PLAN §4.3.4].

**Build steps.** 1) `RouteDecision` + layer-1 override (Phase 1 ships with override-only); 2) rules engine; 3) classifier prompt (~600 tokens, few-shots) + json_schema call; 4) timeout/confidence fallbacks; 5) resolve table; 6) eval CSV (seed from old repo's labeled set, relabeled to class/effort — no model names); 7) `eval_router.py` in CI-optional job (needs classifier model; run on prompt/model change); 8) wire `route` SSE event + spans.

**Tests.** pytest (no model): override beats everything; rules fire on word-boundary only ("scode" ≠ "code"); attachment forcing; classifier timeout → default; low confidence → default + flag; unknown class from schema impossible by construction but resolver still defends. Eval: ≥90% accuracy gate. Playwright: picking a model in composer shows `source: override` in debug view.

**Failure modes.** Classifier process dead → 2s timeout → default model, `route.classifier` span records failure; chat never blocks on routing. Rules misconfig (bad regex) → that rule skipped with startup warning. `routing.enabled: false` → everything is `default_model`, still traced.

## F6. opencode integration (spec §6)

**What.** `opencode serve` runs as a systemd unit on the Ubuntu box (optionally also user-launched on the Windows host); the backend delegates **repo/directory-scoped coding work** to it via its session API and streams session events into a "Code" area. Dividing line: no workspace → chat model + artifact sandbox (F8); real directory/repo on the box → opencode session. opencode is never called as a tool-RPC for one-off ops (its API is session-based [FACT]), and never nested silently inside the chat tool loop — the router *suggests* delegation, the user confirms. [FACT — PLAN §4.4]

**Modules & files.**
- `app/opencode/client.py` — httpx client for the OpenAPI surface (create session, prompt, list, events); version pinned.
- `app/opencode/api.py` — our REST façade + SSE relay of session events.
- `app/opencode/confgen.py` — deterministic writer of `opencode.json` on both machines: provider = llama-swap `/v1` (custom OpenAI-compatible), models from our roster; documents-and-writes the zen (hosted) switch too. Never AI-generated. [FACT — PLAN §4.4, Future §8 rule]
- `web/src/views/code.ts` — session list, "delegate to opencode" flow (directory picker limited to registered project/repo paths), event viewer, "Open in VS Code" deep-link.
- `docs/opencode.md` — provider switching local↔zen runbook (owner deliverable §9). [FACT]

**Model policy (owner decision 2026-07-20):** opencode's default profile = local `coder` (Qwen3-Coder-30B-A3B via llama-swap `/v1`) — free, private, agentic-tuned, the strongest 24GB-local option [FACT — 2026 local rankings]. A second opencode profile points at **zen DeepSeek V4 Flash** (284B MoE, 1M ctx, $0.14/$0.28 per M [FACT]) as the *escalation*, chosen per-session by the user under the rule: **local first → Flash on context-overflow or one failed local attempt → V4 Pro only on Flash failure.** Zen enters only through opencode's own config — the chat app stays fully local; this does not pull the v2 remote-provider registry forward. Flash's free tier is temporary — don't design defaults around it. [FACT]

**Interfaces.**
- `POST /api/code/sessions {host: "ubuntu"|"windows", directory: str, prompt: str}` → `{session_id}`; `GET /api/code/sessions`; `GET /api/code/sessions/{id}/events` (SSE relay: pass-through opencode events wrapped as `oc_event {type, data}`, terminated by `done`/`error` per our contract).
- `client.py`: `create_session(dir) -> Session`, `send_prompt(session_id, text)`, `stream_events(session_id) -> AsyncIterator[dict]`. [UNCERTAIN — exact opencode endpoint paths/event shapes; pin version and smoke-test in Phase 4 before building UI (PLAN §4.4)]
- Delegation suggestion: orchestrator surfaces a `tool_call`-like SSE hint `{name: "delegate_opencode", args: {directory}}` that renders as a confirm chip, never auto-executes.

**Config keys.**
```yaml
opencode:
  enabled: true
  hosts:
    ubuntu: {url: "http://127.0.0.1:4096", enabled: true}
    windows: {url: "http://<host-ip>:4097", enabled: false}
  version_pin: "x.y.z"
  confgen: {provider: local-llamaswap, zen_api_key_env: OPENCODE_ZEN_KEY}
  allowed_roots: [/home/user/repos, /home/user/AI-Mega-App/projects]
```

**Integration points.** Reads model roster (F2) for confgen; Code view (F4); router (F5) may set `needs_tools: ["delegate_opencode"]` as a suggestion signal [INFERENCE]. Spans: `opencode.session` (create/prompt/close with session_id), per-event relay counted in span meta. VS Code integration is docs-only (opencode's own extension) [FACT — PLAN §4.4.3].

**Build steps.** 1) Phase 4: install + pin opencode on box, systemd unit; 2) confgen (llama-swap provider) + byte-diff test; 3) smoke-test session API with curl, record shapes; 4) `client.py`; 5) REST façade + SSE relay with terminal guarantee; 6) Code view; 7) delegation confirm flow in chat; 8) `docs/opencode.md` incl. zen switch both directions; 9) optional Windows host registration in Settings.

**Tests.** pytest vs recorded/fake opencode server: session create/prompt/event relay; relay always terminates; disabled host rejected; confgen golden-file test (local and zen variants); `allowed_roots` enforcement (path traversal rejected). Playwright: delegate flow requires explicit confirm; session events render.

**Failure modes.** opencode down → Code area shows offline, chat coding unaffected (falls back to chat-model + sandbox). Version drift breaking API → pinned version + smoke test at startup flips `opencode.enabled` effective-off with banner. Directory outside `allowed_roots` → 400.

## F7. Projects (spec §7)

**What.** Claude.ai-style projects: grid → workspace with instructions, sources/files, project chats, project memory. Filesystem-first (`projects/<id>/instructions.md`, `docs/`) — the one part of the old app that worked — but chats/messages live in SQLite. Ingestion into RAG is incremental on file mtime. [FACT — PLAN §4.5]

**Modules & files.**
- `app/projects/store.py` — project CRUD over the filesystem + a `projects` row cache in SQLite for listing (`projects(id, name, path, created_at)`); `app/projects/ingest.py` — mtime scan → chunker (F10) → embed → sqlite-vec/FTS, per-file incremental; `app/projects/api.py` — REST + file upload into `docs/`.
- `web/src/views/projects.ts` (grid), `web/src/views/project_detail.ts` (instructions editor, file list with ingest status, project chats, project memory tab).

**Interfaces.** `POST /api/projects {name}` → creates `projects/<slug>/` with `instructions.md` + `docs/`; `GET /api/projects`; `GET/PUT /api/projects/{id}/instructions`; `POST /api/projects/{id}/files` (multipart → `docs/`); `GET /api/projects/{id}/files` → `[{path, mtime, ingested: bool, chunks: int}]`; `POST /api/projects/{id}/reingest`. Chats link via `chats.project_id`; project chats inherit instructions (F17 injection) + project RAG scope (F10).

**Config keys.**
```yaml
projects:
  enabled: true
  root: projects/
  auto_ingest: true          # ingest on upload + on mtime change (poll on project open)
  max_file_mb: 50
```

**Integration points.** Feeds RAG (F10 scope filter `project_id`), memory scope (F17), opencode `allowed_roots` candidate (F6), file_ops tool scope (F9). Spans: `rag.ingest {project_id, file, chunks, ms}`. Home stays plain new chat — Projects is nav, not a gate (Bug-3 fix). [FACT]

**Build steps.** 1) filesystem layout + store + SQLite cache; 2) REST; 3) grid + detail views; 4) instructions injection wiring (F17); 5) ingest pipeline hookup (F10); 6) mtime incremental re-ingest; 7) project-scoped retrieval filter proof-test.

**Tests.** pytest: create → dirs exist; instructions round-trip; upload → chunks appear scoped to project; mtime bump → only that file re-ingested; deleting file → chunks removed (`delete_by_source`). Playwright: create project, upload doc, ask a question in a project chat, answer cites the doc (the end-to-end wiring proof).

**Failure modes.** Ingest failure on one file → file marked `error` in UI, others proceed. Filesystem/SQLite cache drift → `reingest` rebuilds cache from disk (disk is truth). Embedder down → ingest queues, FTS-only retrieval meanwhile. [INFERENCE]

## F8. Artifacts + sandboxed execution (spec §8)

**What.** Two toggleable tiers. Tier 1 (client, Phase 3): artifact panel rendering markdown/HTML/SVG/JS in a sandboxed iframe (`sandbox="allow-scripts"`, no same-origin) and Python via Pyodide in a web worker — Claude.ai-artifact parity, zero server risk. Tier 2 (server, Phase 4): `POST /api/exec` runs code in a short-lived locked-down Docker container for the `run_code` tool and dep-needing artifacts. In-chat artifacts always use the **chat model** — never opencode. [FACT — PLAN §4.6, §4.4.1]

**Modules & files.**
- `app/artifacts/detect.py` — fenced-block → artifact extraction from streamed completions (kind: html/svg/js/python/markdown/mermaid), stable artifact ids per chat.
- `app/sandbox/docker_exec.py` — container lifecycle: `--network none`, mem/cpu/pids limits, read-only rootfs + tmpfs workdir, 30s timeout; images `sandbox-python`, `sandbox-node`; `app/sandbox/api.py` — `/api/exec`.
- `ops/sandbox/Dockerfile.python`, `ops/sandbox/Dockerfile.node`.
- `web/src/views/artifact_panel.ts` — right-panel tabs, version history per artifact id; `web/src/artifacts/iframe_host.ts` — srcdoc sandboxed iframe, postMessage console capture; `web/src/artifacts/pyodide_runner.ts` — worker bootstrap, stdout/plot capture.

**Interfaces.** SSE `artifact {id, kind, title}` then panel fetches `GET /api/chats/{id}/artifacts/{aid}` → `{kind, content, version}`. `POST /api/exec {lang: "python"|"node"|"bash", code, stdin?, timeout_s<=30}` → `{exit_code, stdout, stderr, duration_ms, files: [{name, b64}]}` (small outputs only, cap in config). Pyodide runner: `run(code) -> {stdout, stderr, result}` via worker postMessage.

**Config keys.**
```yaml
artifacts:
  enabled: true
  tier1: {enabled: true, pyodide: true}
sandbox:
  enabled: true              # Tier 2; also gates run_code tool
  images: {python: sandbox-python:latest, node: sandbox-node:latest}
  timeout_s: 30
  mem_mb: 512
  cpus: 1.0
  pids: 128
  network: none
  max_output_kb: 256
```

**Integration points.** Orchestrator pipes deltas through `detect.py`; `run_code` tool (F9) calls `docker_exec`; Phase-5 sandbox audit hardens it (no backend auth, but tool-executed code is not the owner — sandbox stays locked [FACT — PLAN Phase 5]). Spans: `tool.run_code` (container id, limits, exit), `artifact.detect`.

**Build steps.** 1) iframe host + panel with static content; 2) stream detection → panel live-update; 3) Pyodide worker; 4) Docker images; 5) `docker_exec` with full limit set; 6) `/api/exec` + `run_code` tool wiring; 7) audit checklist (escape attempts, resource bombs); 8) toggles verified end-to-end.

**Tests.** pytest: detection extracts artifacts from golden streams; exec enforces timeout (sleep 60 → killed), memory cap (alloc bomb → OOM-killed), no network (`curl` fails inside), read-only rootfs (write outside tmpfs fails), output cap. Playwright: HTML artifact renders in panel; JS `alert` sandboxed; Python artifact runs via Pyodide and shows stdout; `sandbox.enabled: false` → `run_code` absent from tool list (toggle wiring proof).

**Failure modes.** Docker daemon absent → Tier 2 + `run_code` disabled with banner; Tier 1 unaffected. Pyodide load failure (large wasm) → panel shows code with "run unavailable". Runaway container → hard timeout + `docker kill`; orphan sweep at startup. [INFERENCE]

## F9. Tool calls + Needle dispatcher (spec §9, §9.1)

**What.** Primary path: llama.cpp native tool calling (`--jinja` + model chat template) through the OpenAI `tools` API; the orchestrator runs accumulate-deltas → dispatch → append-result, max N iterations. Tools are self-describing modules auto-discovered into a registry, each toggleable. **Needle assist:** for models tagged `tool_call: weak`, the call-emission step routes to resident Needle (26M) — Needle is the dispatcher, never the planner; anything branching on results stays with the main model. [FACT — PLAN §4.7]

**Modules & files.**
- `app/tools/registry.py` — auto-discover `app/tools/impl/*.py`, filter by `enabled`, render OpenAI tool schemas.
- `app/tools/loop.py` — delta accumulation (reuse old build's correct merge *pattern*), dispatch, result append, iteration cap.
- `app/tools/needle.py` — Needle call-emission: query + tool schemas → one JSON call; used per-step when the active model is `tool_call: weak`, or in fixed pipelines where the next step is mechanically determined.
- `app/tools/impl/web_search.py`, `fetch_url.py`, `file_ops.py` (project-scoped read/list/grep, ~100 lines), `run_code.py`, `browser.py` (F15), `memory_save.py`, `memory_search.py` — one module each: `name`, `description`, `schema` (JSON Schema), `async execute(args, ctx) -> ToolResult`, `enabled` (config-bound), `consequential: bool` (browser=true → per-chat opt-in).

**Interfaces.** `ToolResult {content: str, is_error: bool, meta: dict}`; `ctx` carries `chat, project_id, tracer, config`. Loop contract: max `tools.max_iterations`; every dispatch emits SSE `tool_call`/`tool_result` and span `tool.<name>` (args, duration, error). Needle: `emit_call(query: str, tools: list[schema]) -> {name, arguments}` — single shot, no chaining [FACT — Cactus's own framing]. Fine-tune plan: after registry stabilizes (end Phase 3), fine-tune Needle on our schemas (~120 examples/tool), adopt only if it beats untuned baseline; schema changes mean retraining. [FACT — PLAN §4.7]

**Config keys.**
```yaml
tools:
  enabled: true
  max_iterations: 6
  needle_assist: {enabled: true, model: needle}   # applies to models tagged tool_call: weak
  web_search: {enabled: true}
  fetch_url: {enabled: true, timeout_s: 20, max_bytes: 2000000}
  file_ops: {enabled: true}          # scope = project dirs only
  run_code: {enabled: true}          # requires sandbox.enabled
  browser: {enabled: false}          # off by default, per-chat toggle (F15)
  memory: {enabled: true}            # memory_save/search pair
```

**Integration points.** Called by orchestrator (A4); tools call search (F16), sandbox (F8), BrowserOS (F15), memory (F10/F17), projects fs (F7). Router's `needs_tools` pre-warms nothing but is logged. Debug panel marks Needle-assisted turns and shows per-step *who decided vs. who emitted*. [FACT — PLAN §4.7]

**Build steps.** 1) registry + schema render + toggle filtering; 2) loop with delta merge + cap; 3) `web_search`/`fetch_url`/`file_ops` (Phase 3 first wave) **wired into the orchestrator the same PR** (Bug-1 regression rule); 4) SSE + span emission; 5) `run_code` (Phase 4); 6) Needle client + weak-model routing; 7) `memory_save/search`; 8) `browser` (Phase 5); 9) Needle fine-tune experiment (post-Phase-3).

**Tests.** pytest vs fake LLM emitting tool-call deltas: split-across-chunks arguments merge correctly; unknown tool → `is_error` result fed back, loop continues; cap stops runaway loop; disabled tool absent from schemas AND dispatch rejects it (double gate); Needle path invoked only for `tool_call: weak` models; Needle output validated against schema before execution. **Startup wiring test:** every enabled tool in config appears in the live registry (the anti-Bug-1 test). Playwright: ask "search the web for X" → visible tool chips → cited answer.

**Failure modes.** Tool exception → `is_error` ToolResult to the model (it can recover verbally), never a dead stream. Needle emits invalid call → schema validation fails → fall back to main-model emission for that step, flagged in debug. All tools disabled → plain chat, tools chip hidden.

## F10. RAG + memory (spec §10) — hermes-agent style

**What.** RAG: per-project ingestion → heading-aware chunking (~512 tokens, 20% overlap) → embeddings via resident embed model → sqlite-vec + FTS5 → hybrid retrieval (vector + BM25, reciprocal-rank fusion) → top-k with source citations in the UI. Memory: discrete fact rows in SQLite (FTS5 + optional embedding), three scopes (user / project / global), injected as a tagged `<memory-context>` block hermes-fashion; all visible/editable in Settings → Memory. Self-improvement: post-turn background review on the utility model *proposes* writes into a review queue (auto-accept optional per scope). [FACT — PLAN §4.8]

**Modules & files.**
- `app/rag/chunker.py` — heading-aware markdown/text chunker (tree-sitter AST chunking for code is a later drop-in); `app/rag/embed.py` — batch embed via `llm.client.embed`; `app/rag/retrieve.py` — hybrid query + RRF + k; `app/rag/cite.py` — citation assembly for SSE.
- `app/memory/store.py` — fact CRUD per scope; `app/memory/inject.py` — build `<memory-context>` block (user-scope always injected per spec §17; project scope in project chats; global on relevance match); `app/memory/reviewer.py` — post-turn utility-model job → `review_queue` proposals (Phase 5); `app/memory/api.py` — REST for Settings → Memory + review queue.
- `web/src/views/settings/memory.ts` — list/edit/delete facts per scope, review-queue accept/reject.

**Interfaces.**
- `retrieve(query: str, project_id: str|None, k: int) -> list[Hit]`; `Hit {chunk_id, source_path, heading, content, score, origin: "vec"|"fts"|"both"}`. RRF: `score = Σ 1/(60 + rank_i)` over the two rank lists. [INFERENCE — standard RRF constant]
- Injection block format (hermes-fashion, tagged):
```
<memory-context>
[user] Prefers concise answers.
[project:mega-app] Uses tsc only, no bundler.
</memory-context>
```
appended inside the user message [FACT — hermes injects into the user message, PLAN §4.8].
- REST: `GET/POST/PATCH/DELETE /api/memory?scope=&project_id=`; `GET /api/review-queue`, `POST /api/review-queue/{id} {action: accept|reject}`.
- SSE `citations` event lists retrieval sources rendered under the message.

**Config keys.**
```yaml
rag:
  enabled: true
  chunk_tokens: 512
  overlap_pct: 20
  top_k: 6
  hybrid: {vec_weight: 1.0, fts_weight: 1.0, rrf_k: 60}
memory:
  enabled: true
  inject: {user: always, project: in_project_chats, global: on_match}
  reviewer:
    enabled: false           # Phase 5; queue-first, never silent writes
    model: utility
    auto_accept: {user: false, project: false, global: false}
```

**Integration points.** Context assembler (A4) calls `inject` + `retrieve` before `llm.request`; `memory_save/search` tools (F9) write/read the same store; chat-history embedding (F13) reuses embed/retrieve. Spans: `rag.retrieve` (query, k, hit ids, ms), `memory.inject` (counts per scope), `memory.review` (proposal or no-op). Reviewer failures never block chat (background). [FACT]

**Build steps.** 1) chunker + golden-file chunk tests; 2) embed batch path; 3) vec+FTS dual write on ingest (F7); 4) hybrid retrieve + RRF; 5) citations end-to-end (SSE→UI); 6) memory store + manual CRUD UI; 7) inject wiring with scope rules; 8) `memory_save` tool (Phase 3 manual path); 9) reviewer + queue (Phase 5); 10) auto-accept per-scope toggles.

**Tests.** pytest: chunker respects headings/overlap; hybrid beats either-alone on a fixture set (known needle docs); RRF ordering deterministic; scope injection matrix (user always / project only in project chat / global on match); reviewer proposal lands in queue and is NOT injected until accepted; accepted memory appears in next turn's `<memory-context>` (full wiring test). Playwright: save a preference via chat ("remember I like X") → visible in Settings → next chat reflects it.

**Failure modes.** Embed model down → ingest queues + retrieval degrades to FTS-only (flagged in citations meta). Reviewer error → dropped silently into a `log` tap event; chat unaffected. Oversized memory set → injection capped by token budget, lowest-relevance dropped, span notes truncation. [INFERENCE]

## F11. Attachments (spec §11)

**What.** Upload → type sniff → extractor registry: text/code direct, PDF (pymupdf), docx/xlsx/pptx (python-docx family or markitdown), images → vision model path; audio is Future. Small extractions go straight to context; large ones become RAG-on-the-fly for that chat. Each extractor is one module. [FACT — PLAN §4.9]

**Modules & files.**
- `app/attachments/api.py` — `POST /api/upload` (multipart, streams to `data/uploads/`), status endpoint; `app/attachments/sniff.py` — magic-bytes + extension typing; `app/attachments/registry.py` — extractor discovery/dispatch, mirrors tool-registry pattern; `app/attachments/extract/text.py`, `pdf.py`, `office.py`, `image.py` — one module each, `supports(mime) -> bool`, `async extract(path) -> Extraction`.

**Interfaces.** `POST /api/upload` → `{attachment_id, filename, mime, status: "ready"|"extracting"|"error", extracted_chars}`; composer sends `attachments: [id]` in the stream request. `Extraction {text: str|None, images: list[path], meta}`. Threshold logic in `app/chat/context.py`: `extracted_chars <= attachments.inline_max_chars` → inline block; larger → chunk+embed into chat-scoped ephemeral chunks (`chunks.project_id = "chat:<id>"`) retrieved like RAG. Image attachments force `class: vision` in router rules (F5).

**Config keys.**
```yaml
attachments:
  enabled: true
  max_mb: 50
  inline_max_chars: 12000
  extractors: {text: true, pdf: true, office: true, image: true}
```

**Integration points.** Router forced intents (F5), context assembly (A4), RAG machinery reuse (F10), vision model path (F2). Spans: `attach.extract {mime, chars, ms, extractor}`.

**Build steps.** 1) upload endpoint + storage + attachments table; 2) sniffer; 3) text/code extractor; 4) pdf; 5) office; 6) image→vision wiring (message content parts with image path → llama.cpp multimodal request [UNCERTAIN — exact multimodal payload shape for the installed llama-server; verify in Phase 3]); 7) inline-vs-RAG threshold; 8) composer UI + per-message attachment chips.

**Tests.** pytest with fixture files: each extractor golden-text output; oversize rejected 413; unknown type → stored, marked `error: unsupported`, chat proceeds without it; big PDF → ephemeral chunks retrievable in that chat only. Playwright: upload PDF, ask about its content, answer cites it; upload image, router picks vision (debug view shows forced rule).

**Failure modes.** Extractor crash → attachment marked error, user told inline, turn proceeds. Disabled extractor type → upload accepted but marked unsupported (predictable, visible). Vision model unavailable → image acknowledged with "vision unavailable" system note instead of silent drop.

## F12. Testing suite (spec §12)

**What.** The layered harness proving wiring, not just units: pytest vs fake llama-swap; golden SSE transcript contract tests; router eval CSV; Playwright E2E vs fake backend (no GPU in CI); live `preflight.py` on the box. Gate: no feature merges without its tests; CI = ruff + `tsc --noEmit` + pytest + Playwright-vs-fake. [FACT — PLAN §4.10]

**Modules & files.**
- `tests/fakes/fake_llamaswap.py` — ASGI app serving canned OpenAI streams (plain, tool-call, error-mid-stream, slow, loading-then-serve scripts selected per test).
- `tests/contract/golden/*.jsonl` — golden SSE transcripts; `tests/contract/test_sse_golden.py` — diff on change (intentional changes update goldens explicitly).
- `tests/unit/…`, `tests/integration/…` — per-feature (named in each section above).
- `e2e/playwright/*.spec.ts` + `e2e/fake_backend.py` (full app wired to fakes).
- `eval/router_eval.csv`, `scripts/eval_router.py` (F5); `scripts/preflight.py` (F3); `.github/workflows/ci.yml`.

**Interfaces.** Fake selection via header `X-Fake-Script: tool_loop_2step` [INFERENCE — simplest deterministic control]. Golden transcript = ordered `(event, data-shape)` list with volatile fields (ids, timestamps, latencies) masked. Preflight exit codes: 0 ok / 1 degraded / 2 dead, printed as a table.

**Config keys.** `testing:` not needed in `config.yaml`; tests inject their own config fixtures (`tests/fixtures/config.test.yaml`). CI never needs a GPU by design. [FACT]

**Integration points.** Rule 005 enforcement: PR template requires "wiring test" checkbox; the registry-vs-config startup assertions (F9-style) run inside pytest for tools, extractors, search providers, and views (Playwright checks each nav item mounts).

**Build steps.** 1) fake llama-swap + first golden transcript (Phase 1, day one); 2) CI pipeline; 3) contract-test harness with masking; 4) Playwright + fake backend boot fixture; 5) router eval job (manual-trigger, needs classifier); 6) preflight; 7) per-phase: extend goldens for tool loop, artifacts, citations.

**Tests.** (meta) The harness tests itself: golden masking stability (two runs, zero diff); fake scripts cover: happy, error-mid-stream, tool loop, swap-loading, timeout.

**Failure modes.** Flaky Playwright → retries=1 max, flake beyond that is a bug to fix, not mask. Goldens drifting silently → CI fails on any transcript diff; update requires explicit file change in the PR.

## F13. Vector DB & chat-history search (spec §13)

**What.** sqlite-vec is the vector store (decided in §3.1/A2; Qdrant only returns behind the `VectorStore` interface if >~1M vectors). This feature's user-visible half: "search my past chats" — per-message-batch embeddings + FTS over messages, hybrid-searched from the sidebar. [FACT — PLAN §4.8, §4.11]

**Modules & files.**
- `app/db/vectors.py` — (A2) the interface + impl; `app/search_chats/indexer.py` — background job embedding message batches (per N messages or on chat idle) into `vec_messages`; `app/search_chats/api.py` — `GET /api/search/chats?q=` hybrid over `vec_messages` + `messages_fts`, grouped by chat; `web/src/views/search.ts` — sidebar search box + results (chat title, matching snippet, jump-to-message).

**Interfaces.** `GET /api/search/chats?q=&limit=20` → `[{chat_id, title, message_id, snippet, score}]`. Indexer: `index_chat(chat_id)` embeds unembedded batches; runs post-turn fire-and-forget alongside summaries (F18).

**Config keys.**
```yaml
chat_search:
  enabled: true
  embed_batch_messages: 8
  semantic: true            # false = FTS-only
```

**Integration points.** Reuses embed (F10), utility scheduling slot (F18's background queue), `VectorStore` (A2). Spans: `chat_search.index`, `chat_search.query`. Phase-0 benchmark result decides sqlite-vec confidence. [FACT]

**Build steps.** 1) FTS-only search first (works day one); 2) indexer job; 3) hybrid merge; 4) sidebar UI + jump-to-message anchor; 5) backfill script for pre-existing chats.

**Tests.** pytest: FTS finds exact phrase; semantic finds paraphrase in fixture chats; disabled semantic → FTS still serves; backfill idempotent. Playwright: search finds a message sent earlier in the test run, click jumps to it highlighted.

**Failure modes.** Embedder down → semantic column empty, FTS results still returned (flagged `semantic: false` in response meta). Index lag → results eventually consistent; UI notes "indexing…" if unembedded batches exist for matched chats. [INFERENCE]

## F14. GPU delegation & swapgen (spec §14)

**What.** At startup, `nvidia-smi` inventory → GPU inventory endpoint → Settings UI assigns each model to a GPU (or CPU for <2B). A deterministic module renders `llama-swap.yaml` from config + assignments; changing assignments regenerates the file and triggers llama-swap config reload — programmatic config writing, never AI-generated, never hand-edited. [FACT — PLAN §4.1]

**Modules & files.**
- `app/gpu/inventory.py` — parse `nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv`; `app/gpu/swapgen.py` — pure function config→yaml (macros incl. `${PORT}`, per-model `cmd`, groups `resident`/`gpu0-main` per PLAN's sample); `app/gpu/api.py` — inventory endpoint + assignment PATCH + reload trigger; `web/src/views/settings/gpu.ts` — assignment table with live VRAM bars.

**Interfaces.** `GET /api/gpu/inventory` → `[{index, name, mem_total_mb, mem_free_mb}]`; `PATCH /api/models/{alias} {device}` → overlay write → `swapgen.render(config) -> str` → write `llama-swap.yaml` → `POST` llama-swap reload endpoint [UNCERTAIN — reload endpoint name per installed llama-swap version; verify Phase 0] → poll until resident group healthy. `render()` output shape per PLAN §4.1 sample (macros/models/groups; `--device CUDAn`/`--device none -ngl 0`; vision adds `--mmproj`) [UNCERTAIN — exact `--device` flag spelling per installed llama.cpp build; verify against `llama-server --help` in Phase 0].

**Config keys.**
```yaml
gpu:
  enabled: true               # false = swapgen never writes; hand-managed yaml honored
  swap_yaml_path: /opt/llama-swap/llama-swap.yaml
  reload_on_change: true
  vram_guard: true            # refuse assignment if model est. size > free VRAM
```

**Integration points.** Consumes model registry (F2); feeds llama-swap (F1); Settings UI (F4); debug panel shows swap state + GPU poll (A3). Spans: `gpu.inventory`, `swapgen.render`, `swapgen.reload`. Exit gate Phase 2: GPU reassignment without backend restart. [FACT]

**Build steps.** 1) inventory parser (fixture-tested on captured nvidia-smi output); 2) swapgen pure function + golden-yaml tests (one per roster scenario: default, model disabled, ctx-variant pair, vision, two-box future map [INFERENCE — future map is config passthrough only]); 3) file write (temp+rename) + generated-file header comment; 4) reload call + health poll; 5) Settings GPU tab; 6) vram_guard estimates (file size × overhead factor from Phase-0 measurements).

**Tests.** pytest: golden yaml byte-diffs; disabled model omitted; resident group always contains classifier/embed/needle/utility when enabled; guard rejects oversized assignment; reload failure rolls the yaml file back to previous version (kept as `.bak`). Live: reassign embed 3070→CPU in Settings, preflight still green.

**Failure modes.** `nvidia-smi` absent → CPU-only inventory, big models flagged unassignable, app still runs. Reload fails → rollback yaml + error banner; llama-swap keeps old config (never left half-configured). Hand-edit detected (header hash mismatch) → refuse to overwrite, tell user to move changes into `config.yaml`. [INFERENCE — protects the "never hand-edit" contract]

## F15. BrowserOS integration (spec §15)

**What.** BrowserOS runs on the Windows host (GUI browser, logged-in sessions, you watch the agent); the backend is a generic **MCP client** connecting to BrowserOS's built-in MCP server (31+ tools: navigate, click, scrape, screenshot) over LAN, exposed to capable models as the `browser` toolset — off by default, per-chat toggle (consequential actions). Deep research does NOT ride the browser: search+fetch is primary, browser is the escalation for JS-heavy/logged-in/interactive pages. [FACT — PLAN §4.12]

**Modules & files.**
- `app/mcp/client.py` — minimal MCP client (initialize, list_tools, call_tool) over the transport BrowserOS exposes [UNCERTAIN — SSE vs streamable-HTTP vs stdio-only, and non-localhost reachability; verify Phase 5 before UI work; if localhost-only, a tiny host-side relay or SSH tunnel bridges it (PLAN §4.12.4)]. Generic by design — future MCP servers reuse it.
- `app/tools/impl/browser.py` — bridges MCP tools into our tool registry: prefixes names (`browser.navigate`…), maps schemas, marks `consequential: true`.
- `web/src/views/settings/browseros.ts` — URL, connect test, tool list display; per-chat toggle chip lives in the composer.

**Interfaces.** `MCPClient.connect(url) -> ServerInfo`, `list_tools() -> [{name, description, input_schema}]`, `call_tool(name, args) -> {content, is_error}`. Tool results (screenshots) returned as image content → attachment pipeline for vision-model consumption [INFERENCE]. Per-chat enable stored as `chats` meta flag; disabled = tools absent from that turn's schema list.

**Config keys.**
```yaml
browseros:
  enabled: false             # global gate; also requires per-chat opt-in
  url: "http://<host-ip>:<port>"
  connect_timeout_s: 5
  allowed_tools: []          # empty = all discovered
```

**Integration points.** Tool registry (F9), attachments/vision (F11/F2), debug spans `browser.<tool>` (args minus sensitive fields, duration, screenshot ref). BrowserOS may independently point its own in-browser agent at llama-swap `/v1` — docs note only, no backend work. [FACT]

**Build steps.** 1) Phase-5 transport spike: verify MCP transport + LAN reachability, document; 2) `mcp/client.py` against findings; 3) registry bridge + consequential gating; 4) settings tab + connect test; 5) per-chat toggle UX; 6) screenshot→vision wiring; 7) `docs/browseros.md` (install, relay/tunnel if needed).

**Tests.** pytest vs fake MCP server: discovery → registry names prefixed; disabled globally → absent; enabled globally but not per-chat → absent from schemas; call_tool error → `is_error` ToolResult; timeout → error result not hang. Playwright: toggle chip enables browser tools, tool chip renders screenshot result (fake).

**Failure modes.** BrowserOS unreachable → connect test fails in Settings, tools silently absent from chats (with debug span noting skip). Mid-task disconnect → tool error result, model continues. Localhost-only MCP → documented relay; feature stays off until bridge verified.

## F16. Web search providers (spec §16)

**What.** `search/` provider chain: DDG primary (`ddgs` lib, no key) → on rate-limit/empty → Tavily (key in `.env`). Fallback is automatic per-query; the provider actually used is shown in citations and the debug panel. DDG throttling is real (old build's resilience doc). [FACT — PLAN §4.13]

**Modules & files.**
- `app/search/base.py` — `SearchProvider` protocol + result type; `app/search/ddg.py`, `app/search/tavily.py` — one module each; `app/search/chain.py` — ordered chain with per-provider circuit breaker (cooldown after repeated failures).

**Interfaces.** `async search(query: str, max_results: int) -> SearchResponse`; `SearchResponse {provider: "ddg"|"tavily", results: [{title, url, snippet}], degraded: bool}`. `web_search` tool (F9) wraps `chain.search`; fallback triggers on: exception, HTTP 429/403, or zero results [INFERENCE — "rate-limit/empty" per PLAN].

**Config keys.**
```yaml
search:
  enabled: true
  chain: [ddg, tavily]
  max_results: 8
  ddg: {enabled: true}
  tavily: {enabled: true, api_key_env: TAVILY_API_KEY}
  breaker: {failures: 3, cooldown_s: 300}
```

**Integration points.** `web_search` tool (F9); deep research (Future) fans out over this same chain; citations SSE event carries `provider`. Spans: `search.ddg` / `search.tavily` (query, count, ms, fallback_reason).

**Build steps.** 1) protocol + ddg; 2) tool wiring same PR (anti-Bug-1 — the DDG adapter existing-but-never-injected is the canonical old failure); 3) tavily; 4) chain + breaker; 5) provider surfaced in citations UI; 6) missing-key behavior (tavily self-disables).

**Tests.** pytest with mocked HTTP: ddg success → provider "ddg"; ddg 429 → tavily used, `fallback_reason` recorded; both fail → tool returns `is_error` with human-readable message; breaker opens after 3 failures and closes after cooldown; no Tavily key → chain is ddg-only, startup log notes it. **Wiring test:** `web_search` present in live registry and dispatches to `chain.search`.

**Failure modes.** Both providers down → model told search unavailable, answers from knowledge with a caveat. Tavily key invalid → provider disabled after first 401, banner in Settings.

## F17. Custom prompts / preferences / project memories (spec §17)

**What.** User preferences and custom prompts are always-injected user-scope memories (F10); prompt templates (system prompt per model class, per project) live in `config.yaml` + Settings UI. Project instructions (`instructions.md`) inject in project chats. Everything visible and editable — nothing invisible. [FACT — PLAN §4.8, §4.14]

**Modules & files.**
- `app/prompts/templates.py` — resolve system prompt: base template per model class → project override → per-chat additions; simple `{placeholders}` (date, model, project name), deterministic; `app/prompts/api.py` — CRUD over template overlay; `web/src/views/settings/prompts.ts` — per-class + per-project template editors with preview.

**Interfaces.** `build_system_prompt(model_class: str, project: Project|None) -> str` — called by context assembler (A4) before memory/RAG blocks. `GET/PUT /api/prompts?class=&project_id=`. Injection order in final user-turn assembly: system prompt (templates) → `<memory-context>` (F10) → RAG block → attachment inline block → user text. [INFERENCE — stable documented order so debug prompts are readable]

**Config keys.**
```yaml
prompts:
  enabled: true
  system:
    general: "You are a helpful assistant…"
    coding: "…"
    reasoning: "…"
    vision: "…"
  allow_project_override: true
```

**Integration points.** Context assembler (A4), projects (F7 instructions), memory (F10 user scope "always injected" [FACT — spec §17 via PLAN]). Span: `memory.inject` meta includes template source (`base|project|chat`).

**Build steps.** 1) template resolver + placeholder engine; 2) wiring into context assembly with order contract; 3) Settings editors + preview (renders the exact final system prompt); 4) project override path; 5) debug panel shows the assembled prompt verbatim (uses `store_prompts`).

**Tests.** pytest: resolution precedence (chat > project > class base); placeholders filled; missing class falls back to `general`; preview equals what `llm.request` span records (the honesty test). Playwright: edit coding template in Settings → next coding-routed message's debug span shows it.

**Failure modes.** Template with unknown placeholder → rendered literally + warning, never a failed turn. Empty template → class falls back to `general` base.

## F18. Auto-title, summaries, compaction (spec §18)

**What.** The `utility` model (3070 resident) handles: chat title after first exchange; rolling summary stored per chat; compaction when context exceeds threshold (summarize oldest turns, keep recent verbatim + summary block — Claude Code's own pattern). All background; failures never block chat. [FACT — PLAN §4.15]

**Modules & files.**
- `app/background/queue.py` — tiny in-process task queue (post-turn jobs: title, summary, chat-search indexing, memory review) with per-job-type concurrency 1; `app/background/titler.py` — first-exchange → title prompt → `PATCH` chat + SSE `title` event; `app/background/summarizer.py` — rolling `chats.summary` update every N turns; `app/chat/compactor.py` — in-turn (not background): when assembled context > threshold, replace oldest turns with summary block before `llm.request`.

**Interfaces.** `queue.submit(job_type: str, payload)`; jobs read/write via repositories only. Compaction message shape: a synthetic assistant-side block `"[Summary of earlier conversation]\n…"` preserved at messages head [INFERENCE — pattern per PLAN]; compaction state stored as `chats` meta (`compacted_before_message_id`) so it's stable across turns.

**Config keys.**
```yaml
summaries:
  enabled: true
  model: utility
  title: {enabled: true}
  rolling: {enabled: true, every_turns: 6}
  compaction: {enabled: true, trigger_pct: 80, keep_recent_turns: 8}
```

**Integration points.** Orchestrator post-turn hook submits jobs; compactor sits inside context assembly (A4); chat-search indexer (F13) and memory reviewer (F10) ride the same queue. Spans: `title.generate`, `summary.update`, `compact.run` (tokens before/after). SSE `title` event updates sidebar live.

**Build steps.** 1) queue with error isolation; 2) titler + SSE event + sidebar wiring; 3) rolling summarizer; 4) compactor with token counting (llama.cpp `usage` numbers, else tiktoken-free heuristic chars/4 [INFERENCE]); 5) thresholds in Settings; 6) debug spans.

**Tests.** pytest vs fake utility model: title generated exactly once after first exchange; summary refreshes on cadence; compaction triggers at threshold, keeps N recent verbatim, resulting context under limit; utility model failure → job logged, retried once, chat unaffected (the never-block test). Playwright: sidebar title appears without refresh.

**Failure modes.** Utility model busy/down → jobs queue and retry; titles show "New chat" meanwhile. Compaction failure → fall back to hard truncation of oldest turns with a visible `[context truncated]` marker (degraded but honest).

## F19. Debug panel — the Debug view (spec §19, frontend half; backend in A3)

**What.** A **standalone Debug window** (its own route `#/debug`, meant to be opened in a separate browser window/tab so you watch it live beside the app — not an embedded panel), toggled on in Settings → Debug. Fed by `/api/debug/stream` + trace REST: per-turn waterfall, route decision + why, **exactly what each model was sent and returned** (raw prompts/responses incl. thinking tokens, toggle), **token counts + latency/tok-s derived from llama.cpp** (`usage` + `timings`, never client estimates), every tool call (name/args/result/emitter), llama-swap state, nvidia-smi telemetry, Needle-assist markers. Critical infrastructure, shipped in Phase 1 alongside the first chat path. [FACT — PLAN §4.16]

**Modules & files.**
- `web/src/views/debug.ts` — layout: trace list (left), waterfall + span detail (center), live tails (right); `web/src/debug/waterfall.ts` — span rows → CSS-grid timeline (no chart lib); `web/src/debug/live.ts` — tap subscription: GPU bars, swap state, rolling log; `web/src/debug/span_detail.ts` — prompt/response viewer (monospace, copy button), token/latency stats.

**Interfaces.** Consumes A3's REST + SSE verbatim. Per-turn header shows: `route` source chip (override/rules/classifier/default + confidence), model, tok/s (`usage` + `timings` from llama.cpp [FACT]), swap-wait badge if `swap.wait` span present, Needle badge if `needle.dispatch` spans present ("who decided vs. who emitted" per step [FACT — PLAN §4.7]). Deep link: each chat message has a "debug" affordance → `#/debug?trace=<trace_id>`.

**Config keys.** `debug:` (A3) drives it; `ui.debug_link_on_messages: true`.

**Integration points.** Pure consumer of A3; every feature's spans appear here for free — that's the point. Per-model bench numbers (F2 `data/bench.json`) render in a models sub-tab.

**Build steps.** 1) trace list + waterfall from REST (Phase 1); 2) live tap tails; 3) span detail with prompt toggle honoring `store_prompts`; 4) route/Needle/swap badges; 5) message→trace deep link; 6) GPU/swap panels; 7) bench tab (Phase 5 `llama-bench` panel [FACT — PLAN Phase 5]).

**Tests.** Playwright vs fake backend: send message → trace appears with golden span sequence; waterfall spans ordered by time; live GPU event updates bar; error turn shows red span with exception text; prompts hidden when `store_prompts: false`. This suite doubles as the wiring proof for A3.

**Failure modes.** Debug stream drop → view reconnects, backfills from REST. Huge prompt bodies → lazy-load span detail on click (list stays fast). `debug.enabled: false` → view shows "tracing disabled" with a link to Settings, never an error.

---

## Appendix: feature → phase → toggle quick reference

| Feature | Phase | Toggle key | Degrades to |
|---|---|---|---|
| A1 Config | 1 | — (substrate) | fail-loud at startup |
| A2 Storage | 1 | — (substrate) | vec-off → FTS-only |
| A3 Tracing | 1 | `debug.enabled` | no-op tracer, chat unaffected |
| A4 Orchestrator/SSE | 1 | `chat.enabled` | — (the app) |
| A5 Frontend shell | 1 | `ui.enabled` | — |
| F1 llama-swap layer | 0–1 | (llm section) | fast `llm_unreachable` errors |
| F2 Model roster | 2 | per-model `enabled` | class → `chat-default` |
| F3 Topology | 0–1 | — | `/health` degraded states |
| F4 Web UI | 1–4 | `ui.enabled` | — |
| F5 Router | 2 | `routing.enabled`, `routing.classifier.enabled` | default model |
| F6 opencode | 4 | `opencode.enabled` | chat model + sandbox |
| F7 Projects | 3 | `projects.enabled` | plain chats |
| F8 Artifacts/sandbox | 3–4 | `artifacts.enabled`, `sandbox.enabled` | code shown, not run |
| F9 Tools + Needle | 3–5 | `tools.enabled`, per-tool, `tools.needle_assist.enabled` | plain chat / main-model emission |
| F10 RAG + memory | 3, 5 | `rag.enabled`, `memory.enabled`, `memory.reviewer.enabled` | FTS-only / no injection |
| F11 Attachments | 3 | `attachments.enabled`, per-extractor | upload rejected visibly |
| F12 Testing | 1+ | — (process) | — |
| F13 Chat search | 3 | `chat_search.enabled`, `.semantic` | FTS-only |
| F14 GPU/swapgen | 2 | `gpu.enabled` | hand-managed yaml |
| F15 BrowserOS | 5 | `browseros.enabled` + per-chat | fetch_url path |
| F16 Search chain | 3 | `search.enabled`, per-provider | model answers with caveat |
| F17 Prompts/prefs | 3 | `prompts.enabled`, `memory.inject` | class-base prompt only |
| F18 Title/summary/compaction | 2–3 | `summaries.*` | "New chat", hard truncation |
| F19 Debug view | 1 | `debug.enabled` | "tracing disabled" notice |

**Non-goals restated (do not build):** Ollama, LiteLLM, React or any frontend framework/bundler, Qdrant (unless the interface escape hatch triggers), backend auth, remote providers before Future, opencode inside the chat tool loop, Needle as planner, browser-driving as the deep-research primary.
