# AI Mega App — FEATURES.md (Full Build Document)

**Source of truth:** `PLAN.md` (Build Plan v2, **rev 6 — 2026-07-23, post-Phase-0**). This document expands every feature in PLAN.md into buildable detail. Where this file and PLAN.md disagree, PLAN.md wins. Measured facts live in `docs/phase0-measurements.md`; the one-line-per-decision version is `docs/PHASE0_FINDINGS_SUMMARY.md`.
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

**What.** `config.yaml` (checked-in **defaults** only) + `.env` (secrets only) + a machine-written `settings.local.yaml` overlay. **The Settings UI is the authoritative surface for every user change and writes the overlay (and secrets to `.env`); you never have to hand-edit a file — anything in the file is editable in the UI, and the UI wins.** This designs out the old "edit-a-file-and-a-menu" split. The one thing the UI writes to `.env` (not the overlay) is **API keys** for remote providers (Anthropic, opencode zen, Kimi, Tavily), redacted on read-back. Settings must "account for different models and configurations" — model roster + per-model device assignment (Models tab → swapgen; GPU index or CPU, never a tensor-split), per-box `model → endpoint` map (multi-computer, future), BrowserOS MCP URL, tool toggles — each a tab-driven overlay edit, no code change. [FACT — PLAN §3.1, §4.14]

**Modules & files.**
- `app/config.py` — one flat module (rule `002`, ≤300 lines): pydantic models for every section, `load_config()` with deep-merge of `settings.local.yaml` and env-substitution from `.env`, and a cached `get_config()`. Validation errors fail startup loudly with the offending key path. Split into a package only if it genuinely outgrows the cap — and only with owner approval, because five wave-2 agents import from it.
- `app/settings/store.py` — reads/writes the `settings.local.yaml` overlay atomically (write-temp + rename), re-validating the merged result through `app/config.py` before committing. The Settings API is its only caller; `config.yaml` is never written at runtime. (Phase 2 — see F4/§4.14.)
- `config.yaml` — repo root, all defaults.

**Interfaces.**
- `load_config(path: Path = CONFIG_PATH) -> Config` — raises `ConfigError(key_path, reason)`. `get_config() -> Config` — cached accessor.
- `GET /api/settings` → merged effective config (secrets redacted). Writes are typed and scoped rather than one generic patch: `PUT /api/settings/models/{name}`, `PUT /api/settings/routing`, `GET /api/models`. Each validates, persists the overlay, and hot-reloads in process (`config_changed` on an internal pub/sub) — no restart.
- Every feature section carries `enabled: bool` — the registry pattern in A-modules checks it at startup *and* per request (hot toggle).

**Config keys.** Top-level sections (each detailed in its feature): `server`, `llm`, `models`, `routing`, `tools`, `rag`, `memory`, `projects`, `attachments`, `artifacts`, `sandbox`, `opencode`, `browseros`, `search`, `summaries`, `debug`, `ui`. Plus `server: {host: 0.0.0.0, port: 8000, static_dir: web/}`.

**Integration points.** Called by literally everything at startup; Settings UI (F4) writes it; swapgen (F14) and opencode config generator (F6) read it as input. Emits span `config_load` at startup with validation duration.

**Build steps.** 1) pydantic schema for Phase-1 sections only; 2) loader + merge + env substitution; 3) fail-loud validation with key paths; 4) overlay writer; 5) `/api/settings` GET/PATCH; 6) hot-reload pub/sub; 7) grow schema per phase.

**Tests.** pytest: valid config loads; unknown key rejected with path; overlay merge precedence (overlay > base); secret redaction in GET; PATCH round-trip changes live config object; malformed overlay leaves base intact.

**Failure modes.** Bad `config.yaml` → startup abort with exact key (never half-boot). Bad overlay → ignored with warning span + UI banner, base config used. `.env` missing a referenced secret → the *feature* using it disables itself (e.g. Tavily), app still boots.

---

## A2. SQLite storage core

**What.** One SQLite file (WAL mode) holds chats, messages, memories, chunk *text*, FTS5 indexes, attachments metadata, review queue, and debug traces. **Vectors live in Qdrant**, reached only through the `VectorStore` interface. Phase 0 benchmarked sqlite-vec at 100k×768-dim and it failed the gate — KNN p95 105ms against a ~50ms interactive bar, brute-force-scan-bound rather than query-shape-bound (the documented rowid-prefilter hybrid form was already in use; the naive FTS-JOIN form was 11.6s/query and is a trap worth not re-discovering). [FACT — PLAN §3.1, `docs/phase0-measurements.md` §6]

**Modules & files.**
- `app/db.py` — connection factory (WAL, `busy_timeout`), thin query helpers, and a thread-executor wrapper so sync `sqlite3` calls stay off the event loop. **No `aiosqlite`** — the backend dependency core is fastapi/httpx/uvicorn/pydantic/pyyaml and an executor helper is ~15 lines (rule `001`).
- `app/schema.sql` — `CREATE TABLE` DDL, deterministic hand-written SQL (Key Rule 1), applied by `init_db()`; versioned migrations via `PRAGMA user_version` as later phases add tables.
- Repository functions live beside their feature (`app/chat/history.py`, `app/memory/store.py`, …) rather than in a `db/` package — one module per feature is the architecture.
- `app/rag/store.py` (Phase 3) — `VectorStore` interface (`add`, `query`, `delete_by_source`) + **`QdrantStore` (default)** and `SqliteVecStore` (fallback, kept as the conformance suite's second target). Which one is live is the `vectors.store` config key; no caller outside this module knows the difference. [FACT — PLAN §3.1, escape hatch exercised]

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
-- Vectors are NOT in SQLite: Qdrant collections `chunks` and `messages`,
-- keyed by the same TEXT ids as the tables above (DIM from embed model config).
-- SqliteVecStore's vec_chunks/vec_messages tables exist only when that impl is selected.
```
`VectorStore`: `async add(ids: list[str], vecs: list[list[float]], meta: list[dict])`, `async query(vec, k: int, filter: dict) -> list[Hit]` where `Hit = (id, score, meta)`, `async delete_by_source(source: str)`.

**Phase-1 subset.** `app/schema.sql` ships only `chats`, `messages`, `traces`, `spans`, `settings_overlay` in Phase 1 — plus **`chats.summary`**, which exists from day one even though nothing writes it until Phase 2's rolling summarizer. That one column is cheaper than a mid-wave frozen-schema approval, and it is why there is no `chat_summaries` table anywhere in this plan.

**Config keys.**
```yaml
db: {path: data/app.db, wal: true, backup_dir: data/backups}
vectors:
  store: qdrant              # qdrant | sqlite_vec
  qdrant: {url: "http://127.0.0.1:6333", collection_prefix: mega, timeout_s: 10}
  dim: 768                   # must match the embed model; validated at startup
```
(no `enabled:` — storage is not optional; the toggle rule applies to features, not the substrate)

**Integration points.** Every feature. FTS triggers keep `*_fts` in sync with base tables. Debug spans: `db_migrate` at startup. ~~Phase 0 gate: sqlite-vec at 100k chunks~~ — run, failed, Qdrant adopted (§A2).

**Build steps.** 1) engine + extension load + WAL pragmas; 2) v1 schema (chats/messages/traces/spans) for Phase 1; 3) FTS triggers; 4) repository modules; 5) migration runner; 6) vec tables + `VectorStore` in Phase 3; 7) ~~100k-chunk benchmark~~ — done in Phase 0 (`scripts/bench_sqlitevec.py`, verdict `logs/benchmarks/sqlitevec_verdict.json`); re-run it against `QdrantStore` with the same harness so the two numbers are comparable.

**Tests.** pytest with tmp-file DB: migration idempotence; FTS trigger sync on insert/update/delete; vector round-trip (upsert → query returns nearest); concurrent writer+reader under WAL; **the `VectorStore` conformance suite runs against both `QdrantStore` (containerized or skipped in CI) and `SqliteVecStore` (always) — two passing impls is what keeps the interface honest.**

**Failure modes.** **Qdrant unreachable → vector features degrade to FTS/BM25-only** with a UI banner, never a hard failure; the hybrid design is what makes lexical-only a usable degraded mode. `dim` mismatch between config and the live embed model → startup abort with the offending key (a silent mismatch corrupts a collection quietly). DB locked → `busy_timeout` retry; persistent lock → 503 with `error` SSE, never a hung stream.

---

## A3. Debug tracing core (backend half of spec §19 — BUILT FIRST)

**What.** Per-turn `trace_id`; every stage (route, rag, llm request/response, tool dispatch, swap wait, SSE emit) writes a span row to SQLite and mirrors it to a live SSE tap. This is Phase-1 infrastructure other features must call — retrofitting it killed the old build. [FACT — PLAN §4.16]

**Modules & files.**
- `app/debug/trace.py` — `new_trace()` + the `span()` async context manager; writes rows and publishes to the bus.
- `app/debug/bus.py` — in-process pub/sub fan-out feeding `/api/debug/stream` subscribers; bounded, drop-oldest.
- `app/debug/api.py` — REST for stored traces + the SSE tap endpoint.
- `app/debug/bench.py` (Phase 5) — `nvidia-smi` poll endpoint, llama-swap state proxy, and the per-model bench table. [UNCERTAIN — exact llama-swap status endpoint path; pin against the installed version in Phase 2]

**Interfaces.**
- `new_trace(chat_id) -> trace_id` (uuid4); `async with span(trace_id, stage, **fields) as sp: sp.set(model=..., prompt=..., tokens_in=...)`. Span auto-records start/end ms and exception info on error. Both re-exported from `app/debug/__init__.py`.
- `GET /api/debug/traces?chat_id=&limit=` → trace list with spans nested; `GET /api/debug/trace/{trace_id}` → full waterfall JSON (stage, start/end ms, data).
- `GET /api/debug/stream` (SSE) event types: `span` (a finished span row), `heartbeat` (15s), and from Phase 5 `gpu` (`{index, name, mem_total_mb, mem_used_mb, util_pct}` per card), `swap_state` (llama-swap loaded/loading models), `log` (warnings).
- **Canonical span stages — flat snake_case, no dots, frozen in PLAN.md §4.2:** `route`, `llm_request`, `llm_stream`, `sse_emit`, `swap_wait`, `db`, `tool`, `dispatcher`, `search`, `fetch`, `rag_ingest`, `rag_retrieve`, `memory_inject`, `memory_review`, `project_context`, `attachment_extract`, `compaction`, `title`, `summary`, `gpu_inventory`, `swapgen`, `exec`, `browser`, `opencode_session`, `bench`. Per-instance detail (which tool, which provider) is a **field on the span**, not part of the stage name — `stage="tool", name="web_search"`, never `tool.web_search`. Filtering a waterfall by stage only works if the stage vocabulary is closed and small.

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

**Build steps.** 1) span table + `new_trace`/`span` context-manager API; 2) bus + `/api/debug/stream`; 3) wire into the Phase-1 chat path (route→llm→sse) so the first feature is born traced; 4) trace REST; 5) gpu poller; 6) swap proxy; 7) retention sweeper (daily, deletes old traces unless `store_prompts` archived); 8) stage-vocabulary contract doc comment in `trace.py`.

**Tests.** pytest: span nesting produces correct parent ordering by time; a deliberately failing span write logs and does **not** raise into the caller (chat > observability); tap subscriber receives a span published after subscribe; retention deletes only expired; `debug.enabled: false` still lets chat flow (no-op tracer). Contract test: a fake chat turn produces the golden stage sequence `route → llm_request → llm_stream → sse_emit`.

**Failure modes.** Trace write failure must never fail the turn — tracer catches, logs, continues (chat > observability). Tap backpressure → drop-oldest, `log` event notes drops. `nvidia-smi` absent → `gpu` events omitted, Debug view shows "no GPU telemetry".

---

## A4. SSE streaming contract + chat orchestrator

**What.** The core turn loop: resolve model → inject context (memory/RAG) → stream completion → run tool loop → persist → emit SSE. One orchestrator serving every surface (hermes lesson: platform differences live at the entry point). Hard rule from Bug 2: **every stream terminates with `done` or `error`.** [FACT — PLAN §4.2, §4.7]

**Modules & files.**
- `app/chat/orchestrator.py` — the turn loop (no tool logic, no routing logic — calls them).
- `app/chat/api.py` — chat/message REST + the stream endpoint; owns the encoder and the terminal-event guarantee (finally-block emitter).
- `app/chat/history.py` — message persistence helpers over `app/db.py`.
- `app/chat/compaction.py` (Phase 3) — `maybe_compact(chat)` before the LLM call.
- `app/llm_client.py` — thin OpenAI chat-completions client to llama-swap (`model` field selects; llama-swap swaps). No scheduler code — the old ModelScheduler is deleted. [FACT — PLAN §4.1]

**Interfaces.** These mirror PLAN.md §4.2, which is the source; nothing here may diverge from it.
- REST: `POST /api/chats` `{project_id?}` → chat; `GET /api/chats`, `GET /api/chats/{id}/messages`, `POST /api/chats/{id}/model` (set/clear `model_override`), `POST /api/chats/{id}/attachments` (F11), `DELETE /api/chats/{id}`.
- Stream: `POST /api/chats/{id}/messages` body `{content: str, attachments: [id], model?: str}` → SSE. Event shapes (each `event:` + JSON `data:`):
  - `token {text}` — the only content event; thinking tokens from a thinking-enabled alias arrive here too and are tagged in `done`.
  - `model_loading {model}` — emitted when first-token latency exceeds 2s, i.e. llama-swap is swapping the 3090 slot (**measured up to 12.47s cold** [FACT — PLAN §4.1], not the 3–10s once guessed).
  - `tool_start {name, args_preview}` · `tool_result {name, content_preview, is_error}` (Phase 3)
  - `title {chat_id, title}` (Phase 2)
  - `done {message_id, model, usage, route?, citations?, context?}` **or** `error {kind, detail}` — exactly one, always, enforced in a `finally`.
- **Why so few events.** Route decision, token usage, citations, and compaction state ride the **`done` payload** rather than each getting a mid-stream event: one terminal payload that later phases add keys to is far easier to keep honest than five optional events the client must tolerate in any order. Build `done` as a dict stages contribute to. Artifacts are detected **client-side** from finished message text (F8 `detect.ts`) — there is no `artifact` event.
- `llm_client.py`: `async chat(model, messages, *, tools=None, response_format=None, thinking: bool|None=None, max_tokens: int|None=None, stream=True) -> AsyncIterator[ChatDelta]`; `async embed(model, texts) -> list[list[float]]`; `async models() -> list[str]`. `thinking`/`max_tokens` are in the frozen Phase-1 signature deliberately — `reasoner` is `chat-default`'s blob with thinking flipped at the request layer, and structured-output calls need reasoning suppressed per-request (PLAN §4.1).

**Config keys.**
```yaml
llama_swap:
  base_url: http://127.0.0.1:8080/v1
  timeout_s: 120
llm:
  first_token_timeout_s: 30            # MUST exceed the measured 12.47s cold
                                       # load of chat-default; then error event
chat:
  enabled: true
  max_context_tokens: 32768
  heartbeat_seconds: 15
```

**Integration points.** Calls: router (F5), context providers (F10, F17), tool loop (F9), tracing (A3), background jobs (F18, fire-and-forget post-turn). Called by: web UI (F4). Spans: `llm_request` (with full prompt when enabled), `llm_stream`, `swap_wait` (time between request and first token when llama-swap reports loading), `sse_emit`.

**Build steps.** 1) `app/llm_client.py` against real llama-swap with `curl`-verified contract; 2) SSE encoder with terminal guarantee + heartbeats; 3) minimal orchestrator (no tools/rag): resolve model→stream→persist, leaving a marked seam where the Phase-2 router slots in; 4) chat REST; 5) span wiring; 6) `gather_context()` seam (Phase 3 fills it); 7) tool-loop hook (F9); 8) golden-transcript contract test.

**Tests.** pytest vs fake llama-swap (canned OpenAI responses): full turn emits the golden event sequence (`tests/golden/basic_turn.txt`); provider 500 mid-stream → `error` event, stream closed, message row marked failed (Bug-2 regression test); first-token timeout fires; heartbeats present in slow stream; `model_loading` emitted when the fake reports loading. Playwright: send message, see streamed text, refresh, history persists.

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

**What.** All inference is llama.cpp `llama-server` instances fronted by **llama-swap** (:8080): group `resident` (`swap: false`) pins `classifier` + `utility` + `embed` (CPU) and `dispatcher` (GPU1); group `gpu0-main` (`swap: true`) gives the 3090 an exclusive big-model slot, one model at a time, placed with `CUDA_VISIBLE_DEVICES=0` and **never `--tensor-split`**. The backend speaks plain OpenAI chat-completions; llama-swap does every load/swap. Native router mode rejected (no group pinning). [FACT — PLAN §4.1]

**Measured behavior this layer must accommodate** [FACT — `docs/phase0-measurements.md` §13]: `chat-default` cold load **12.47s** / warm 0.67s; `dispatcher` cold 3.48s / warm 0.03–0.18s; `utility` (CPU) cold 20.54s / warm ~9.1–9.4s. The `groups:` block is load-bearing — without it llama-swap's implicit default group serializes everything and the residents cannot coexist with the big model.

**Modules & files.**
- `app/llm_client.py` — (A4) the only code path touching :8080.
- `app/gpu/rewarm.py` — ~20-line policy: re-warm `chat-default` when the 3090 has idled on another model N minutes; exposes `start_rewarm(app)`, wired at startup (Phase 2, ships with swapgen because it reads the same GPU state). [FACT — PLAN §4.1 "always loaded" policy]
- Health checks (llama-swap reachable, resident group answering) live in `/health` in `app/main.py` and in `scripts/preflight.py` — not a module of their own.
- `ops/llama-swap.service`, `ops/llama-server-build.md` — systemd unit + build notes (Phase 0 deliverables).
- Generated `llama-swap.yaml` — written only by swapgen (F14).

**Interfaces.** Upstream: `POST {base_url}/chat/completions` (stream), `POST {base_url}/embeddings`; llama-swap admin/status API proxied by `app/debug/swap_proxy.py` [UNCERTAIN — endpoint names; verify Phase 0]. Downstream: `GET /api/models` → `[{alias, class, device, resident, loaded, ctx}]` (merges config roster + live swap state).

**Config keys.**
```yaml
llama_swap: {base_url: ..., timeout_s: 120}   # see A4
llm:  {first_token_timeout_s: 30}             # MUST exceed the measured 12.47s cold load
gpu:  {rewarm_default_after_min: 10}          # re-warm GPU0 after it has idled on another model
defaults: {chat_model: chat-default, utility_model: utility, title_model: dispatcher}
```

**Integration points.** Everything model-shaped flows through here. Spans: `llm_request`, `swap_wait` (backend measures request→first-token and tags turns that crossed a swap), `llm_stream`. `model_loading` SSE event sourced from swap state. Phase 0 measures real load times / tok/s and replaces guessed budgets. [FACT — PLAN Phase 0]

**Build steps.** 1) Phase 0: build llama.cpp, install llama-swap systemd, hand-write first `llama-swap.yaml`, curl-verify swap + concurrent residents; 2) record measured VRAM/load/tok-s doc; 3) `llm/client.py` + streaming parse; 4) health checks; 5) warmkeeper; 6) `/api/models`; 7) swap-state proxy into debug tap; 8) hand-written yaml replaced by swapgen output (F14) with byte-diff check.

**Tests.** pytest vs fake swap server: streaming parse incl. tool-call deltas and `usage`/`timings`; warmkeeper fires only after idle threshold; health degradation states. Live (`scripts/preflight.py`, not CI): every configured model loads and answers 1 token; embeddings endpoint alive. [FACT — PLAN §4.10]

**Failure modes.** llama-swap down → chat errors fast with actionable message; resident model crashed → llama-swap restarts it [FACT — llama-swap process management]; swap slower than `first_token_timeout_s` → `error {recoverable: true}` and UI offers retry. **There is no instant-fallback chat model:** `utility` is CPU-resident and takes ~18–22s for a short generation, so it answers background jobs only — the mitigation for a cold swap is the warmkeeper plus a visible `model_loading` state, not a substitute reply. Routing `chat`→`reasoner` costs nothing because they share one blob.

## F2. Model classes & roster (spec §2)

**What.** The locked 10-entry roster (5 big on the GPU0 swap slot, 4 residents, 1 off-by-default alternate) tagged with a `class:` (general, coding, tool, reasoning, vision) and a placement. Old tier aliases (`coding-light/medium/heavy`, …) survive as routing labels pointing at this roster. Speed floor: nothing under ~25 tok/s at working quant. **Every row is measured — PLAN §4.1 carries the numbers, `docs/PHASE0_FINDINGS_SUMMARY.md` §1 carries the one-line rationale per pick.** [FACT]

**Modules & files.**
- Parsing `models:` into `ModelEntry` records and resolving routing label → alias is part of `app/config.py`'s pydantic schema — it is validation, not a subsystem, and every agent already imports config.
- `/api/models` (roster + class + resident flags + live swap state) is served by `app/settings/api.py`; per-model tok/s sanity numbers come from `app/debug/bench.py` (Phase 5).
- `scripts/bench_models.py` — wraps `llama-bench` per configured model, writes `data/bench.json` (Critical scope = sanity numbers in debug panel; full suite is Future §4). [FACT — PLAN §4.1]

**Interfaces.** `ModelEntry {name, class, file, quant, gpu: 0|1|"cpu", resident: bool, ttl_s: int|None, ctx: int, tool_call: "native"|"weak"|"none", mmproj: path|None, thinking: bool, reasoning_off: bool, max_tokens: int, enabled: bool, extra_flags: [str]}`. `resolve_label(label: str) -> name`. Placement is a **GPU index or `"cpu"`**, which swapgen renders as `CUDA_VISIBLE_DEVICES` — never a device string like `cuda:0`, and never a tensor-split (§F14).

`thinking`, `reasoning_off`, and `max_tokens` are not cosmetic: `reasoner` is the *same GGUF as `chat-default`* with thinking enabled at the request layer (so that route never swaps), `classifier` is unusable without `--reasoning off`, and any thinking-capable alias needs a real token budget or it returns empty. All three came out of Phase 0 the hard way.

**Config keys.**
```yaml
# Paths under /home/john/llm-stack/models/{blobs,gguf}; abbreviated here.
# `models:` is a LIST of entries, each carrying its own `name` — not a map.
# The list form keeps ordering stable for swapgen's byte-identical output and
# lets pydantic validate one entry model uniformly.
models:
  - {name: chat-default, class: general,    file: .../Qwen3.6-35B-A3B-UD-Q4_K_M.gguf, quant: Q4_K_M, gpu: 0, resident: true, ttl_s: 0, ctx: 32768, tool_call: native}
  - {name: reasoner,     class: reasoning,  file: .../Qwen3.6-35B-A3B-UD-Q4_K_M.gguf, quant: Q4_K_M, gpu: 0, ctx: 32768, thinking: true, max_tokens: 4096}   # same blob as chat-default — no swap
  - {name: reasoner-alt, class: reasoning,  file: .../DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf, quant: Q4_K_M, gpu: 0, ctx: 8192, enabled: false, max_tokens: 4096}
  - {name: coder,        class: coding,     file: .../Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf, quant: Q5_K_M, gpu: 0, ctx: 16384}   # Q5 is the locked quant
  - {name: coder-small,  class: coding,     file: .../qwen2.5-coder-7b.gguf, quant: Q4_K_M, gpu: 0, ctx: 16384}
  - {name: vision,       class: vision,     file: .../Qwen3-VL-32B-Instruct-Q4_K_M.gguf, quant: Q4_K_M, mmproj: .../Qwen3-VL-32B-Instruct-mmproj-BF16.gguf, gpu: 0, ctx: 8192}
  - {name: dispatcher,   class: dispatcher, file: .../Hammer2.1-1.5b-Q4_K_M.gguf, quant: Q4_K_M, gpu: 1, resident: true, ttl_s: 0, ctx: 4096}
  - {name: classifier,   class: classifier, file: .../Qwen3-1.7B-Q8_0.gguf, quant: Q8_0, gpu: cpu, resident: true, ttl_s: 0, ctx: 4096, reasoning_off: true}
  - {name: utility,      class: utility,    file: .../qwen3-8b.gguf, quant: Q4_K_M, gpu: cpu, resident: true, ttl_s: 0, ctx: 8192}
  - {name: embed,        class: embed,      file: .../nomic-embed-text-v2-moe.Q4_K_M.gguf, quant: Q4_K_M, gpu: cpu, resident: true, ttl_s: 0}
routing_labels: {coding-light: coder-small, coding-heavy: coder, reasoning-heavy: reasoner, ...}
```
Context-variant entries (same weights, different `ctx`) remain supported — the roster no longer ships one because `chat-default` at 32k is the measured target. Note the open caveat: throughput on `chat-default` degrades **52.9% by 32k** (recall stays correct), so compaction thresholds matter more than the window size.
Per-model toggling = removing/commenting the entry or `enabled: false` on it; registry skips disabled entries and swapgen omits them.

**Integration points.** Input to swapgen (F14), router (F5), model picker (F4), dispatcher assist (`tool_call: weak` tag, F9). Users add models per class in Settings [FACT — owner decision 4]. Spans: none of its own; registry data annotates `route` and `llm_request` spans.

**Build steps.** 1) `ModelEntry` schema + validation (paths exist, one always-loaded per swap group); 2) label resolution; 3) `/api/models`; 4) Settings editor section (add/edit/disable model → overlay → swapgen regen); 5) bench script + `data/bench.json`; 6) Phase-0 A/B note for `reasoner`.

**Tests.** pytest: label→alias resolution; disabled model invisible to router and swapgen; duplicate device/resident conflicts rejected; ctx-variant entries share one weights path. Router eval (F5) exercises class coverage.

**Failure modes.** Missing GGUF path → that entry disabled with banner, others unaffected. A class with zero enabled models → router maps that class to `chat-default` and flags it in the debug panel. [INFERENCE — graceful-degradation carry-over] A blob that loads but produces garbage is a real occurrence, not a hypothetical: a Q6_K coder GGUF passed a byte-count check against the remote `content-length` and was still mid-stream-corrupted (`tensor ... not within the file bounds`). `wget -c` cannot detect this. Preflight (F3) loading each model and taking one token is the only cheap check that catches it.

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

**What.** Three strictly ordered layers resolve the model per turn: (1) per-chat manual override always wins; (2) deterministic rules (attachment types force intents; config keyword rules, word-boundary, 2+ words); (3) grammar-constrained classifier (Qwen3-1.7B-Q8_0, CPU resident) emitting schema-enforced JSON. Every decision emitted to the debug panel with source + latency. **Measured 91.76% on the frozen taxonomy at 0.283s/item** — the Phase-2 ≥90% gate is already met in the lab; Phase 2's job is reproducing it in-app. [FACT — PLAN §4.3, `docs/phase0-measurements.md` §13]

**Modules & files.**
- `app/router/router.py` — the ordered pipeline, single entry `route()`.
- `app/router/rules.py` — attachment→intent forcing + keyword rule engine (compiled word-boundary regexes from config).
- `app/router/classifier.py` — prompt build (~600 tokens + few-shots), llama.cpp `response_format: json_schema` call, timeout, confidence gate. **Three things are load-bearing and must be ported verbatim from Phase 0, not re-derived:** the `--reasoning off` server flag (a `/no_think` suffix is not a substitute — it silently fails on some checkpoints), the few-shot examples targeting the *observed* confusions (live-data-without-a-tool-name: `stock price`/`weather`; file-search-vs-code-writing: `grep`/`find files`), and a real token budget.
- `app/router/router.py` also owns resolution: `{class, effort}` → model alias via `routing.intents`; the classifier **never names models** (old spec's mistake). [FACT — PLAN §4.3]
- `eval/router_eval.csv` + `scripts/eval_router.py` — labeled prompt→expected-route set + scorer.

**Interfaces.**
- `async route(chat, text: str, attachments: list) -> RouteResult`, exported from `app/router/__init__.py`.
- `RouteResult {model: str, source: "override"|"rule"|"classifier"|"fallback", intent: str, confidence: float|None, latency_ms: int}` — the type lives in `app/types.py` and is frozen from Phase 1. `effort` and `needs_tools` are rules-layer outputs carried as span fields, not classifier output.
- Classifier JSON schema (GBNF-enforced by llama.cpp — malformed JSON structurally impossible [FACT]), **re-frozen 2026-07-23 to the taxonomy that was actually measured**:
```json
{"class": "chat|chit_chat|code_task|tool_call_needed|reasoning_task|vision_task", "confidence": 0.0}
```
Per-class accuracy: `chat` 100%, `reasoning_task` 100%, `vision_task` 100%, `chit_chat` 90%, `code_task` 88.2%, **`tool_call_needed` 78.9%** (the weak class — expect regressions here first). `effort` and `needs_tools` are **no longer classifier output**; the deterministic rules layer sets them, which is cheaper and testable without a model. A bigger classifier was tested and rejected: Qwen3-4B on the identical prompt costs ~93.7s per classification because that gguf never honors reasoning-suppression flags.
- There is no `route` SSE event: the decision rides the `done` payload as `{"route": {source, intent, model, confidence}}` and is written in full to the `route` span (A4, PLAN §4.2).

**Config keys.**
```yaml
routing:
  enabled: true                  # off = chat-default for everything (override still honored)
  classifier:
    enabled: true
    model: classifier
    timeout_s: 2.0               # timeout → fallback_model
    confidence_threshold: 0.5    # below → fallback_model, flagged in debug
    fallback_model: chat-default
  rules:
    - {keywords: ["write code", "stack trace"], intent: code_task}
  attachments: {image: vision, code_file: coding}   # forced intents
  intents: {chat: chat-default, chit_chat: chat-default, code_task: coder,
            reasoning_task: reasoner, vision_task: vision, tool_call_needed: chat-default}
  # NB: chit_chat routes to chat-default, not utility — utility is CPU-resident
  # (~18-22s/short generation) and answers background jobs only.
```

**Integration points.** Called by the orchestrator before `llm_request`, at the model-resolution seam Phase 1 left. Reads the model roster (F2), attachment types (F11). Emits **one** `route` span carrying source, intent, confidence, latency_ms, and which layer won (not three stage names — see A3). Phase-2 exit gate: ≥90% on eval set. Future upgrade path: ModernBERT-style head behind the same `route()` interface [INFERENCE — PLAN §4.3.4].

**Build steps.** 1) `RouteDecision` + layer-1 override (Phase 1 ships with override-only); 2) rules engine; 3) classifier prompt (~600 tokens, few-shots) + json_schema call; 4) timeout/confidence fallbacks; 5) resolve table; 6) eval CSV (seed from old repo's labeled set, relabeled to class/effort — no model names); 7) `eval_router.py` in CI-optional job (needs classifier model; run on prompt/model change); 8) wire `route` SSE event + spans.

**Tests.** pytest (no model): override beats everything; rules fire on word-boundary only ("scode" ≠ "code"); attachment forcing; classifier timeout → default; low confidence → default + flag; unknown class from schema impossible by construction but resolver still defends. Eval: ≥90% accuracy gate (baseline 91.76%, margin 1.76 points — a drop is a regression to explain, not noise). **Scorer requirement:** `chat` is a substring of `chit_chat`; the label extractor must match longest-first with word boundaries or exactly. A naive `if label in text` scan cost 45 accuracy points in Phase 0 and produced a completely wrong conclusion about model capacity. Playwright: picking a model in composer shows `source: override` in debug view.

**Failure modes.** Classifier process dead → 2s timeout → default model, `route` span records failure; chat never blocks on routing. Rules misconfig (bad regex) → that rule skipped with startup warning. `routing.enabled: false` → everything is `default_model`, still traced.

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

**Integration points.** Reads model roster (F2) for confgen; Code view (F4); router (F5) may set `needs_tools: ["delegate_opencode"]` as a suggestion signal [INFERENCE]. Spans: `opencode_session` (create/prompt/close with session_id), per-event relay counted in span meta. VS Code integration is docs-only (opencode's own extension) [FACT — PLAN §4.4.3].

**Build steps.** 1) Phase 4: install + pin opencode on box, systemd unit; 2) confgen (llama-swap provider) + byte-diff test; 3) smoke-test session API with curl, record shapes; 4) `client.py`; 5) REST façade + SSE relay with terminal guarantee; 6) Code view; 7) delegation confirm flow in chat; 8) `docs/opencode.md` incl. zen switch both directions; 9) optional Windows host registration in Settings.

**Tests.** pytest vs recorded/fake opencode server: session create/prompt/event relay; relay always terminates; disabled host rejected; confgen golden-file test (local and zen variants); `allowed_roots` enforcement (path traversal rejected). Playwright: delegate flow requires explicit confirm; session events render.

**Failure modes.** opencode down → Code area shows offline, chat coding unaffected (falls back to chat-model + sandbox). Version drift breaking API → pinned version + smoke test at startup flips `opencode.enabled` effective-off with banner. Directory outside `allowed_roots` → 400.

## F7. Projects (spec §7)

**What.** Claude.ai-style projects: grid → workspace with instructions, sources/files, project chats, project memory. Filesystem-first (`projects/<id>/instructions.md`, `docs/`) — the one part of the old app that worked — but chats/messages live in SQLite. Ingestion into RAG is incremental on file mtime. [FACT — PLAN §4.5]

**Modules & files.**
- `app/projects/store.py` — project CRUD over the filesystem + a `projects` row cache in SQLite for listing (`projects(id, name, path, created_at)`); `app/projects/ingest.py` — mtime scan → chunker (F10) → embed → Qdrant + FTS, per-file incremental; `app/projects/api.py` — REST + file upload into `docs/`.
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

**Integration points.** Feeds RAG (F10 scope filter `project_id`), memory scope (F17), opencode `allowed_roots` candidate (F6), file_ops tool scope (F9). Spans: `rag_ingest` (fields: project_id, file, chunks, ms). Home stays plain new chat — Projects is nav, not a gate (Bug-3 fix). [FACT]

**Build steps.** 1) filesystem layout + store + SQLite cache; 2) REST; 3) grid + detail views; 4) instructions injection wiring (F17); 5) ingest pipeline hookup (F10); 6) mtime incremental re-ingest; 7) project-scoped retrieval filter proof-test.

**Tests.** pytest: create → dirs exist; instructions round-trip; upload → chunks appear scoped to project; mtime bump → only that file re-ingested; deleting file → chunks removed (`delete_by_source`). Playwright: create project, upload doc, ask a question in a project chat, answer cites the doc (the end-to-end wiring proof).

**Failure modes.** Ingest failure on one file → file marked `error` in UI, others proceed. Filesystem/SQLite cache drift → `reingest` rebuilds cache from disk (disk is truth). Embedder down → ingest queues, FTS-only retrieval meanwhile. [INFERENCE]

## F8. Artifacts + sandboxed execution (spec §8)

**What.** Two toggleable tiers. Tier 1 (client, Phase 3): artifact panel rendering markdown/HTML/SVG/JS in a sandboxed iframe (`sandbox="allow-scripts"`, no same-origin) and Python via Pyodide in a web worker — Claude.ai-artifact parity, zero server risk. Tier 2 (server, Phase 4): `POST /api/exec` runs code in a short-lived locked-down Docker container for the `run_code` tool and dep-needing artifacts. In-chat artifacts always use the **chat model** — never opencode. [FACT — PLAN §4.6, §4.4.1]

**Modules & files.**
- `web/src/artifacts/detect.ts` — **client-side** pure function: finished message text → artifact candidates (html/svg/js/python/markdown/mermaid over a size threshold). Detection is client-side because the artifact is a rendering decision, not a turn outcome; this is why there is no `artifact` SSE event and no server module (PLAN §4.2).
- `app/exec/runner.py` — container lifecycle: `--network none`, mem/cpu/pids limits, read-only rootfs + tmpfs workdir, 30s timeout; images `sandbox-python`, `sandbox-node`; `app/exec/api.py` — `/api/exec`. The docker argv is built in one function with the security flags as **non-optional constants** — a caller cannot disable them, and a test pins the exact argv.
- `docker/sandbox-python/Dockerfile`, `docker/sandbox-node/Dockerfile`.
- `web/src/artifacts/panel.ts` — right-panel controller, tabs (preview/source), per-chat artifact list; `web/src/artifacts/sandbox.ts` — srcdoc sandboxed iframe, postMessage console capture; `web/src/artifacts/pyodide.ts` + `web/workers/pyodide-worker.js` — worker bootstrap, stdout/plot capture; `web/src/artifacts/exec.ts` (Phase 4) — the "Run on server" action.

**Interfaces.** Panel exports exactly three things, and `chat.ts` integrates through those only: `initPanel(hostEl, store)`, `detectArtifacts(text) -> ArtifactCandidate[]`, `showArtifactsFor(messageId)`. Server side: `POST /api/exec {lang: "python"|"node"|"bash", code, files?}` → `{exit_code, stdout, stderr, duration_ms, artifacts: {name: b64}}` (small outputs only, cap in config). Pyodide runner: `run(code) -> {stdout, stderr, result}` via worker postMessage.

**Config keys.**
```yaml
artifacts:
  enabled: true
  tier1: {enabled: true, pyodide: true}
exec:                        # Tier 2; also gates the run_code tool
  enabled: true
  images: {python: sandbox-python:latest, node: sandbox-node:latest}
  timeout_s: 30
  mem_mb: 512
  cpus: 1.0
  pids: 128
  max_output_kb: 256
```
(`--network none` is a hardcoded constant in the runner, not a config key — a security flag a config file can turn off is not a security flag.)

**Integration points.** Orchestrator pipes deltas through `detect.py`; `run_code` tool (F9) calls `app/exec/runner`; Phase-5 sandbox audit hardens it (no backend auth, but tool-executed code is not the owner — sandbox stays locked [FACT — PLAN Phase 5]). Spans: `tool` (name=run_code; container id, limits, exit) and `exec`.

**Build steps.** 1) iframe host + panel with static content; 2) stream detection → panel live-update; 3) Pyodide worker; 4) Docker images; 5) `app/exec/runner.py` with the full limit set; 6) `/api/exec` + `run_code` tool wiring; 7) audit checklist (escape attempts, resource bombs); 8) toggles verified end-to-end.

**Tests.** pytest: detection extracts artifacts from golden streams; exec enforces timeout (sleep 60 → killed), memory cap (alloc bomb → OOM-killed), no network (`curl` fails inside), read-only rootfs (write outside tmpfs fails), output cap. Playwright: HTML artifact renders in panel; JS `alert` sandboxed; Python artifact runs via Pyodide and shows stdout; `exec.enabled: false` → `run_code` absent from tool list (toggle wiring proof).

**Failure modes.** Docker daemon absent → Tier 2 + `run_code` disabled with banner; Tier 1 unaffected. Pyodide load failure (large wasm) → panel shows code with "run unavailable". Runaway container → hard timeout + `docker kill`; orphan sweep at startup. [INFERENCE]

## F9. Tool calls + dispatcher (spec §9, §9.1)

**What.** Primary path: llama.cpp native tool calling (`--jinja` + model chat template) through the OpenAI `tools` API; the orchestrator runs accumulate-deltas → dispatch → append-result, max N iterations. Tools are self-describing modules auto-discovered into a registry, each toggleable. **Dispatcher assist:** for models tagged `tool_call: weak`, the call-emission step routes to the resident `dispatcher` (**Hammer2.1-1.5b**, GPU1, 0.07–0.21s/call) — the dispatcher is never the planner; anything branching on results stays with the main model. [FACT — PLAN §4.7]

**Measured accuracy sets a design constraint** [FACT — §13]: 79.0% call_f1 on a realistic 6-tool registry, **63.75% on a hostile 13-tool registry** with confusable name-trios, 98.8% parse. After few-shot disambiguation the residual errors are argument-fidelity only (dropping articles while copying query text), not wrong-tool selection. **So: keep tool names semantically distinct** — `web_search`/`web_news`/`web_images` and `file_read`/`file_read_lines`/`read_file_metadata` are exactly the shapes that cost accuracy. Embedding-based tool pre-filtering ("tool-RAG") was measured and made things *worse* (60.87% at k=5); don't add it without re-measuring at a much larger registry.

**Modules & files.**
- `app/tools/base.py` — the `Tool` protocol; `app/tools/__init__.py` — registry: auto-discovers tool modules directly under `app/tools/`, filters by `enabled`, renders OpenAI tool schemas, `dispatch(name, args, ctx)` with a `tool` span.
- The delta-accumulation loop lives in `app/chat/orchestrator.py`, not a separate module — it is the turn loop's inner loop (reuse the old build's correct merge *pattern*, not its code), capped at `tools.max_iterations`.
- `app/tools/dispatcher.py` — **not a registry Tool**; the assist module for single-shot call emission: query + tool schemas → one JSON call, used per-step when the active model is `tool_call: weak`. (Was `needle.py`; Cactus Needle was evaluated and dropped — see PLAN §4.7.)
- `app/tools/web_search.py`, `fetch_url.py`, `file_ops.py` (project-scoped read/list/grep, ~100 lines), `run_code.py`, `browser.py` (F15), `memory_save.py`, `memory_search.py` — one flat module each (no `impl/` subdirectory): `name`, `description`, `schema` (JSON Schema), `async execute(args, ctx) -> ToolResult`, `enabled` (config-bound), `consequential: bool` (browser=true → per-chat opt-in).

**Interfaces.** `ToolResult {content: str, is_error: bool, data: dict|None}`; `ToolContext` carries `chat_id, project_id, trace_id, config`. Loop contract: max `tools.max_iterations`; every dispatch emits SSE `tool_start`/`tool_result` and a span with `stage="tool", name=<tool>` (args, duration, error). Dispatcher: `emit_call(text, tool_schemas, client, cfg) -> ToolCallRequest | None` — single shot, no chaining; **every** failure path returns `None` so the caller falls back to native emission. Fine-tune plan (unchanged, deferred): after the registry stabilizes at the end of Phase 3, re-run the Hammer-vs-FunctionGemma comparison against the *real* schemas. The FunctionGemma path is already proven end-to-end — full 250-example finetune → 88.3% call_f1 on a fresh non-overlapping holdout → GGUF conversion (including the upstream `vocab_size` 262144→262146 fix) — with scripts under `scripts/needle_training/`. It lost on registry pressure (36.5%) and per-call latency, not on capability. [FACT — PLAN §4.7]

**Config keys.**
```yaml
tools:
  enabled: true
  max_iterations: 6
  dispatcher_assist: true            # applies to models tagged tool_call: weak;
                                     # the model itself is the roster entry with
                                     # class `dispatcher` — not repeated here
  web_search: {enabled: true}
  fetch_url: {enabled: true, timeout_s: 20, max_bytes: 2000000}
  file_ops: {enabled: true}          # scope = project dirs only
  run_code: {enabled: true}          # requires exec.enabled
  browser: {enabled: false}          # off by default, per-chat toggle (F15)
  memory_save: {enabled: true}
  memory_search: {enabled: true}
search:
  provider_chain: [ddg, tavily]
```
Every `tools.<key>` matches a module filename under `app/tools/` exactly — the startup wiring test asserts that correspondence in both directions, which is the anti-Bug-1 check.

**Integration points.** Called by orchestrator (A4); tools call search (F16), sandbox (F8), BrowserOS (F15), memory (F10/F17), projects fs (F7). Router's `needs_tools` pre-warms nothing but is logged. Debug panel marks dispatcher-assisted turns and shows per-step *who decided vs. who emitted*. [FACT — PLAN §4.7] A dev surface for firing candidate dispatchers at live traffic and comparing them is a known gap (PHASE0_FINDINGS §4) — Phase 5 `llama-bench` panel is its natural home.

**Build steps.** 1) registry + schema render + toggle filtering; 2) loop with delta merge + cap; 3) `web_search`/`fetch_url`/`file_ops` (Phase 3 first wave) **wired into the orchestrator the same PR** (Bug-1 regression rule); 4) SSE + span emission; 5) `run_code` (Phase 4); 6) dispatcher client + weak-model routing; 7) `memory_save/search`; 8) `browser` (Phase 5); 9) dispatcher fine-tune re-run against the stabilized registry (post-Phase-3).

**Tests.** pytest vs fake LLM emitting tool-call deltas: split-across-chunks arguments merge correctly; unknown tool → `is_error` result fed back, loop continues; cap stops runaway loop; disabled tool absent from schemas AND dispatch rejects it (double gate); dispatcher path invoked only for `tool_call: weak` models; dispatcher output validated against schema before execution. **Startup wiring test:** every enabled tool in config appears in the live registry (the anti-Bug-1 test). Playwright: ask "search the web for X" → visible tool chips → cited answer.

**Failure modes.** Tool exception → `is_error` ToolResult to the model (it can recover verbally), never a dead stream. Dispatcher emits an invalid call → schema validation fails → fall back to main-model emission for that step, flagged in debug. Dispatcher picks the *wrong but valid* tool (the realistic failure at 63.75–79% call_f1) → the tool returns a useless-but-honest result and the main model recovers; this is why the assist is per-model opt-in and the primary path stays native tool calling. All tools disabled → plain chat, tools chip hidden.

## F10. RAG + memory (spec §10) — hermes-agent style

**What.** RAG: per-project ingestion → heading-aware chunking (~512 tokens, 20% overlap) → embeddings via the CPU-resident `embed` model (nomic-embed-text-v2-moe, 11.7–34ms/call, **recall@1 96.67% / recall@5 100%** on a 30-query fixture [FACT — §13]) → **Qdrant (vectors) + SQLite FTS5 (lexical)** → hybrid retrieval (vector + BM25, reciprocal-rank fusion) → top-k with source citations in the UI. Memory: discrete fact rows in SQLite (FTS5 + optional embedding), three scopes (user / project / global), injected as a tagged `<memory-context>` block hermes-fashion; all visible/editable in Settings → Memory. Self-improvement: post-turn background review on the utility model *proposes* writes into a review queue (auto-accept optional per scope). [FACT — PLAN §4.8]

**Modules & files.**
- `app/rag/chunker.py` — heading-aware markdown/text chunker (tree-sitter AST chunking for code is a later drop-in); `app/rag/embed.py` — batch embed via `llm_client.embed`; `app/rag/retrieve.py` — hybrid query + RRF + k; `app/rag/cite.py` — citation assembly for SSE.
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
- Retrieval sources ride `done.citations` (see PLAN §4.2 frozen contract) and render under the message — there is no separate `citations` SSE event.

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

**Integration points.** Context assembler (A4) calls `inject` + `retrieve` before `llm_request`; `memory_save/search` tools (F9) write/read the same store; chat-history embedding (F13) reuses embed/retrieve. Spans: `rag_retrieve` (query, k, hit ids, ms), `memory_inject` (counts per scope), `memory_review` (proposal or no-op). Reviewer failures never block chat (background). [FACT]

**Build steps.** 1) chunker + golden-file chunk tests; 2) embed batch path; 3) Qdrant+FTS dual write on ingest (F7), with the id space shared so a delete hits both; 4) hybrid retrieve + RRF; 5) citations end-to-end (`done` payload → UI); 6) memory store + manual CRUD UI; 7) inject wiring with scope rules; 8) `memory_save` tool (Phase 3 manual path); 9) reviewer + queue (Phase 5); 10) auto-accept per-scope toggles.

**Tests.** pytest: chunker respects headings/overlap; hybrid beats either-alone on a fixture set (planted target docs); RRF ordering deterministic; scope injection matrix (user always / project only in project chat / global on match); reviewer proposal lands in queue and is NOT injected until accepted; accepted memory appears in next turn's `<memory-context>` (full wiring test). Playwright: save a preference via chat ("remember I like X") → visible in Settings → next chat reflects it.

**Failure modes.** Embed model or Qdrant down → ingest queues + retrieval degrades to FTS-only (flagged in citations meta) — the hybrid design is what makes this a degraded mode rather than an outage. Reviewer error → dropped silently into a `log` tap event; chat unaffected. Oversized memory set → injection capped by token budget, lowest-relevance dropped, span notes truncation. [INFERENCE]

## F11. Attachments (spec §11)

**What.** Upload → type sniff → extractor registry: text/code direct, PDF (pymupdf), docx/xlsx/pptx (python-docx family or markitdown), images → vision model path; audio is Future. Small extractions go straight to context; large ones become RAG-on-the-fly for that chat. Each extractor is one module. [FACT — PLAN §4.9]

**Modules & files.**
- `app/attachments/api.py` — `POST /api/chats/{id}/attachments` (multipart, streams to `data/attachments/`) + `GET /api/attachments/{id}` metadata; `app/attachments/extract.py` — sniffing plus the extractor registry, `register(mime_prefixes, fn)` so a new type is one entry; `app/attachments/context.py` — the context provider registered on the orchestrator's `gather_context` seam.

**Interfaces.** `POST /api/chats/{id}/attachments` → `{id, filename, kind, mime, extracted_chars}`; the composer then sends `attachments: [id]` in the stream request. `Extraction {text: str|None, images: list[path], meta}`. Threshold logic in the context provider: `extracted_chars <= attachments.max_inline_tokens` (token-estimated) → inline tagged block; larger → chunk+embed via `rag.ingest_text` into chat-scoped ephemeral chunks (`project_id = "chat:<id>"`) retrieved like RAG, or truncated inline with a notice if `app.rag` is not importable. Image attachments force the `vision` intent in the rules layer (F5).

**Config keys.**
```yaml
attachments:
  enabled: true
  max_mb: 50
  max_inline_tokens: 4000
  extractors: {text: true, pdf: true, office: true, image: true}
```

**Integration points.** Router forced intents (F5), context assembly (A4), RAG machinery reuse (F10), vision model path (F2). Spans: `attachment_extract` (fields: mime, chars, ms, extractor).

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

**What.** Qdrant is the vector store (decided in §3.1/A2 after sqlite-vec failed its 100k-vector gate; `SqliteVecStore` remains the in-tree fallback behind the same interface). This feature's user-visible half: "search my past chats" — per-message-batch embeddings + FTS over messages, hybrid-searched from the sidebar. [FACT — PLAN §4.8, §4.11]

**Modules & files.**
- `app/rag/store.py` — (A2) the interface + impl; `app/search_chats/indexer.py` — background job embedding message batches (per N messages or on chat idle) into `vec_messages`; `app/search_chats/api.py` — `GET /api/search/chats?q=` hybrid over `vec_messages` + `messages_fts`, grouped by chat; `web/src/views/search.ts` — sidebar search box + results (chat title, matching snippet, jump-to-message).

**Interfaces.** `GET /api/search/chats?q=&limit=20` → `[{chat_id, title, message_id, snippet, score}]`. Indexer: `index_chat(chat_id)` embeds unembedded batches; runs post-turn fire-and-forget alongside summaries (F18).

**Config keys.**
```yaml
chat_search:
  enabled: true
  embed_batch_messages: 8
  semantic: true            # false = FTS-only
```

**Integration points.** Reuses embed (F10), the background queue (F18), `VectorStore` (A2). Spans: `rag_ingest` and `rag_retrieve` (the chat-search indexer reuses them with a `messages` source tag). [FACT]

**Build steps.** 1) FTS-only search first (works day one); 2) indexer job; 3) hybrid merge; 4) sidebar UI + jump-to-message anchor; 5) backfill script for pre-existing chats.

**Tests.** pytest: FTS finds exact phrase; semantic finds paraphrase in fixture chats; disabled semantic → FTS still serves; backfill idempotent. Playwright: search finds a message sent earlier in the test run, click jumps to it highlighted.

**Failure modes.** Embedder down → semantic column empty, FTS results still returned (flagged `semantic: false` in response meta). Index lag → results eventually consistent; UI notes "indexing…" if unembedded batches exist for matched chats. [INFERENCE]

## F14. GPU delegation & swapgen (spec §14)

**What.** At startup, `nvidia-smi` inventory → GPU inventory endpoint → Settings UI assigns each model to a GPU or CPU. A deterministic module renders `llama-swap.yaml` from config + assignments; changing assignments regenerates the file and triggers llama-swap config reload — programmatic config writing, never AI-generated, never hand-edited. [FACT — PLAN §4.1]

**Four non-negotiable output properties, each an observed-and-fixed defect in the hand-written config swapgen replaces** [FACT — PLAN §4.1]:
1. **A `groups:` block always exists** — CPU residents + `dispatcher` in `resident: {swap: false}`, big models in `gpu0-main: {swap: true}`. Omit it and llama-swap's implicit default group serializes every model, which silently defeats Config B.
2. **Device placement is `CUDA_VISIBLE_DEVICES=<n>` in the entry's `env:`** — never `--tensor-split`. A degenerate split measured ~3x slower than a clean single-device restriction; a real 3,1 split starves GPU1 and OOMs its residents.
3. **`--reasoning off` on `classifier`** (and any alias with `reasoning_off: true`). Without it the classifier spends its budget in `<think>` and returns empty content — the measured 91.76% does not reproduce.
4. **`CUDA_VISIBLE_DEVICES=""` on CPU-placed entries.** `--device none -ngl 0` alone still initializes CUDA contexts worth ~150–256MB per card per process — measured as 256MB stolen from the big slot's headroom by the classifier alone.

**Modules & files.**
- `app/gpu/inventory.py` — parse `nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv`; `app/gpu/swapgen.py` — pure function config→yaml (macros incl. `${PORT}`, per-model `cmd`, groups `resident`/`gpu0-main` per PLAN's sample); `app/gpu/api.py` — inventory endpoint + assignment PATCH + reload trigger; `web/src/views/settings/gpu.ts` — assignment table with live VRAM bars.

**Interfaces.** `GET /api/gpu/inventory` → `[{index, name, mem_total_mb, mem_free_mb}]`; `PATCH /api/models/{alias} {device}` → overlay write → `swapgen.render(config) -> str` → write `llama-swap.yaml` → `POST` llama-swap reload endpoint [UNCERTAIN — reload endpoint name per installed llama-swap version; verify Phase 0] → poll until resident group healthy. `render()` output shape per PLAN §4.1 sample (macros/models/groups; `env: [CUDA_VISIBLE_DEVICES=n]` for GPU, `--device none -ngl 0` for CPU; vision adds `--mmproj`; residents get `ttl: 0`). [FACT — verified against the installed build in Phase 0]

**Config keys.**
```yaml
gpu:
  enabled: true               # false = swapgen never writes; hand-managed yaml honored
  swap_yaml_path: /opt/llama-swap/llama-swap.yaml
  reload_on_change: true
  vram_guard: true            # refuse assignment if model est. size > free VRAM
```

**Integration points.** Consumes model registry (F2); feeds llama-swap (F1); Settings UI (F4); debug panel shows swap state + GPU poll (A3). Spans: `gpu_inventory`, `swapgen`. Exit gate Phase 2: GPU reassignment without backend restart. [FACT]

**Build steps.** 1) inventory parser (fixture-tested on captured nvidia-smi output); 2) swapgen pure function + golden-yaml tests (one per roster scenario: default, model disabled, ctx-variant pair, vision, two-box future map [INFERENCE — future map is config passthrough only]); 3) file write (temp+rename) + generated-file header comment; 4) reload call + health poll; 5) Settings GPU tab; 6) vram_guard estimates (file size × overhead factor from Phase-0 measurements).

**Tests.** pytest: golden yaml byte-diffs; disabled model omitted; **`groups:` block present with the right membership**; **no `--tensor-split` in any emitted cmd**; **`--reasoning off` present for every `reasoning_off: true` alias**; **`CUDA_VISIBLE_DEVICES=""` on every CPU-placed entry**; resident group contains classifier/embed/utility/dispatcher when enabled; guard rejects oversized assignment; reload failure rolls the yaml back to the previous version (kept as `.bak`). The three assertions in bold are regression tests for real observed defects, not hypotheticals. Live: reassign embed CPU→GPU in Settings, preflight still green.

**Failure modes.** `nvidia-smi` absent → CPU-only inventory, big models flagged unassignable, app still runs. Reload fails → rollback yaml + error banner; llama-swap keeps old config (never left half-configured). Hand-edit detected (header hash mismatch) → refuse to overwrite, tell user to move changes into `config.yaml`. [INFERENCE — protects the "never hand-edit" contract]

## F15. BrowserOS integration (spec §15)

**What.** BrowserOS runs on the Windows host (GUI browser, logged-in sessions, you watch the agent); the backend is a generic **MCP client** connecting to BrowserOS's built-in MCP server (31+ tools: navigate, click, scrape, screenshot) over LAN, exposed to capable models as the `browser` toolset — off by default, per-chat toggle (consequential actions). Deep research does NOT ride the browser: search+fetch is primary, browser is the escalation for JS-heavy/logged-in/interactive pages. [FACT — PLAN §4.12]

**Modules & files.**
- `app/mcp/client.py` — minimal MCP client (initialize, list_tools, call_tool) over the transport BrowserOS exposes [UNCERTAIN — SSE vs streamable-HTTP vs stdio-only, and non-localhost reachability; verify Phase 5 before UI work; if localhost-only, a tiny host-side relay or SSH tunnel bridges it (PLAN §4.12.4)]. Generic by design — future MCP servers reuse it.
- `app/tools/browser.py` — bridges MCP tools into our tool registry: prefixes names (`browser.navigate`…), maps schemas, marks `consequential: true`.
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

**Integration points.** Tool registry (F9), attachments/vision (F11/F2), debug spans `browser` (field: action) (args minus sensitive fields, duration, screenshot ref). BrowserOS may independently point its own in-browser agent at llama-swap `/v1` — docs note only, no backend work. [FACT]

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
  provider_chain: [ddg, tavily]
  max_results: 8
  ddg: {enabled: true}
  tavily: {enabled: true, api_key_env: TAVILY_API_KEY}
  breaker: {failures: 3, cooldown_s: 300}
```

**Integration points.** `web_search` tool (F9); deep research (Future) fans out over this same chain; citations (on the `done` payload) carry `provider`. Spans: `search` (field: provider) (query, count, ms, fallback_reason).

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

**Integration points.** Context assembler (A4), projects (F7 instructions), memory (F10 user scope "always injected" [FACT — spec §17 via PLAN]). Span: `memory_inject` meta includes template source (`base|project|chat`).

**Build steps.** 1) template resolver + placeholder engine; 2) wiring into context assembly with order contract; 3) Settings editors + preview (renders the exact final system prompt); 4) project override path; 5) debug panel shows the assembled prompt verbatim (uses `store_prompts`).

**Tests.** pytest: resolution precedence (chat > project > class base); placeholders filled; missing class falls back to `general`; preview equals what `llm_request` span records (the honesty test). Playwright: edit coding template in Settings → next coding-routed message's debug span shows it.

**Failure modes.** Template with unknown placeholder → rendered literally + warning, never a failed turn. Empty template → class falls back to `general` base.

## F18. Auto-title, summaries, compaction (spec §18)

**What.** Two models, split by who waits on the result [FACT — PLAN §4.15, `docs/phase0-measurements.md` §12]:

- **Titles → `dispatcher`** (Hammer2.1-1.5b, GPU1): 8/8 on the title rubric at **0.042s/call**, versus CPU `utility` at 1/8 and **43.5s**. The utility model hits the same thinking-budget trap as the reasoners and mostly returns nothing at a title-sized budget. The sidebar shows a title immediately, so this one is not "background."
- **Rolling summary + compaction → `utility-gpu` first, `utility` (CPU) fallback** (both Qwen3-8B, redesigned 2026-08-15 — see `docs/HANDOFF.md` this-session entry for the live benchmark that drove this). `utility-gpu` is GPU1-resident (always warm, alongside `dispatcher`): measured live ~2700 tok/s prefill / ~70 tok/s decode, ~14x CPU decode speed. `utility` (CPU) is the fallback if the GPU1 call errors: measured live ~55 tok/s prefill / ~5 tok/s decode — slow enough that a naive ctx-sized bite can exceed even a relaxed timeout (this is what the 2026-08-11 double-timeout incident actually was), so the CPU path is capped by a speed-derived token budget (`_time_budget_tokens` in `app/background/summaries.py`), not ctx alone. Both paths trigger on real `prompt_tokens` crossing `summary_context_fraction` of the tightest routable ctx (not a turn-count cadence — see the module docstring for the full incremental-summarization design), and summarize only the delta since the last regen.

Title post-processing is deterministic and lives in code: `clean_title()` **truncates** overlong output and never penalizes a short title. The 8/8 depends on it — an earlier 4/8 was the rubric's fault for demanding an exact 5–8 word range with no recovery path for "too short." Also strip wrapping quotes and markdown code fences. All background; failures never block chat.

**Modules & files.**
- `app/background/queue.py` — tiny in-process task queue (post-turn jobs: title, summary, chat-search indexing, memory review) with per-job-type concurrency 1; `app/background/titles.py` — first-exchange → title prompt → `PATCH` chat + SSE `title` event; `app/background/summaries.py` — rolling `chats.summary` update, triggered on real token pressure (not turn count — see module docstring), tries `utility-gpu` then `utility`; `app/chat/compaction.py` (`maybe_compact(chat)`) — in-turn (not background): when assembled context > threshold, replace oldest turns with summary block before `llm_request`.

**Interfaces.** `queue.submit(job_type: str, payload)`; jobs read/write via repositories only. Compaction message shape: a synthetic assistant-side block `"[Summary of earlier conversation]\n…"` preserved at messages head [INFERENCE — pattern per PLAN]; compaction state stored as `chats` meta (`compacted_before_message_id`) so it's stable across turns.

**Config keys.**
```yaml
background:                                    # Phase 2, extended 2026-08-15
  title_model: dispatcher                      # user-visible → fast model wins (0.042s)
  summary_model: utility                       # CPU fallback
  summary_model_gpu: utility-gpu                # GPU1 fast path, tried first
  summary_every_n_turns: 6                      # fallback cadence, pre-usage-data only
  summary_context_fraction: 0.5                 # primary trigger
  summary_token_threshold: 4000                 # fallback if model ctx unresolved
  summary_timeout_s: 180.0                      # decoupled from llama_swap.timeout_s (120s, tuned for chat)
  summary_cpu_tokens_per_sec_prefill: 55.0       # measured live 2026-08-15
  summary_cpu_tokens_per_sec_decode: 5.0
  summary_gpu_tokens_per_sec_prefill: 2700.0
  summary_gpu_tokens_per_sec_decode: 70.0
compaction:                           # Phase 3 (in-turn, not background)
  enabled: true
  threshold_tokens: 24000
  keep_recent_turns: 8
```
`threshold_tokens` is an absolute count rather than a percentage of the window on purpose: `chat-default` loses **52.9% throughput by 32k** while staying accurate, so the trigger wants tuning against that measured curve, and a percentage hides what is actually being traded. Start at 24000 and move it on evidence.

**Integration points.** Orchestrator post-turn hook submits jobs; compactor sits inside context assembly (A4); chat-search indexer (F13) and memory reviewer (F10) ride the same queue. Spans: `title`, `summary`, `compaction` (tokens before/after). SSE `title` event updates sidebar live.

**Build steps.** 1) queue with error isolation; 2) titler + SSE event + sidebar wiring; 3) rolling summarizer; 4) compactor with token counting (llama.cpp `usage` numbers, else tiktoken-free heuristic chars/4 [INFERENCE]) — tune `compaction.threshold_tokens` against the measured 52.9% throughput degradation at 32k on `chat-default` rather than guessing; 5) thresholds in Settings; 6) debug spans.

**Tests.** pytest vs fake models: `clean_title()` truncates >8 words, strips wrapping quotes and code fences, and passes a short title unchanged (rubric-matches-postprocessing rule); title generated exactly once after first exchange; summary refreshes on cadence; compaction triggers at threshold, keeps N recent verbatim, resulting context under limit; utility model failure → job logged, retried once, chat unaffected (the never-block test). Playwright: sidebar title appears without refresh.

**Failure modes.** Utility model busy/down → jobs queue and retry; titles show "New chat" meanwhile. Compaction failure → fall back to hard truncation of oldest turns with a visible `[context truncated]` marker (degraded but honest).

## F19. Debug panel — the Debug view (spec §19, frontend half; backend in A3)

**What.** A **standalone Debug window** (its own route `#/debug`, meant to be opened in a separate browser window/tab so you watch it live beside the app — not an embedded panel), toggled on in Settings → Debug. Fed by `/api/debug/stream` + trace REST: per-turn waterfall, route decision + why, **exactly what each model was sent and returned** (raw prompts/responses incl. thinking tokens, toggle), **token counts + latency/tok-s derived from llama.cpp** (`usage` + `timings`, never client estimates), every tool call (name/args/result/emitter), llama-swap state, nvidia-smi telemetry, dispatcher-assist markers. Critical infrastructure, shipped in Phase 1 alongside the first chat path. [FACT — PLAN §4.16]

**Modules & files.**
- `web/src/views/debug.ts` — layout: trace list (left), waterfall + span detail (center), live tails (right); `web/src/debug/waterfall.ts` — span rows → CSS-grid timeline (no chart lib); `web/src/debug/live.ts` — tap subscription: GPU bars, swap state, rolling log; `web/src/debug/span_detail.ts` — prompt/response viewer (monospace, copy button), token/latency stats.

**Interfaces.** Consumes A3's REST + SSE verbatim. Per-turn header shows: `route` source chip (override/rules/classifier/default + confidence), model, tok/s (`usage` + `timings` from llama.cpp [FACT]), swap-wait badge if `swap_wait` span present, dispatcher badge if `dispatcher` spans present ("who decided vs. who emitted" per step [FACT — PLAN §4.7]). Deep link: each chat message has a "debug" affordance → `#/debug?trace=<trace_id>`.

**Config keys.** `debug:` (A3) drives it; `ui.debug_link_on_messages: true`.

**Integration points.** Pure consumer of A3; every feature's spans appear here for free — that's the point. Per-model bench numbers (F2 `data/bench.json`) render in a models sub-tab.

**Build steps.** 1) trace list + waterfall from REST (Phase 1); 2) live tap tails; 3) span detail with prompt toggle honoring `store_prompts`; 4) route/dispatcher/swap badges; 5) message→trace deep link; 6) GPU/swap panels; 7) bench tab (Phase 5 `llama-bench` panel [FACT — PLAN Phase 5]).

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
| F8 Artifacts/sandbox | 3–4 | `artifacts.enabled`, `exec.enabled` | code shown, not run |
| F9 Tools + dispatcher | 3–5 | `tools.enabled`, per-tool, `tools.dispatcher_assist.enabled` | plain chat / main-model emission |
| F10 RAG + memory | 3, 5 | `rag.enabled`, `memory.enabled`, `memory.reviewer.enabled` | FTS-only / no injection |
| F11 Attachments | 3 | `attachments.enabled`, per-extractor | upload rejected visibly |
| F12 Testing | 1+ | — (process) | — |
| F13 Chat search | 3 | `chat_search.enabled`, `.semantic` | FTS-only |
| F14 GPU/swapgen | 2 | `gpu.enabled` | hand-managed yaml |
| F15 BrowserOS | 5 | `browseros.enabled` + per-chat | fetch_url path |
| F16 Search chain | 3 | `search.enabled`, per-provider | model answers with caveat |
| F17 Prompts/prefs | 3 | `prompts.enabled`, `memory_inject` | class-base prompt only |
| F18 Title/summary/compaction | 2–3 | `background.*`, `compaction.enabled` | "New chat", hard truncation |
| F19 Debug view | 1 | `debug.enabled` | "tracing disabled" notice |

**Non-goals restated (do not build):** Ollama, LiteLLM, React or any frontend framework/bundler, backend auth, remote providers before Future, opencode inside the chat tool loop, **the dispatcher as planner**, browser-driving as the deep-research primary, **`--tensor-split` in generated configs**, **thinking suppression via prompt convention instead of the server flag**. (Qdrant left this list on 2026-07-23 — the escape hatch triggered on measurement.)
