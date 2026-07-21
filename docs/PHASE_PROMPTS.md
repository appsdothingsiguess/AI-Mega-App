# Phase Prompts — Parallel Cursor Agents

Ready-to-paste prompts for every build phase of the AI Mega App rebuild.
Source of truth for architecture: `PLAN.md`. Feature detail: `docs/FEATURES.md`
(written in parallel). Cursor rules live in `.cursor/rules/` and are specified
in `docs/CURSOR_RULES.md` (written in parallel) — this file references rules,
it never defines them.

## How to use

1. **One worktree = one branch = one Cursor window.** Create worktrees from the
   repo parent, as sibling directories of the main clone:

   ```bash
   cd /path/to/AI-Mega-App
   git worktree add ../AI-Mega-App-p1-llm-client -b p1/llm-client main
   ```

   Open each worktree folder in its own Cursor window. Verify
   `git branch --show-current` matches before any edit. Stay in that folder;
   switching branches inside a shared checkout is how the old build broke.

2. **Paste the agent prompt verbatim** into a fresh Cursor agent session in
   that window. Cursor has no session memory — every prompt below opens with a
   continuity packet (CONTEXT) so the agent starts from truth, not guesses. If
   a session dies mid-task, start a new one, re-paste the same prompt, and add
   one line: "Partial work exists on this branch; read the diff vs main first."

3. **Plan Mode first.** Every multi-file prompt instructs the agent to start in
   Plan Mode (Shift+Tab), present interfaces before implementation, and wait
   for approval. Approve or edit the plan before letting it write code.

4. **FILE SCOPE is a hard boundary, not a guideline.** Each agent owns an
   explicit, non-overlapping path set. Shared files (`pyproject.toml`,
   `config.yaml`, `app/config.py`, `app/main.py`, `src/app.ts`) get exactly
   ONE named owner per wave; every other prompt lists them as read-only.
   Optionally copy each prompt into `.cursor/agent-contracts/<branch>.md` so
   the verification agent can audit against it.

5. **Waves are dependency order.** Do not start a wave until the previous
   wave's branches are merged to `main` and its worktrees recreated from the
   new `main`. Within a wave, all agents run in parallel.

6. **Integrator merges in the listed order**, running the semantic-conflict
   checklist — worktrees prevent file conflicts, they do NOT catch interface
   drift between agents (agent A changes a response shape, agent B consumes
   the old one). The INTEGRATOR prompt per phase exists for exactly this.

7. **Verification pass after every wave.** The VERIFICATION prompt audits each
   branch's diff against its FILE SCOPE and runs the test suite. Run it before
   the integrator merges.

Naming: branches `p<phase>/<task>`, worktrees `../AI-Mega-App-p<phase>-<task>`.
Backend test gate everywhere: `python -m pytest -q` from repo root. Frontend
gate: `tsc --noEmit`. Commits: conventional messages, explicit paths only
(`git add <files>` — rule `007-git-worktrees`).

### Sub-agent delegation (Cursor 3 Agents Window)

Each phase is written as **one orchestrator prompt that delegates each major step to its own sub-agent** in its own worktree, run in parallel where dependencies allow — this is the Cursor 3 model (Agents Window → native worktrees), and the rule is `009-subagents`. Every phase section names, explicitly: which steps are delegated, the worktree per sub-agent, which run at the same time, and which wait. Phase 0 is the worked example (ORCHESTRATOR → P0-A tooling ∥ P0-B serving, then P0-C benchmark). Read a step's "Sub-agent" tag as "spawn a delegated agent here," not "do it inline." One sub-agent = one worktree = one FILE SCOPE = one completion report.

---

# Phase 0 — Ground truth (box + inference)

**Nature: box work over `ssh ubuntu-ai` (rule `008-remote-box`) + a little repo tooling. Produces measured facts, not app code.** The full test spec is `docs/BENCHMARK_PLAN.md` (single box: 3090 + 3070) — this section is the *delegation*: one orchestrator, sub-agents per major step, named worktrees, run in parallel where dependencies allow.

**Already done on the box — do NOT redo:** ✅ 0.1 Drivers + CUDA · ✅ 0.2 llama.cpp built (binaries at `/home/john/llm-stack/engine/llama.cpp/build/bin/`). Record their versions/flags into the measurements doc from the installed build; don't reinstall.

### Sub-agent delegation (the "one major prompt → delegated sub-agents" model)

Paste the **Phase 0 ORCHESTRATOR** prompt (below) into one Cursor agent. It spawns these sub-agents, each in its own worktree, and runs P0-A and P0-B **at the same time**:

| Sub-agent | Worktree | Owns | Runs |
|---|---|---|---|
| **P0-A · tooling** | `../AI-Mega-App-p0-measure` (branch `p0/measure`) | repo bench scripts + measurements template (0.6) — no box dependency | immediately, parallel with P0-B |
| **P0-B · serving** | `../AI-Mega-App-p0-serving` (branch `p0/serving`) | on box via SSH: llama-swap systemd + first config + swap/concurrency verify (0.3, 0.5) | immediately, parallel with P0-A |
| **P0-C · benchmark** | reuses `../AI-Mega-App-p0-measure` | on box: model downloads + per-class benchmarks + **placement verdict** per BENCHMARK_PLAN (0.4 + §2–§6), fills measurements doc | **after** A's scripts and B's serving exist |

Each sub-agent gets a FILE SCOPE and starts in Plan Mode. Only P0-A and P0-C write repo files, and they own **different** files (scripts vs the measurements doc is co-owned → P0-A creates the template, P0-C fills it, sequential, no overlap). P0-B touches only the box, not the repo.

### Phase 0 ORCHESTRATOR (paste this one)

```
You are the Phase 0 orchestrator for the AI Mega App rebuild. Source of truth:
PLAN.md §4.1/§5; the test spec is docs/BENCHMARK_PLAN.md; box access + paths are
in .cursor/rules/008-remote-box (ssh ubuntu-ai; llama.cpp at
/home/john/llm-stack/engine/llama.cpp/build/bin; models at
/home/john/llm-stack/models). Drivers/CUDA and llama.cpp are ALREADY DONE.

Delegate, do not implement yourself:
1. Create two worktrees from main:
     git worktree add ../AI-Mega-App-p0-measure  -b p0/measure  main
     git worktree add ../AI-Mega-App-p0-serving  -b p0/serving  main
2. Spawn sub-agent P0-A (tooling) in ../AI-Mega-App-p0-measure with the "0.6
   p0/measure" prompt, and sub-agent P0-B (serving) in ../AI-Mega-App-p0-serving
   with the "0.3+0.5 p0/serving" prompt. Run BOTH at once.
3. When A and B report done, spawn P0-C (benchmark) in ../AI-Mega-App-p0-measure
   with the "0.4+benchmarks p0/measure" prompt.
4. sudo on the box is permission-gated: if any sub-agent needs sudo, it must ask
   YOU, and you ask the human — never auto-approve (rule 008).
Collect each sub-agent's completion report; do not merge until VERIFICATION.
```

## 0.3 — Sub-agent P0-B (serving): llama-swap + systemd

Worktree `../AI-Mega-App-p0-serving`. Box work over `ssh ubuntu-ai` (rule 008); touches the box, not repo files.

```
On the box (ssh ubuntu-ai; llama.cpp already built at
/home/john/llm-stack/engine/llama.cpp/build/bin):
1. Install llama-swap (latest release binary) under /home/john/llm-stack/serving.
2. Create systemd unit llama-swap.service: runs as the john user (non-root),
   listens on 0.0.0.0:8080 (trusted LAN, no auth — PLAN.md §7), config at
   serving/llama-swap/config.yaml, Restart=on-failure. (systemd install needs
   sudo — ASK the orchestrator first, rule 008.)
3. systemctl enable --now llama-swap; verify the web UI answers on :8080.
4. Record the config-reload endpoint name/method for the installed version, and
   pin the llama-swap version, in docs/phase0-measurements.md (hand to P0-C).
```

## 0.4 — Sub-agent P0-C (downloads): candidate GGUFs

Box work. Pull the candidate set defined in **docs/BENCHMARK_PLAN.md §2** (per-class candidates incl. the quant/vision/reasoner A/Bs) into `/home/john/llm-stack/models/blobs`. Check `df -h` before large pulls; delete superseded blobs (rule 008). Record exact filename, SHA/source URL, size in `docs/phase0-measurements.md`. Do not download the full matrix blindly — pull per class as you benchmark so the 363G mount doesn't fill.

## 0.5 — Sub-agent P0-B (serving): first llama-swap config + swap verification

Box work, same worktree as 0.3.

```
1. Hand-write serving/llama-swap/config.yaml on the box (the ONLY hand-written
   copy ever — Phase 2 swapgen takes over): macros block with the llama-server
   path (/home/john/llm-stack/engine/llama.cpp/build/bin/llama-server); groups
   and device assignment follow the PLACEMENT DECISION from
   docs/BENCHMARK_PLAN.md §5 (Config A: big models --tensor-split 3,1, residents
   embed/utility on CPU; or Config B: 3090-solo big, embed/utility on the 3070).
   Until §5 is decided, stand up Config A (the working hypothesis).
2. Verify with curl against :8080/v1:
   a. chat completion on chat-default answers.
   b. while chat-default is loaded, classifier (and Config-A CPU residents)
      answer CONCURRENTLY (resident group is not evicted).
   c. request coder → measure wall-clock swap latency (expect 3–10s); repeat
      for reasoner and vision; record each.
   d. vision: send one image; confirm the mmproj path works.
3. Record all swap latencies + per-model VRAM (nvidia-smi during load) into
   docs/phase0-measurements.md.
```

## 0.6 — Sub-agent P0-A (tooling), then P0-C fills it: `p0/measure`

Worktree: `../AI-Mega-App-p0-measure`. **P0-A** writes the scripts + measurements template (below); **P0-C** later runs them on the box and fills the results + placement verdict per `docs/BENCHMARK_PLAN.md`.

```
CONTEXT
This is a fresh rebuild of AI-Mega-App; the repo currently contains planning
docs only. Phase 0 measures ground truth on the Ubuntu GPU box before any app
code exists. Drivers/CUDA + llama.cpp are already installed (rule 008 paths).
The test spec you implement tooling for is docs/BENCHMARK_PLAN.md (single box:
3090+3070). Source of truth: PLAN.md §4.1, §5; docs/BENCHMARK_PLAN.md.

GOAL
Write the measurement tooling and the results document skeleton:
1. scripts/bench_models.sh — wraps llama-bench for every GGUF in /models:
   prints model name, quant, prompt tok/s, gen tok/s as a markdown table row.
   Accepts a models dir argument (default /models).
2. scripts/bench_sqlitevec.py — benchmarks sqlite-vec at 100k chunks:
   generates 100k random 768-dim float32 vectors, inserts into a vec0 virtual
   table in a temp SQLite file (WAL), then measures top-10 KNN query latency
   (p50/p95 over 100 queries) and hybrid FTS5+vector query latency. Prints a
   markdown table. Pure stdlib + sqlite-vec + numpy only.
3. docs/phase0-measurements.md — template with empty sections the operator
   fills: driver/CUDA versions, GPU index map, llama.cpp commit + flag
   spellings, llama-swap version + reload endpoint, per-model table (file,
   VRAM, load time, tok/s), swap latencies, sqlite-vec results, and a final
   "Decisions" section (reasoner A/B winner, sqlite-vec verdict per PLAN.md
   §3.1 escape hatch).

NON-GOALS
No FastAPI app, no config.yaml, no llama-swap.yaml in the repo, no CI.

FILE SCOPE (hard boundary — touch nothing else)
  scripts/bench_models.sh
  scripts/bench_sqlitevec.py
  docs/phase0-measurements.md

INTERFACES
Scripts run standalone on the box with python3.12; bench_sqlitevec.py exits
nonzero and prints a clear message if the sqlite-vec extension fails to load.

CONSTRAINTS
Start in Plan Mode; present the plan before writing. Keep each file under 300
lines. Add dependencies only after asking (numpy + sqlite-vec expected). Use
affirmative, commented shell. Scripts must be re-runnable (idempotent temp
files).

ACCEPTANCE
scripts run without syntax errors (bash -n; python -m py_compile). The
measurements doc has a fillable slot for every unknown listed in PLAN.md §7.

STOP CONDITION
If you believe any other file needs changing, stop and ask before touching it.

FINAL STEPS
Commit as `feat(phase0): benchmark scripts + measurements template`. Report:
files created, how the operator runs each script.
```

**Phase 0 exit:** `docs/phase0-measurements.md` fully filled in; roster chosen; **placement config (A/B) decided from measured CPU-resident latency** (BENCHMARK_PLAN §5); reasoner + vision A/Bs decided; sqlite-vec verdict recorded; llama-swap live on :8080 with the kept set. No app code exists. Merge `p0/serving` then `p0/measure`.

---

# Phase 1 — Skeleton with eyes

FastAPI app: config, SQLite, llm_client, SSE chat against a manually picked
model, minimal chat UI, **debug trace store + Debug panel**, CI (PLAN.md §5
Phase 1). Exit: chat with any manually-picked model, every turn fully traced.

## Wave 1 (sequential — one agent, defines all shared files)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| foundation | `p1/foundation` | `../AI-Mega-App-p1-foundation` | `pyproject.toml`, `config.yaml`, `.env.example`, `app/__init__.py`, `app/config.py`, `app/types.py`, `app/db.py`, `app/schema.sql`, `app/main.py`, `tests/test_config.py`, `tests/test_db.py` | Phase 0 merged |

## Wave 2 (parallel)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| llm-client | `p1/llm-client` | `../AI-Mega-App-p1-llm` | `app/llm_client.py`, `tests/fakes/__init__.py`, `tests/fakes/fake_llama_swap.py`, `tests/test_llm_client.py` | foundation |
| debug-trace | `p1/debug-trace` | `../AI-Mega-App-p1-debug` | `app/debug/**`, `tests/test_debug_trace.py` | foundation |
| chat-sse | `p1/chat-sse` | `../AI-Mega-App-p1-chat` | `app/chat/**`, `app/main.py` (OWNER this wave), `tests/test_chat_sse.py`, `tests/golden/**` | foundation |
| web-shell | `p1/web-shell` | `../AI-Mega-App-p1-web` | `src/**`, `web/**`, `tsconfig.json` | foundation |
| ci-harness | `p1/ci-harness` | `../AI-Mega-App-p1-ci` | `.github/workflows/ci.yml`, `tests/conftest.py`, `e2e/**`, `playwright.config.ts`, `scripts/dev.sh` | foundation |

Shared-file owners this wave: `app/main.py` → chat-sse. `pyproject.toml`,
`config.yaml`, `app/config.py`, `app/types.py` → frozen (owner was
foundation); all wave-2 agents treat them as read-only and ask if a change is
needed.

## Prompt: `p1/foundation`

```
CONTEXT
Fresh rebuild of AI-Mega-App. Repo contains PLAN.md, docs/, and Phase 0
benchmark scripts — no app code yet. You are the foundation agent: everything
you write becomes the frozen interface for five parallel agents in the next
wave. Hardware facts (models, VRAM, tok/s) are in docs/phase0-measurements.md.
Source of truth: PLAN.md §3, §3.1, §4.16, §5 (Phase 1); docs/FEATURES.md §core.

GOAL
1. pyproject.toml — Python 3.12 project `ai_mega_app`; deps: fastapi, uvicorn,
   httpx, pydantic, pyyaml, sqlite-vec; dev: pytest, pytest-asyncio, ruff.
   Include ruff config (line length 100) and pytest config here.
2. config.yaml — checked-in defaults, the ONE hand-edited config (plus .env
   for secrets). Keys:
     server: {host: "0.0.0.0", port: 8000}
     llama_swap: {base_url: "http://127.0.0.1:8080/v1", timeout_s: 120}
     db: {path: "data/app.db"}
     models: list of {name, class: general|coding|tool|reasoning|vision|
       utility|embed|classifier|needle, ctx, gpu: 0|1|cpu, tool_call:
       native|weak|none} — seed from the PLAN.md §4.1 roster aliases
     defaults: {chat_model: "chat-default", utility_model: "utility"}
     debug: {store_prompts: true}
3. .env.example — TAVILY_API_KEY= (secrets only; empty placeholders).
4. app/config.py — pydantic models mirroring the schema; load_config(path) ->
   Config; merges optional settings.local.yaml overlay; clear ValidationError
   on bad config. get_config() cached accessor.
5. app/types.py — shared dataclasses/pydantic types other agents import:
   ChatDelta(content, tool_calls, finish_reason, usage), SSEEvent(event, data),
   RouteResult(model, source, intent, latency_ms, confidence) [stub for P2],
   Span / TraceId aliases.
6. app/schema.sql + app/db.py — SQLite WAL, sqlite-vec loaded. Tables:
   chats(id TEXT PK, title, project_id, model_override, created_at, updated_at)
   messages(id TEXT PK, chat_id FK, role, content, model, created_at)
   traces(trace_id TEXT PK, chat_id, started_at)
   spans(id INTEGER PK, trace_id FK, stage, started_at, ended_at, data JSON)
   settings_overlay(key TEXT PK, value JSON)
   db.py: connect(path), init_db(conn), thin query helpers. Deterministic
   hand-written SQL (PLAN.md §6 guardrail) — no ORM.
7. app/main.py — create_app() FastAPI factory: loads config, opens DB, mounts
   /health (returns {status, version, db: ok}), serves web/ statically.
   Leave clearly marked mount points (comment blocks) where wave-2 agents'
   routers will be included.
8. tests/test_config.py, tests/test_db.py.

NON-GOALS
No chat endpoint, no llm_client, no frontend files, no CI workflow, no router.

FILE SCOPE (hard boundary)
  pyproject.toml  config.yaml  .env.example  app/__init__.py  app/config.py
  app/types.py  app/db.py  app/schema.sql  app/main.py
  tests/test_config.py  tests/test_db.py

INTERFACES (you DEFINE these; next wave implements against them — be exact)
As specified in GOAL. Model and provider names appear only in config.yaml,
never in Python literals.

CONSTRAINTS
Start in Plan Mode; present the config schema + types signatures for approval
before implementation. Keep every file under 300 lines. Add dependencies
beyond the listed set only after asking. All I/O async-compatible (aiosqlite
is unnecessary — use sqlite3 in a thread executor helper provided by db.py).

ACCEPTANCE
python -m pytest -q passes from repo root. uvicorn app.main:app starts and
GET /health returns 200 with db:ok (prove it with a TestClient test).
config.yaml round-trips through load_config with zero validation errors.

STOP CONDITION
If any file outside FILE SCOPE seems necessary, stop and ask.

FINAL STEPS
Run python -m pytest -q from repo root. Commit
`feat(foundation): config, types, sqlite schema, app factory`. Report: every
public interface signature (verbatim) — it becomes the wave-2 contract.
```

## Prompt: `p1/llm-client`

```
CONTEXT
AI-Mega-App rebuild, Phase 1 wave 2. Merged on main: app/config.py
(load_config/get_config), app/types.py (ChatDelta), app/db.py, app factory in
app/main.py. llama-swap fronts all models at config.llama_swap.base_url; the
`model` field in an OpenAI-style request selects the model and llama-swap
swaps transparently. Source of truth: PLAN.md §4.1 (Client paragraph), §3.1;
docs/FEATURES.md §inference.

GOAL
app/llm_client.py — the ONLY module that talks to llama-swap. One class:

  class LLMClient:
      def __init__(self, base_url: str, timeout_s: float): ...
      async def chat(self, model: str, messages: list[dict], *,
                     tools: list[dict] | None = None,
                     response_format: dict | None = None,
                     stream: bool = True) -> AsyncIterator[ChatDelta]
      async def embed(self, model: str, texts: list[str]) -> list[list[float]]
      async def models(self) -> list[str]          # GET /models
      async def close(self) -> None

chat() speaks OpenAI chat-completions over httpx, parses SSE deltas into
ChatDelta (content deltas, tool_call deltas accumulated per index, final
usage/timings from llama.cpp). Connection errors, HTTP errors, and timeouts
raise a typed LLMError(kind, detail) — callers translate to SSE `error`
events; a stream must never just go silent (old build Bug 2).
Also: tests/fakes/fake_llama_swap.py — an in-process ASGI app mimicking
llama-swap's /v1/chat/completions (streaming, canned responses, scriptable
tool calls + errors), /v1/embeddings, /v1/models. Every later phase's tests
use this fake; make responses configurable per test.

NON-GOALS
No retry/queue logic, no model scheduling (llama-swap owns swaps), no router,
no changes to app/main.py.

FILE SCOPE (hard boundary)
  app/llm_client.py  tests/fakes/__init__.py  tests/fakes/fake_llama_swap.py
  tests/test_llm_client.py
READ-ONLY: app/config.py, app/types.py, pyproject.toml, config.yaml.

INTERFACES
Exactly the class above; ChatDelta from app/types.py. base_url and model
names always arrive as parameters sourced from config — zero hardcoded names.

CONSTRAINTS
Start in Plan Mode. Files under 300 lines. httpx only; add dependencies only
after asking. Timeouts explicit on every request.

ACCEPTANCE
pytest -q passes. Tests cover: streamed content deltas, tool-call delta
accumulation, usage extraction, timeout → LLMError, HTTP 500 → LLMError,
embed round-trip — all against the fake, no network.

STOP CONDITION
If app/types.py needs a field added to ChatDelta, stop, state the exact field,
and ask (types.py is owned by foundation).

FINAL STEPS
Run python -m pytest -q from repo root. Commit
`feat(llm): llama-swap client + fake server`. Report interface as built,
fake-server capabilities, tests added.
```

## Prompt: `p1/debug-trace`

```
CONTEXT
AI-Mega-App rebuild, Phase 1 wave 2. The debug panel is CRITICAL
infrastructure built FIRST, not last — every later pipeline stage must write
spans through this module (PLAN.md §4.16). Merged on main: app/db.py with
traces/spans tables, app/types.py, app factory. Source of truth: PLAN.md
§4.16, §5 (Phase 1); docs/FEATURES.md §debug.

GOAL
app/debug/ package:
1. app/debug/trace.py — the span API every feature will call:
     new_trace(chat_id) -> trace_id
     @asynccontextmanager
     async def span(trace_id, stage: str, **fields) -> SpanHandle
   SpanHandle.set(**fields) adds data mid-span. On exit, writes the span row
   (stage, timestamps, JSON data incl. model, token counts, latency) to
   SQLite and publishes it to live subscribers. Stage names are free-form
   strings; document the Phase 1 vocabulary: route, llm_request, llm_stream,
   sse_emit, swap_wait, db.
2. app/debug/bus.py — in-process pub/sub (asyncio) feeding live SSE taps.
3. app/debug/api.py — APIRouter:
     GET /api/debug/traces?chat_id=&limit=      (recent traces, spans nested)
     GET /api/debug/trace/{trace_id}            (full waterfall JSON)
     GET /api/debug/stream                      (SSE tap: every span as event
                                                 `span`, heartbeat every 15s)
   Prompt bodies included only when config.debug.store_prompts is true.
4. tests/test_debug_trace.py.

NON-GOALS
No frontend (web-shell builds the Debug view against your endpoints), no
nvidia-smi polling (Phase 2), no llama-swap state proxy (Phase 2), no edits
to app/main.py (chat-sse owns wiring this wave).

FILE SCOPE (hard boundary)
  app/debug/__init__.py  app/debug/trace.py  app/debug/bus.py
  app/debug/api.py  tests/test_debug_trace.py
READ-ONLY: app/db.py, app/types.py, app/config.py.

INTERFACES
Exactly the functions above; app/debug/__init__.py re-exports new_trace and
span. api.py exposes `router = APIRouter()` for main.py to include. SSE
events on /api/debug/stream: `span` (JSON span row), `heartbeat`.

CONSTRAINTS
Start in Plan Mode. Files under 300 lines. Span writes are fire-and-forget:
a debug failure logs a warning and never breaks the chat path. No new
dependencies without asking.

ACCEPTANCE
pytest -q passes. Tests: span rows persist with correct timing; nested spans
share a trace; /api/debug/stream delivers a span published after subscribe;
a deliberately failing DB write does not raise into the caller.

STOP CONDITION
If the spans schema in app/schema.sql needs a column, stop and ask
(foundation owns schema.sql).

FINAL STEPS
Run python -m pytest -q from repo root. Commit
`feat(debug): trace store, span API, SSE tap`. Report the exact span API
signature and stage-name vocabulary — every later agent codes against it.
```

## Prompt: `p1/chat-sse`

```
CONTEXT
AI-Mega-App rebuild, Phase 1 wave 2. Merged on main: config, types, DB
(chats/messages tables), app factory with marked mount points. Being built in
parallel (code against their declared interfaces, import lazily):
app/llm_client.py (LLMClient.chat -> AsyncIterator[ChatDelta]) and app/debug
(new_trace, span async context manager, stages: route, llm_request,
llm_stream, sse_emit). You OWN app/main.py this wave. Source of truth:
PLAN.md §4.2 (SSE rule), §4.16, §5 (Phase 1); docs/FEATURES.md §chat.

GOAL
1. app/chat/orchestrator.py — ChatOrchestrator.handle_message(chat_id, text,
   model: str | None) -> AsyncIterator[SSEEvent]:
   resolve model = explicit model ?? chat.model_override ??
   config.defaults.chat_model (router arrives in Phase 2 behind this same
   resolution point — leave a marked seam). Persist user message; stream
   completion via LLMClient; persist assistant message with the model name
   that produced it; wrap every stage in a debug span.
2. app/chat/api.py — APIRouter:
     POST /api/chats                     -> {id}
     GET  /api/chats                     -> list (id, title, updated_at)
     GET  /api/chats/{id}/messages       -> history with per-message model
     POST /api/chats/{id}/messages       -> SSE stream (see events below)
     POST /api/chats/{id}/model          -> set model_override (null clears)
3. app/chat/history.py — message persistence helpers over app/db.py.
4. app/main.py — include chat router + debug router (app.debug.api.router),
   keep /health and static serving intact.
5. SSE contract (the golden rule from old Bug 2): every stream terminates
   with exactly one `done` or one `error`. Events this phase:
     event: token          data: {"text": "..."}
     event: model_loading  data: {"model": "..."}   (emitted when first token
                            latency exceeds 2s — llama-swap is swapping)
     event: done           data: {"message_id", "model", "usage"}
     event: error          data: {"kind", "detail"}
6. tests/test_chat_sse.py + tests/golden/basic_turn.txt — a golden transcript
   of the full event sequence for one turn against the fake llama-swap;
   contract test diffs future changes (PLAN.md §4.10).

NON-GOALS
No router/classifier, no tools, no titles/summaries, no frontend files, no
changes to app/llm_client.py or app/debug/**.

FILE SCOPE (hard boundary)
  app/chat/__init__.py  app/chat/orchestrator.py  app/chat/api.py
  app/chat/history.py  app/main.py  tests/test_chat_sse.py  tests/golden/**
READ-ONLY: app/config.py, app/types.py, app/db.py, app/llm_client.py,
app/debug/**.

INTERFACES
Endpoints + SSE events exactly as above (web-shell builds against this list
verbatim). Model names flow from config/request — never a string literal.

CONSTRAINTS
Start in Plan Mode; present the SSE event list + endpoint table for approval
first. Files under 300 lines. LLMError and unexpected exceptions both become
a terminal `error` event — a stream that ends any other way is a bug. Every
stage emits a debug span.

ACCEPTANCE
pytest -q passes. Golden-transcript test green. Wiring proof: a TestClient
test drives POST /api/chats → POST message → asserts token events then done,
AND asserts spans exist for the turn's trace via /api/debug/trace/{id} —
end-to-end, not just unit-level.

STOP CONDITION
If llm_client or debug interfaces don't match their declared contract when
you integrate, stop and report the mismatch — the integrator resolves drift.

FINAL STEPS
Run python -m pytest -q from repo root. Commit
`feat(chat): orchestrator, SSE endpoint, golden contract`. Report endpoints,
event vocabulary, and the model-resolution seam left for Phase 2.
```

## Prompt: `p1/web-shell`

```
CONTEXT
AI-Mega-App rebuild, Phase 1 wave 2. Frontend is TypeScript compiled by plain
tsc to native ES modules — no React, no bundler, no framework (PLAN.md §3.1).
UI target is a 1:1 mirror of claude.ai web layout; static mock FIRST, approved
before logic (PLAN.md §4.2). Backend (parallel agent) exposes:
  POST /api/chats · GET /api/chats · GET/POST /api/chats/{id}/messages (SSE:
  token, model_loading, done, error) · POST /api/chats/{id}/model
  GET /api/debug/traces · GET /api/debug/trace/{id} · GET /api/debug/stream
  (SSE: span, heartbeat) · GET /health
Source of truth: PLAN.md §4.2, §4.16, §3.1; docs/FEATURES.md §ui.

GOAL
1. tsconfig.json — strict, target ES2022, module ES2022, outDir web/js,
   rootDir src. `tsc` is the entire build.
2. Static mock first: web/index.html + web/css/theme.css (all colors/spacing
   as CSS custom properties) + web/css/app.css — claude.ai layout: collapsible
   left sidebar (new chat, Chats, recents; Projects placeholder), centered
   chat column, composer with model picker slot, right panel placeholder,
   Debug nav item. PAUSE after the mock and ask for approval before logic.
3. src/router.ts (~200 lines, hash-based) · src/store.ts (~150 lines pub/sub)
   · src/api.ts (typed fetch + SSE client with auto-reconnect; a stream that
   ends without done/error renders "connection lost" — hard rule) ·
   src/app.ts (boot + view registry).
4. Views, each `export function mount(el, store)` / `unmount()`:
   src/views/chat.ts — history, streamed tokens, per-message model label,
   "loading <model>…" indicator on model_loading, composer model picker
   (populated from GET /api/chats + a models list you read from /health for
   now; picker POSTs /api/chats/{id}/model).
   src/views/debug.ts — trace list + per-turn waterfall (spans as horizontal
   bars with stage, latency, model), live-updating from /api/debug/stream.
5. Markdown: vendor `marked` + DOMPurify into web/vendor/ (small pinned
   files); highlight.js for code blocks.

NON-GOALS
No Projects UI, no Settings UI, no artifacts panel (Phase 2–3), no backend
edits of any kind, no npm bundler config.

FILE SCOPE (hard boundary)
  src/**  web/**  tsconfig.json
READ-ONLY: everything else.

INTERFACES
Consume the endpoint/event list in CONTEXT verbatim. Each view = one TS
module + one CSS file.

CONSTRAINTS
Start in Plan Mode. Mock-approval gate before any TS logic. Files under 300
lines (split views into modules instead). Vendored libs are the only
third-party code; add more only after asking. All user/model content rendered
through DOMPurify.

ACCEPTANCE
tsc --noEmit clean. Wiring proof: with the backend running against the fake
llama-swap (scripts/dev.sh from ci-harness, or uvicorn directly), a message
sent from the UI streams tokens, shows the model label, and the Debug view
shows the turn's waterfall. Record a manual checklist result in the report.

STOP CONDITION
If a backend endpoint you need is missing or shaped differently, stop and
report — the integrator reconciles; invent nothing.

FINAL STEPS
Run tsc --noEmit; run python -m pytest -q from repo root (must stay green —
you touched no backend files). Commit
`feat(web): claude.ai-parity shell, chat + debug views`. Report views built
and any endpoint drift observed.
```

## Prompt: `p1/ci-harness`

```
CONTEXT
AI-Mega-App rebuild, Phase 1 wave 2. Merged: pyproject.toml (pytest + ruff
config), app skeleton. Parallel agents are adding app/llm_client.py (+
tests/fakes/fake_llama_swap.py), app/chat/**, app/debug/**, src/** frontend.
CI must need no GPU: everything runs against the fake llama-swap (PLAN.md
§4.10). Source of truth: PLAN.md §4.10, §5 (Phase 1); docs/FEATURES.md
§testing.

GOAL
1. tests/conftest.py — shared fixtures: tmp SQLite db, loaded test config
   (points llama_swap.base_url at the in-process fake), app TestClient
   factory. Import the fake lazily so your branch tests green before merge.
2. .github/workflows/ci.yml — jobs: lint (ruff check), typecheck (tsc
   --noEmit, tolerate absent tsconfig until web-shell merges by guarding on
   file existence), pytest, e2e (Playwright vs fake-LLM backend).
3. e2e/ + playwright.config.ts — scaffold + 2 smoke specs against uvicorn +
   fake llama-swap: send a message and see streamed text; debug view
   populates. Skip cleanly when the frontend isn't built yet.
4. scripts/dev.sh — starts the fake llama-swap + uvicorn with reload + tsc
   --watch for local development, single command.

NON-GOALS
No app code, no frontend views, no GPU/live-hardware checks (preflight is
Phase 5).

FILE SCOPE (hard boundary)
  tests/conftest.py  .github/workflows/ci.yml  e2e/**  playwright.config.ts
  scripts/dev.sh
READ-ONLY: pyproject.toml (foundation owns it — if a dev-dependency like
playwright must be added, stop and ask).

INTERFACES
Fixtures: `db_conn`, `test_config`, `app_client`. CI gate = lint + typecheck
+ pytest + Playwright-vs-fake (PLAN.md §4.10 gate list, exactly).

CONSTRAINTS
Start in Plan Mode. Files under 300 lines. CI must pass on a bare runner —
zero network to model hosts, zero GPU.

ACCEPTANCE
python -m pytest -q passes locally. `act`-style dry run not required; instead
prove the workflow YAML is valid (yamllint or python -c yaml.safe_load) and
scripts/dev.sh starts and serves /health.

STOP CONDITION
pyproject.toml changes require asking. If fixture needs conflict with what a
parallel agent ships in tests/, note it for the integrator instead of editing
their files.

FINAL STEPS
Run python -m pytest -q from repo root. Commit
`chore(ci): pipeline, shared fixtures, e2e scaffold, dev script`. Report jobs
configured and fixtures exposed.
```

## Prompt: Phase 1 INTEGRATOR

```
ROLE: Integrator for Phase 1. You merge; you do not build features.

CONTEXT
Wave 1 (p1/foundation) is on main. Wave 2 branches ready: p1/llm-client,
p1/debug-trace, p1/chat-sse, p1/web-shell, p1/ci-harness. Worktrees isolated
files, but NOT interfaces — your job is catching semantic drift.
Source of truth: PLAN.md §5 (Phase 1 exit), §6.

MERGE ORDER (into main, one at a time, pytest between each)
1. p1/llm-client   2. p1/debug-trace   3. p1/ci-harness
4. p1/chat-sse     5. p1/web-shell

SEMANTIC-CONFLICT CHECKLIST (run before/after each merge)
[ ] chat-sse imports LLMClient/ChatDelta exactly as llm-client shipped them
    (signature diff, not eyeball).
[ ] chat-sse span stage names match debug-trace's documented vocabulary.
[ ] SSE event names/payloads in app/chat/api.py match what src/api.ts and
    src/views/chat.ts parse (grep both sides, compare literally).
[ ] Debug view consumes /api/debug/stream event name `span` as shipped.
[ ] conftest fixtures don't shadow fixtures defined in feature test files.
[ ] app/main.py includes BOTH routers (chat, debug) and static serving —
    "built but not injected" is a rejected merge (rule 005-integration).
[ ] No agent modified files outside its FILE SCOPE (verification agent's
    report confirms; spot-check pyproject.toml/config.yaml diffs are
    foundation-only).

PHASE EXIT DEMO (on the box or against the fake)
Send a chat from the UI with a manually picked model → streamed reply with
model label → Debug view shows the full turn waterfall → kill the fake
mid-stream → UI shows the error/connection-lost state, stream ended with
`error`. All CI jobs green.

FINAL STEPS
python -m pytest -q and tsc --noEmit on merged main. Delete merged worktrees
(git worktree remove). Report: merge order executed, drift found + fixes,
demo checklist results.
```

## Prompt: Phase 1 VERIFICATION

```
ROLE: Verification agent. Read-only audit; you change nothing.

For each Phase 1 branch (p1/llm-client, p1/debug-trace, p1/chat-sse,
p1/web-shell, p1/ci-harness):
1. git diff main...<branch> --name-only → compare against the branch's FILE
   SCOPE in docs/PHASE_PROMPTS.md (and .cursor/agent-contracts/ if present).
   Flag every out-of-scope file touched, including shared files
   (pyproject.toml, config.yaml, app/config.py, app/types.py, app/main.py)
   modified by a non-owner.
2. Check out the branch in a scratch worktree; run python -m pytest -q from
   repo root and tsc --noEmit (if src/ exists). Record pass/fail + new
   failure names.
3. Contract spot-checks: streams terminate with done|error in tests; every
   new pipeline stage calls app.debug.span; zero hardcoded model names
   (grep for roster aliases inside app/ and src/ excluding config.yaml).
4. Output a per-branch verdict table: SCOPE OK / VIOLATIONS, TESTS PASS/FAIL,
   contract notes. Recommend merge or bounce per branch. Do not merge, do not
   fix, do not commit.
```

---

# Phase 2 — Routing + models control

GPU inventory, swapgen, Settings UI, 3-layer router with grammar-constrained
classifier, router eval, auto-title/summaries (PLAN.md §5 Phase 2). Exit:
correct model auto-selected ≥90% on eval set; GPU reassignment without
restart.

## Wave 1 (sequential — one agent extends shared config)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| config-schema | `p2/config-schema` | `../AI-Mega-App-p2-config` | `config.yaml`, `app/config.py`, `tests/test_config.py` | Phase 1 merged |

## Wave 2 (parallel)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| gpu-swapgen | `p2/gpu-swapgen` | `../AI-Mega-App-p2-gpu` | `app/gpu/**`, `tests/test_swapgen.py`, `tests/test_gpu_inventory.py` | config-schema |
| router-classifier | `p2/router-classifier` | `../AI-Mega-App-p2-router` | `app/router/**`, `tests/test_router.py` | config-schema |
| router-eval | `p2/router-eval` | `../AI-Mega-App-p2-eval` | `eval/**`, `scripts/eval_router.py` | config-schema |
| settings-api | `p2/settings-api` | `../AI-Mega-App-p2-settings` | `app/settings/**`, `app/main.py` (OWNER), `app/chat/orchestrator.py` (OWNER: router seam only), `tests/test_settings_api.py` | config-schema |
| background-utility | `p2/background-utility` | `../AI-Mega-App-p2-utility` | `app/background/**`, `tests/test_background.py` | config-schema |
| settings-ui | `p2/settings-ui` | `../AI-Mega-App-p2-ui` | `src/views/settings.ts`, `src/views/chat.ts`, `src/api.ts` (OWNER this wave), `web/css/settings.css` | config-schema |

Shared-file owners this wave: `config.yaml`/`app/config.py` frozen after wave
1; `app/main.py` + orchestrator router-seam → settings-api; `src/api.ts` →
settings-ui.

## Prompt: `p2/config-schema`

```
CONTEXT
AI-Mega-App, Phase 2 wave 1. Phase 1 shipped: config loader, SQLite, LLM
client + fake, chat SSE with debug spans, claude.ai-shell UI, CI. Six
parallel agents build routing/GPU/settings next — you extend the shared
config schema they all read. Measured hardware facts: docs/
phase0-measurements.md. Source of truth: PLAN.md §4.1, §4.3, §4.15;
docs/FEATURES.md §routing, §models.

GOAL
Extend config.yaml + app/config.py (pydantic) with, exactly:
  models[*]: add {file: /models/....gguf, quant, mmproj: path|null,
    resident: bool, ttl_s: int|null, extra_flags: [str]}
  gpu: {rewarm_default_after_min: 10}
  routing:
    rules: [{keywords: [str] (2+ words, word-boundary), intent: str}]
    attachments: {image: vision, code_file: coding}   # forced intents
    intents: map class+effort -> model alias, e.g.
      general: chat-default · coding: coder · reasoning: reasoner ·
      vision: vision · tool: chat-default ·
      coding_light: utility-or-coder per PLAN §4.1 labels
    classifier: {model: classifier, timeout_s: 2.0,
                 confidence_threshold: 0.5, fallback_model: chat-default}
  background: {title_model: utility, summary_model: utility,
               summary_every_n_turns: 6}
Update tests/test_config.py for the new schema; keep old keys compatible.

NON-GOALS
No router logic, no swapgen, no UI, no main.py changes.

FILE SCOPE (hard boundary)
  config.yaml  app/config.py  tests/test_config.py

INTERFACES
Schema exactly as above — six agents were promised these key names.

CONSTRAINTS
Plan Mode first; show the final YAML for approval. Files under 300 lines
(split config.py into a package only if forced — ask first). Aliases from
the PLAN.md §4.1 roster only.

ACCEPTANCE
pytest -q passes; load_config on the new config.yaml validates clean.

STOP CONDITION
Any other file → ask first.

FINAL STEPS
pytest -q; commit `feat(config): routing, gpu, background schema`. Report
the schema verbatim.
```

## Prompt: `p2/gpu-swapgen`

```
CONTEXT
AI-Mega-App, Phase 2 wave 2. config.yaml now has full model entries (file,
gpu, resident, ttl_s, mmproj, extra_flags). llama-swap runs as systemd on
:8080; the hand-written llama-swap.yaml from Phase 0 is about to be replaced
by generated config. Exact llama-server flag spellings + llama-swap reload
endpoint are RECORDED in docs/phase0-measurements.md — use those, never
guess. Source of truth: PLAN.md §4.1 (GPU delegation + generated YAML
example), §6 guardrail; docs/FEATURES.md §gpu.

GOAL
1. app/gpu/inventory.py — async run of `nvidia-smi --query-gpu=index,name,
   memory.total,memory.free --format=csv` → list[GPUInfo]; graceful "no
   nvidia-smi" result for dev machines.
2. app/gpu/swapgen.py — DETERMINISTIC generator (hand-written code, no AI,
   no templating beyond f-strings): generate(config: Config) -> str renders
   llama-swap.yaml exactly in the PLAN.md §4.1 shape: macros.llama, one
   models entry per config model (CPU → --device none -ngl 0; GPU → device
   flag per phase-0 spelling; --embeddings for class embed; --mmproj for
   vision; --jinja globally; ctx from config), groups: resident
   (swap:false, exclusive:false, members = resident models) and gpu0-main
   (swap:true, the 3090 slot). Header comment: "# generated — do not
   hand-edit".
3. app/gpu/api.py — APIRouter:
     GET  /api/gpu/inventory
     GET  /api/gpu/swap-config            (current generated YAML, text)
     POST /api/gpu/apply                  (write file to configured path +
                                           call llama-swap reload endpoint;
                                           returns llama-swap's response)
   Each handler wrapped in a debug span (stage: gpu_inventory, swapgen).
4. Rewarm policy (PLAN.md §4.1 default-model paragraph): app/gpu/rewarm.py —
   ~20-line asyncio task: if the 3090 slot has served a non-default model
   and been idle > config.gpu.rewarm_default_after_min, request 1 token from
   defaults.chat_model. Started from an `async def start(app)` hook that
   settings-api wires into main.py.
5. tests: golden-file test for swapgen output from the checked-in
   config.yaml; inventory parser test on canned CSV; apply test against a
   fake reload endpoint.

NON-GOALS
No Settings UI, no main.py edits (settings-api owns wiring), no editing the
live /opt/llama-swap files from tests.

FILE SCOPE (hard boundary)
  app/gpu/__init__.py  app/gpu/inventory.py  app/gpu/swapgen.py
  app/gpu/api.py  app/gpu/rewarm.py  tests/test_swapgen.py
  tests/test_gpu_inventory.py
READ-ONLY: app/config.py, config.yaml, app/debug/**, app/llm_client.py.

INTERFACES
`router = APIRouter()` in api.py; `generate(config) -> str`;
`start_rewarm(app) -> None`. Output path + reload URL from config (add
nothing to config — the keys exist; if one is missing, STOP and ask).

CONSTRAINTS
Plan Mode first. Files under 300 lines. Generated YAML is byte-stable for a
given config (sorted, no timestamps) so the golden test is meaningful. Flag
spellings copied from docs/phase0-measurements.md with a comment citing it.

ACCEPTANCE
pytest -q passes; golden YAML matches PLAN.md §4.1 structure. Wiring proof
deferred to integrator demo (GPU reassignment without backend restart), but
the apply endpoint must be fully implemented and tested against the fake.

STOP CONDITION
Missing config key, unclear flag spelling, or any file outside scope → stop
and ask.

FINAL STEPS
pytest -q; commit `feat(gpu): inventory, swapgen, apply+reload, rewarm`.
Report generated-YAML sample and endpoints.
```

## Prompt: `p2/router-classifier`

```
CONTEXT
AI-Mega-App, Phase 2 wave 2. The old build's classifier failed as free-text
JSON from a 4.4k-token prompt; the fix is llama.cpp grammar-constrained
output (response_format json_schema) making malformed JSON structurally
impossible, with a ~600-token prompt (PLAN.md §4.3). config.routing now
carries rules, attachment forcing, intents table, classifier settings.
LLMClient.chat accepts response_format. RouteResult exists in app/types.py.
Source of truth: PLAN.md §4.3; docs/FEATURES.md §router.

GOAL
app/router/ package, three strictly ordered layers, every decision traced:
1. app/router/rules.py — layer 2: attachment forcing (image→vision,
   code_file→coding) then keyword rules from config (word-boundary regex,
   2+ word phrases). Returns intent | None. Pure, synchronous, no model.
2. app/router/classifier.py — layer 3: calls the classifier model via
   LLMClient with response_format json_schema enforcing EXACTLY
   {"class": "general|coding|tool|reasoning|vision",
    "effort": "light|heavy", "needs_tools": [string], "confidence": number}
   Prompt ≤ ~600 tokens + few-shots, in a module-level constant. The
   classifier NEVER sees or names model aliases. Timeout
   config.routing.classifier.timeout_s → fallback. Confidence below
   threshold → fallback, flagged.
3. app/router/router.py — async route(chat, text, attachments) ->
   RouteResult: layer 1 manual override (chat.model_override) → layer 2
   rules → layer 3 classifier → fallback_model. Resolves class+effort to a
   model alias via config.routing.intents. Emits one debug span (stage:
   route) with source, intent, confidence, latency_ms, and which layer won.
4. tests/test_router.py — override wins; rule wins over classifier; canned
   classifier JSON routes correctly; timeout → fallback flagged; low
   confidence → fallback; attachment forcing.

NON-GOALS
No orchestrator edits (settings-api wires route() into the Phase 1 seam),
no eval harness (parallel agent), no UI, no prompt-tuning loops.

FILE SCOPE (hard boundary)
  app/router/__init__.py  app/router/rules.py  app/router/classifier.py
  app/router/router.py  tests/test_router.py
READ-ONLY: app/config.py, app/types.py, app/llm_client.py, app/debug/**.

INTERFACES
`async def route(chat, text: str, attachments: list) -> RouteResult` exported
from app/router/__init__.py — settings-api and eval both import exactly this.
RouteResult.source ∈ {"override","rule","classifier","fallback"}.

CONSTRAINTS
Plan Mode first; show the JSON schema + prompt skeleton for approval. Files
under 300 lines. Model aliases appear only via config lookups. Classifier
failures degrade to fallback — routing must never raise into the chat path.

ACCEPTANCE
pytest -q passes with all listed cases against the fake llama-swap
(scriptable canned classifier responses).

STOP CONDITION
If RouteResult needs a new field, stop and ask (types.py is frozen).

FINAL STEPS
pytest -q; commit `feat(router): 3-layer routing, grammar classifier`.
Report the route() signature, JSON schema, and prompt token count.
```

## Prompt: `p2/router-eval`

```
CONTEXT
AI-Mega-App, Phase 2 wave 2. A parallel agent ships app/router with
`async def route(chat, text, attachments) -> RouteResult` (source, intent,
model, confidence). The old repo's one good classifier idea was a labeled
eval CSV + scoring script — recreate it properly (PLAN.md §4.10). Phase exit
gate: ≥90% correct on this set. Source of truth: PLAN.md §4.3, §4.10, §5
(Phase 2 exit); docs/FEATURES.md §router-eval.

GOAL
1. eval/router_eval.csv — ≥120 labeled rows: prompt, expected_class,
   expected_effort, notes. Cover: plain chat, light+heavy coding, tool-ish
   requests (search/fetch phrasing), reasoning, vision-with-attachment
   marker column, adversarial near-misses (code words in casual chat).
2. scripts/eval_router.py — runs each row through app.router.route with a
   real or fake classifier backend (flag --base-url; default = fake canned
   mode for CI, live llama-swap for the box), prints per-class
   precision/recall, overall accuracy, confusion matrix, and the failing
   rows; exits nonzero under --min-accuracy N.
3. eval/README row in the CSV header comment: how to run on the box.

NON-GOALS
No changes to app/router/**, no CI workflow edits (note the suggested job
for the integrator), no prompt edits.

FILE SCOPE (hard boundary)
  eval/router_eval.csv  scripts/eval_router.py

INTERFACES
Consumes app.router.route exactly as declared; a --fake mode monkeypatches
the classifier call so the script also validates rules-layer behavior
without a GPU.

CONSTRAINTS
Plan Mode first. Files under 300 lines (CSV exempt). stdlib + existing deps
only.

ACCEPTANCE
python scripts/eval_router.py --fake runs green end-to-end once the router
branch is merged (until then, prove with a stub import guard + unit-parse of
the CSV: every row well-formed, classes valid).

STOP CONDITION
Any file outside scope → ask.

FINAL STEPS
pytest -q (repo suite must stay green); commit
`feat(eval): router eval set + scorer`. Report row counts per class and the
command line for the box run.
```

## Prompt: `p2/settings-api`

```
CONTEXT
AI-Mega-App, Phase 2 wave 2. You own the shared wiring files this wave:
app/main.py and the model-resolution seam in app/chat/orchestrator.py left
in Phase 1. Parallel agents ship: app/router (async route(chat, text,
attachments) -> RouteResult), app/gpu (router + start_rewarm(app)),
app/background (start hooks + title/summary jobs), and a Settings UI
consuming your endpoints. Settings persistence = settings.local.yaml overlay
(PLAN.md §3.1) + settings_overlay table for UI state. Source of truth:
PLAN.md §3.1, §4.1, §4.3; docs/FEATURES.md §settings.

GOAL
1. app/settings/store.py — read/write settings.local.yaml overlay: typed
   get/set for model entries, GPU assignments, routing rule edits, toggles.
   Writes are atomic (tmp+rename) and re-validated through app.config before
   commit; invalid writes rejected with detail.
2. app/settings/api.py — APIRouter:
     GET  /api/settings                → effective config (defaults+overlay,
                                         secrets excluded)
     PUT  /api/settings/models/{name}  → update model entry (gpu, resident,
                                         ttl_s, enabled)
     PUT  /api/settings/routing        → rules + intents table
     GET  /api/models                  → roster with class + resident flags
                                         (UI model picker feeds from this)
3. Wire the wave into app/main.py: include settings, gpu routers; call
   background start hook + gpu.start_rewarm on startup.
4. app/chat/orchestrator.py — replace the Phase 1 seam:
   model = explicit ?? chat.model_override ?? (await route(...)).model; store
   RouteResult fields on the turn's trace span; pass intent to the done
   event payload as {"route": {...}}.
5. tests/test_settings_api.py — overlay round-trip, invalid write rejected,
   effective-config merge, orchestrator-uses-router test against fakes.

NON-GOALS
No router internals, no swapgen internals, no frontend, no background job
logic (you only call their start hooks).

FILE SCOPE (hard boundary)
  app/settings/__init__.py  app/settings/store.py  app/settings/api.py
  app/main.py  app/chat/orchestrator.py  tests/test_settings_api.py
READ-ONLY: app/config.py, config.yaml, app/router/**, app/gpu/**,
app/background/**.

INTERFACES
Endpoints exactly as above (settings-ui codes against them verbatim).
Orchestrator emits the same SSE vocabulary as Phase 1 plus route info in
`done`.

CONSTRAINTS
Plan Mode first. Files under 300 lines. settings.local.yaml is the only
file you write at runtime; config.yaml stays pristine. Every new stage emits
a debug span.

ACCEPTANCE
pytest -q passes. Wiring proof: TestClient test drives PUT model gpu
assignment → GET /api/gpu/swap-config reflects it; chat turn with no
override shows route source "rule"/"classifier" in the done payload and a
route span in the trace.

STOP CONDITION
Interface drift with router/gpu/background as shipped → stop, report to
integrator; do not adapt their files.

FINAL STEPS
pytest -q; commit `feat(settings): overlay store, API, router+gpu wiring`.
Report endpoints, wiring performed, drift observed.
```

## Prompt: `p2/background-utility`

```
CONTEXT
AI-Mega-App, Phase 2 wave 2. The utility model (Qwen3-8B, resident on the
3070) handles titles/summaries as background tasks; failures never block
chat (PLAN.md §4.15). config.background has title_model, summary_model,
summary_every_n_turns. LLMClient + debug spans exist. settings-api wires
your start hook into main.py. Source of truth: PLAN.md §4.1, §4.15;
docs/FEATURES.md §background.

GOAL
1. app/background/queue.py — tiny asyncio task queue: submit(coro_factory),
   sequential worker, errors logged + span-recorded, never raised to
   callers. start(app) / stop(app) hooks.
2. app/background/titles.py — after a chat's first exchange completes,
   generate a ≤6-word title via utility model; write to chats.title; emit
   SSE `title` event on the chat stream bus if one is open, else UI picks it
   up on next list fetch. Span stage: title.
3. app/background/summaries.py — rolling summary per chat every
   summary_every_n_turns; stored in a new column? NO — store in
   settings_overlay-style table is wrong too; use messages? STOP — a
   `chat_summaries(chat_id PK, summary, updated_at)` table is required:
   request it via the STOP CONDITION unless it already exists in schema.sql.
4. Hook: orchestrator calls background.on_turn_complete(chat_id) — export
   that function; settings-api wires the call.
5. tests/test_background.py against fake llama-swap.

NON-GOALS
No compaction (Phase 3), no memory reviewer (Phase 5), no orchestrator or
main.py edits (settings-api owns them), no frontend.

FILE SCOPE (hard boundary)
  app/background/__init__.py  app/background/queue.py
  app/background/titles.py  app/background/summaries.py
  tests/test_background.py
READ-ONLY: everything else.

INTERFACES
`start(app)`, `stop(app)`, `on_turn_complete(chat_id)` exported from
app/background/__init__.py. Model names from config.background only.

CONSTRAINTS
Plan Mode first. Files under 300 lines. Every job wrapped in a span (stages:
title, summary). A failed job retries once, then records failure and moves
on.

ACCEPTANCE
pytest -q passes: title generated after first exchange; summary at N turns;
utility-model failure leaves chat unaffected.

STOP CONDITION
The chat_summaries table needs adding to app/schema.sql (owned by
foundation/frozen): stop and ask for owner sign-off with the exact DDL
before writing any code that needs it.

FINAL STEPS
pytest -q; commit `feat(background): utility queue, titles, summaries`.
Report exported hooks and the DDL that was approved.
```

## Prompt: `p2/settings-ui`

```
CONTEXT
AI-Mega-App, Phase 2 wave 2. Frontend shell exists (hash router, store,
api.ts SSE client, chat + debug views), tsc-only TS, claude.ai-parity
layout. Backend agents ship this wave (code against these verbatim):
  GET /api/settings · PUT /api/settings/models/{name} ·
  PUT /api/settings/routing · GET /api/models ·
  GET /api/gpu/inventory · GET /api/gpu/swap-config · POST /api/gpu/apply
  chat `done` events now include {"route": {source, intent, model,
  confidence}}; a `title` SSE event may arrive.
You OWN src/api.ts this wave. Source of truth: PLAN.md §4.1, §4.2, §4.3;
docs/FEATURES.md §settings-ui.

GOAL
1. src/views/settings.ts + web/css/settings.css — Settings area: Models
   table (name, class, GPU assignment dropdown fed by /api/gpu/inventory,
   resident toggle, ttl); Routing tab (keyword rules editor, intents map);
   Apply button → PUT then POST /api/gpu/apply, surfacing llama-swap's
   reload result. Split into submodules if nearing 300 lines.
2. src/views/chat.ts — composer model picker now fed from GET /api/models
   (aliases + class grouping, "Auto (router)" default = clear override);
   per-message model label shows route source on hover (e.g. "coder · via
   classifier 0.92"); live title update on `title` event.
3. src/api.ts — typed client additions for the endpoints above.

NON-GOALS
No backend edits, no debug view changes, no Projects/artifacts UI.

FILE SCOPE (hard boundary)
  src/views/settings.ts  src/views/chat.ts  src/api.ts  web/css/settings.css
READ-ONLY: all other src/**, web/**, everything backend.

INTERFACES
Endpoint list in CONTEXT, verbatim. Views keep the mount/unmount contract.

CONSTRAINTS
Plan Mode first; show the Settings layout sketch (text) for approval. Files
under 300 lines. All dynamic content DOMPurify'd. Model names render from
API data only — zero literals.

ACCEPTANCE
tsc --noEmit clean. Wiring proof: with scripts/dev.sh (fake backend),
reassign a model's GPU in Settings → Apply → the swap-config view shows the
change; picking a model in the composer sets the override and the next
message is labeled with it.

STOP CONDITION
Missing/mismatched endpoint → stop and report; invent nothing.

FINAL STEPS
tsc --noEmit; python -m pytest -q (must stay green). Commit
`feat(web): settings view, model picker, route labels`. Report views and
any endpoint drift.
```

## Prompt: Phase 2 INTEGRATOR

```
ROLE: Integrator for Phase 2.

MERGE ORDER
1. p2/config-schema (wave 1 — already on main before wave 2 started)
2. p2/router-classifier   3. p2/gpu-swapgen   4. p2/background-utility
5. p2/router-eval         6. p2/settings-api  7. p2/settings-ui

SEMANTIC-CONFLICT CHECKLIST
[ ] settings-api's orchestrator seam imports route() with the exact shipped
    signature; RouteResult fields it forwards exist.
[ ] settings-api wires gpu.start_rewarm + background.start with the shipped
    hook names.
[ ] background's chat_summaries DDL landed in schema.sql via approved
    foundation change (one commit, not two competing ones).
[ ] eval script imports app.router.route successfully post-merge; run
    `python scripts/eval_router.py --fake`.
[ ] settings-ui endpoint paths/payloads match settings-api + gpu api
    (grep both sides; compare JSON keys literally).
[ ] `done` event route payload shape matches what chat.ts parses.
[ ] Golden SSE transcript updated deliberately (route info in done) — a
    diff here must be an intentional contract change, noted in the commit.
[ ] No non-owner touched config.yaml/app/config.py/app/main.py/src/api.ts.

PHASE EXIT DEMO (on the box)
Regenerate llama-swap.yaml from Settings, apply, llama-swap reloads without
backend restart. Router eval on live classifier ≥90%
(scripts/eval_router.py --min-accuracy 90). New chat auto-routes; override
beats router; titles appear. All spans visible in Debug.

FINAL STEPS
pytest -q + tsc --noEmit on main; remove merged worktrees. Report merges,
drift fixed, eval score, demo results.
```

## Prompt: Phase 2 VERIFICATION

```
ROLE: Verification agent, Phase 2. Read-only.

For each branch (p2/config-schema, p2/gpu-swapgen, p2/router-classifier,
p2/router-eval, p2/settings-api, p2/background-utility, p2/settings-ui):
1. git diff main...<branch> --name-only vs the FILE SCOPE tables above; flag
   every violation, especially non-owner edits to config.yaml,
   app/config.py, app/main.py, app/chat/orchestrator.py, src/api.ts,
   app/schema.sql.
2. Scratch-worktree checkout; python -m pytest -q; tsc --noEmit where
   applicable. Record new failures.
3. Contract checks: classifier prompt contains zero model aliases; swapgen
   output is deterministic (run generate twice, diff); every new stage has
   a span; grep app/ src/ for hardcoded roster aliases outside config.
4. Verdict table per branch (SCOPE / TESTS / CONTRACTS) + merge-or-bounce
   recommendation. No fixes, no merges, no commits.
```

---

# Phase 3 — Substance

Tools framework, search, Needle assist, attachments, Projects, RAG, memory,
Tier-1 artifacts, compaction (PLAN.md §5 Phase 3). Exit: claude.ai-parity
daily driver.

## Wave 1 (sequential — one agent: tool loop + shared seams)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| tools-core | `p3/tools-core` | `../AI-Mega-App-p3-core` | `app/tools/__init__.py`, `app/tools/base.py`, `app/chat/orchestrator.py` (OWNER), `config.yaml` + `app/config.py` (OWNER: tools/rag/memory/attachments keys), `app/schema.sql` + migration (OWNER), `tests/test_tool_loop.py` | Phase 2 merged |

## Wave 2 (parallel — backend)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| search | `p3/search` | `../AI-Mega-App-p3-search` | `app/search/**`, `app/tools/web_search.py`, `app/tools/fetch_url.py`, `tests/test_search.py` | tools-core |
| needle | `p3/needle` | `../AI-Mega-App-p3-needle` | `app/tools/needle_dispatch.py`, `tests/test_needle.py` | tools-core |
| attachments | `p3/attachments` | `../AI-Mega-App-p3-attach` | `app/attachments/**`, `tests/test_attachments.py` | tools-core |
| rag | `p3/rag` | `../AI-Mega-App-p3-rag` | `app/rag/**`, `tests/test_rag.py` | tools-core |
| memory | `p3/memory` | `../AI-Mega-App-p3-memory` | `app/memory/**`, `app/tools/memory_save.py`, `app/tools/memory_search.py`, `app/chat/compaction.py`, `tests/test_memory.py`, `tests/test_compaction.py` | tools-core |
| projects | `p3/projects` | `../AI-Mega-App-p3-projects` | `app/projects/**`, `app/tools/file_ops.py`, `tests/test_projects.py` | tools-core |

## Wave 3 (parallel — frontend)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| projects-ui | `p3/projects-ui` | `../AI-Mega-App-p3-pui` | `src/views/projects.ts`, `src/views/project.ts`, `web/css/projects.css` | wave 2 merged |
| artifacts | `p3/artifacts` | `../AI-Mega-App-p3-artifacts` | `src/artifacts/**`, `web/css/artifacts.css`, `web/workers/**` | wave 2 merged |
| chat-ux | `p3/chat-ux` | `../AI-Mega-App-p3-ux` | `src/views/chat.ts`, `src/api.ts` (OWNER this wave), `web/css/app.css` | wave 2 merged |

## Wave 4 (single agent)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| wiring | `p3/wiring` | `../AI-Mega-App-p3-wiring` | `app/main.py` (OWNER), `src/app.ts` + `src/router.ts` (OWNER), `tests/test_phase3_wiring.py` | waves 2–3 merged |

## Prompt: `p3/tools-core`

```
CONTEXT
AI-Mega-App, Phase 3 wave 1. Phase 2 shipped routing, settings, swapgen,
titles. Chat orchestrator streams via LLMClient with debug spans; the old
build's tool-loop delta-merge PATTERN was correct — reuse the pattern, not
code. Six parallel agents implement tools/features against what you define
here. Source of truth: PLAN.md §4.7, §4.8, §4.9, §6; docs/FEATURES.md
§tools.

GOAL
1. app/tools/base.py — the tool contract:
     class Tool(Protocol):
         name: str; description: str
         schema: dict          # JSON Schema for arguments
         enabled: bool         # from config.tools.<name>.enabled
         async def execute(self, args: dict, ctx: ToolContext) -> ToolResult
     ToolContext carries chat_id, project_id, trace_id, config.
     ToolResult(content: str, is_error: bool, data: dict|None).
2. app/tools/__init__.py — registry: auto-discovers Tool modules in
   app/tools/, filters by enabled flag, renders OpenAI `tools` schema list,
   dispatch(name, args, ctx) with span (stage: tool, fields: name, latency,
   is_error).
3. app/chat/orchestrator.py — tool loop: accumulate tool_call deltas →
   dispatch → append tool result message → continue, max
   config.tools.max_iterations. New SSE events:
     event: tool_start   data: {"name", "args_preview"}
     event: tool_result  data: {"name", "content_preview", "is_error"}
   Needle seam: when the resolved model's config has tool_call: weak AND
   config.tools.needle_assist is true, call
   app.tools.needle_dispatch.emit_call(...) for the call-emission step (the
   module ships in wave 2 — import lazily, degrade to native path if
   absent). RAG seam: before the LLM call, `context_blocks =
   await gather_context(chat, text)` — a hook list features append to
   (memory + rag register providers). Compaction seam: call
   app.chat.compaction.maybe_compact(chat) if the module exists.
4. Config schema additions (you OWN config this wave):
     tools: {max_iterations: 6, needle_assist: true,
             web_search: {enabled: true}, fetch_url: {enabled: true},
             file_ops: {enabled: true}, memory_save: {enabled: true},
             memory_search: {enabled: true}}
     search: {provider_chain: [ddg, tavily]}
     rag: {chunk_tokens: 512, overlap_pct: 20, top_k: 6}
     memory: {scopes: [user, project, global]}
     attachments: {max_inline_tokens: 4000}
     compaction: {threshold_tokens: 24000, keep_recent_turns: 8}
5. app/schema.sql migration (you OWN it this wave): projects(id, name,
   path, created_at), attachments(id, chat_id, filename, mime, path,
   extracted_chars), memories(id, scope, project_id, content, created_at,
   embedding BLOB NULL), chunks + vec/FTS5 virtual tables for rag,
   chat_summaries if not present.
6. tests/test_tool_loop.py — echo-tool fixture: loop executes, respects
   max_iterations, tool error surfaces as tool_result is_error (stream
   still ends in done), disabled tool absent from schema list.

NON-GOALS
No concrete tools (wave 2), no rag/memory/attachments logic, no frontend,
no main.py edits (wave 4 wiring owns it).

FILE SCOPE (hard boundary)
  app/tools/__init__.py  app/tools/base.py  app/chat/orchestrator.py
  config.yaml  app/config.py  app/schema.sql  tests/test_tool_loop.py

INTERFACES
Everything in GOAL 1–5 verbatim — six agents build against it.

CONSTRAINTS
Plan Mode first; present Tool protocol + config keys + DDL for approval
before code. Files under 300 lines. Seams degrade gracefully when the
providing module is absent (lazy import + feature flag), so wave-2 branches
merge in any order.

ACCEPTANCE
pytest -q passes incl. the golden SSE transcript updated for tool events
(intentional contract change, called out in the commit).

STOP CONDITION
Anything beyond scope → ask.

FINAL STEPS
pytest -q; commit `feat(tools): registry, tool loop, phase-3 seams+schema`.
Report every interface verbatim — it is the wave-2/3 contract.
```

## Prompt: `p3/search`

```
CONTEXT
AI-Mega-App, Phase 3 wave 2. Tool contract on main: app/tools/base.py
(Tool protocol: name, description, schema, enabled, execute(args, ctx) ->
ToolResult); registry auto-discovers modules in app/tools/. DDG throttling
is real (old build evidence) — provider chain DDG → Tavily on
rate-limit/empty (PLAN.md §4.13). config.search.provider_chain exists;
TAVILY_API_KEY in .env. Source of truth: PLAN.md §4.13, §4.7;
docs/FEATURES.md §search.

GOAL
1. app/search/base.py — SearchProvider protocol: async search(query, n) ->
   list[SearchHit(title, url, snippet)]; typed RateLimited/Empty errors.
2. app/search/ddg.py (ddgs lib), app/search/tavily.py (httpx), app/search/
   chain.py — walks config.search.provider_chain, falls through on
   RateLimited/Empty, records provider used. Span stage: search.
3. app/tools/web_search.py — Tool wrapping the chain; result content
   includes numbered sources (title+url) for UI citations; ToolResult.data
   carries {provider, hits}.
4. app/tools/fetch_url.py — Tool: httpx GET with size/time limits, html →
   text (readability-lite: strip script/style/nav), truncate to a config
   cap. Span stage: fetch.
5. tests/test_search.py — chain fallback on rate limit, empty → next
   provider, tools respond via registry dispatch, all network mocked.

NON-GOALS
No deep-research pipeline (Future), no browser tool (Phase 5), no registry
or orchestrator edits, no config key additions.

FILE SCOPE (hard boundary)
  app/search/**  app/tools/web_search.py  app/tools/fetch_url.py
  tests/test_search.py
READ-ONLY: app/tools/base.py, app/tools/__init__.py, config.yaml.

INTERFACES
Tool protocol verbatim; tool names exactly `web_search`, `fetch_url`
(config.tools keys already exist for them).

CONSTRAINTS
Plan Mode first. Files under 300 lines. `ddgs` is an expected new
dependency — confirm before adding to your report; pyproject.toml is
frozen, so list it for the integrator instead of editing. Keys read from
config/.env only.

ACCEPTANCE
pytest -q passes; registry dispatch test proves both tools reachable
end-to-end through the tool loop (use the loop fixture from
tests/test_tool_loop.py).

STOP CONDITION
pyproject.toml dependency additions → report, do not edit. Anything else
out of scope → ask.

FINAL STEPS
pytest -q; commit `feat(search): ddg→tavily chain, web_search+fetch_url`.
Report provider chain behavior + the dependency request.
```

## Prompt: `p3/needle`

```
CONTEXT
AI-Mega-App, Phase 3 wave 2. Cactus Needle (26M, CPU resident, alias
`needle`) emits ONE JSON tool call per inference — dispatcher, never
planner; no chaining, no reasoning (PLAN.md §4.7). The orchestrator already
has the seam: for models tagged tool_call: weak it calls
app.tools.needle_dispatch.emit_call. Source of truth: PLAN.md §4.7 (Needle
assist + dispatcher-not-planner), §4.1 roster; docs/FEATURES.md §needle.

GOAL
1. app/tools/needle_dispatch.py — NOT a registry Tool; the assist module:
     async def emit_call(text: str, tool_schemas: list[dict],
                         client: LLMClient, cfg) -> ToolCallRequest | None
   Sends query + tool schemas to model alias from
   config.routing/models (class needle) with response_format json_schema
   enforcing {name: str, arguments: object}; validates name against the
   schema list and arguments against that tool's JSON Schema; returns None
   on validation failure or timeout (caller falls back to the native path).
   Span stage: needle, fields: chosen_tool, valid, latency_ms — the debug
   panel marks Needle-assisted turns (who decided vs. who emitted).
2. tests/test_needle.py — valid call passes validation; unknown tool name →
   None; malformed args → None; timeout → None; canned responses via fake
   llama-swap.

NON-GOALS
No orchestrator edits (seam exists), no multi-step planning, no fine-tuning
tooling (post-Phase-3 per PLAN.md §4.7), no registry changes.

FILE SCOPE (hard boundary)
  app/tools/needle_dispatch.py  tests/test_needle.py
READ-ONLY: everything else.

INTERFACES
emit_call signature verbatim (orchestrator already imports it lazily).
Model alias resolved from config models with class `needle` — no literal.

CONSTRAINTS
Plan Mode first. File under 300 lines. Every failure path returns None —
Needle assist degrades, never breaks a turn.

ACCEPTANCE
pytest -q passes all listed cases; an integration test through the tool
loop with a weak-tagged model shows the needle span present.

STOP CONDITION
Seam mismatch with orchestrator as merged → stop and report.

FINAL STEPS
pytest -q; commit `feat(needle): schema-validated dispatch assist`. Report
validation rules and fallback behavior.
```

## Prompt: `p3/attachments`

```
CONTEXT
AI-Mega-App, Phase 3 wave 2. Upload → type sniff → extractor registry;
small text inline to context, large → RAG-on-the-fly; images route to the
vision path (attachment forcing already in router). attachments table +
config.attachments.max_inline_tokens exist. Source of truth: PLAN.md §4.9;
docs/FEATURES.md §attachments.

GOAL
1. app/attachments/api.py — APIRouter: POST /api/chats/{id}/attachments
   (multipart) → {id, filename, kind, extracted_chars}; GET
   /api/attachments/{id} metadata. Files stored under data/attachments/.
2. app/attachments/extract.py — extractor registry, one module-level entry
   per type (Key Rule 6): text/code (passthrough), pdf (pymupdf), docx/
   xlsx/pptx (python-docx/openpyxl/python-pptx or markitdown — pick ONE
   approach, state it), image (no extraction; marks kind=image for the
   vision path). register(mime_prefixes, fn) so new extractors are one
   module each.
3. app/attachments/context.py — context provider registered on the
   orchestrator gather_context seam: extracted text ≤ max_inline_tokens
   goes inline as a tagged block; larger content is chunked+embedded via
   app.rag ingest-on-the-fly IF app.rag is importable, else truncated
   inline with a notice (graceful while rag merges in parallel).
4. Span stage: attachment_extract. tests/test_attachments.py with small
   fixture files per type.

NON-GOALS
No audio (Future), no rag internals, no orchestrator/main.py edits (seams
+ wave-4 wiring), no frontend.

FILE SCOPE (hard boundary)
  app/attachments/**  tests/test_attachments.py
READ-ONLY: everything else.

INTERFACES
Router exported as `router`; context provider registered via the tools-core
seam function. Endpoint shapes verbatim (chat-ux consumes them wave 3).

CONSTRAINTS
Plan Mode first. Files under 300 lines. New extraction deps (pymupdf etc.)
→ list for the integrator; pyproject.toml is frozen to you. Extractor
failures return a marked error string, never an exception into chat.

ACCEPTANCE
pytest -q passes: per-type extraction, oversize routes to rag-or-truncate,
upload endpoint round-trip via TestClient.

STOP CONDITION
Dependency additions or schema changes → report, do not edit owners' files.

FINAL STEPS
pytest -q; commit `feat(attachments): upload, extractor registry, context
provider`. Report extractor matrix + dependency requests.
```

## Prompt: `p3/rag`

```
CONTEXT
AI-Mega-App, Phase 3 wave 2. Hybrid retrieval: heading-aware chunking
(~512 tokens, 20% overlap) → embeddings via resident `embed` model
(LLMClient.embed) → sqlite-vec + FTS5 → reciprocal-rank fusion → top-k with
citations (PLAN.md §4.8). chunks + vec/FTS5 tables exist in schema;
config.rag has chunk_tokens/overlap_pct/top_k. sqlite-vec verdict is in
docs/phase0-measurements.md. Source of truth: PLAN.md §4.8, §3.1
(VectorStore escape hatch); docs/FEATURES.md §rag.

GOAL
1. app/rag/store.py — VectorStore interface (add(chunks), query(vector,
   text, k) -> list[Hit]) with the sqlite-vec+FTS5 implementation behind
   it (Qdrant can return later behind this interface).
2. app/rag/chunker.py — heading-aware markdown/text chunking, ~512 tokens,
   20% overlap; deterministic chunk ids (source path + hash).
3. app/rag/ingest.py — ingest_path(project_id, path) incremental on file
   mtime; ingest_text(source_id, text) for attachments-on-the-fly.
4. app/rag/retrieve.py — hybrid query: vector + BM25, reciprocal-rank
   fusion, top_k from config; returns hits with {source, heading, snippet,
   score}. Registers a context provider on the orchestrator seam injecting
   a tagged <rag-context> block with citation markers; provider active only
   for chats with a project_id. Span stages: rag_ingest, rag_retrieve.
5. tests/test_rag.py — chunker boundaries, incremental ingest skips
   unchanged files, hybrid retrieval returns fused ranking (seeded
   embeddings via fake embed endpoint), provider injects citations.

NON-GOALS
No project CRUD (parallel agent), no chat-history embedding (list it as a
follow-up), no UI, no schema edits.

FILE SCOPE (hard boundary)
  app/rag/**  tests/test_rag.py
READ-ONLY: everything else.

INTERFACES
VectorStore + ingest_path/ingest_text/retrieve signatures verbatim
(projects agent calls ingest_path; attachments calls ingest_text; chat-ux
renders citations from the done payload's context info).

CONSTRAINTS
Plan Mode first. Files under 300 lines. Embed model alias from config
(class embed) only. Ingest failures span-recorded, never fatal.

ACCEPTANCE
pytest -q passes; end-to-end test: ingest fixture docs → chat turn in a
project → injected block contains the expected citation.

STOP CONDITION
Schema/vec-table mismatch with what tools-core shipped → stop and report.

FINAL STEPS
pytest -q; commit `feat(rag): chunker, hybrid store, retrieval provider`.
Report interfaces + fusion behavior.
```

## Prompt: `p3/memory`

```
CONTEXT
AI-Mega-App, Phase 3 wave 2. hermes-style fact memory: discrete rows in
SQLite (FTS5 + optional embedding), three scopes (user prefs always
injected; project; global), injected as a tagged <memory-context> block;
everything visible/editable — nothing invisible (PLAN.md §4.8). Manual
tools only this phase (background reviewer is Phase 5). Compaction: when
context exceeds threshold, summarize oldest turns via utility model, keep
recent verbatim + summary block (PLAN.md §4.15). memories table +
config.memory/config.compaction exist. Source of truth: PLAN.md §4.8,
§4.15; docs/FEATURES.md §memory.

GOAL
1. app/memory/store.py — CRUD over memories with scope filters; FTS5
   search; optional embedding via embed model.
2. app/memory/api.py — APIRouter: GET/POST/PUT/DELETE /api/memories
   (scope, project_id filters) — Settings→Memory UI consumes it later.
3. app/memory/context.py — context provider on the orchestrator seam:
   user-scope always injected; project-scope for project chats; tagged
   <memory-context> block. Span stage: memory_inject.
4. app/tools/memory_save.py + app/tools/memory_search.py — registry Tools
   (names exactly memory_save, memory_search; config flags exist).
5. app/chat/compaction.py — maybe_compact(chat): token estimate over
   history; over threshold → summarize oldest turns via utility model,
   store summary, mark messages compacted; recent keep_recent_turns stay
   verbatim. Span stage: compaction. The orchestrator seam already calls
   maybe_compact.
6. tests/test_memory.py, tests/test_compaction.py.

NON-GOALS
No self-improvement reviewer (Phase 5), no UI, no schema edits, no
orchestrator/main.py edits.

FILE SCOPE (hard boundary)
  app/memory/**  app/tools/memory_save.py  app/tools/memory_search.py
  app/chat/compaction.py  tests/test_memory.py  tests/test_compaction.py
READ-ONLY: everything else.

INTERFACES
Tool protocol verbatim; `router` export in api.py; maybe_compact(chat) as
the seam expects. Utility model alias from config.background only.

CONSTRAINTS
Plan Mode first. Files under 300 lines. Compaction failure leaves history
untouched and records a span — never blocks the turn.

ACCEPTANCE
pytest -q passes: scope injection matrix, tools reachable through the tool
loop, compaction triggers at threshold and preserves recent turns
verbatim (golden-ish assertion on the rebuilt prompt).

STOP CONDITION
Schema mismatch or missing seam → stop and report.

FINAL STEPS
pytest -q; commit `feat(memory): fact store, tools, injection, compaction`.
Report scopes behavior + compaction thresholds.
```

## Prompt: `p3/projects`

```
CONTEXT
AI-Mega-App, Phase 3 wave 2. Projects are filesystem-first — the one part
of the old app that worked: projects/<id>/instructions.md + docs/; thread
storage lives in SQLite (chats.project_id). App opens to plain chat;
Projects is a nav item, not a gate (PLAN.md §4.5). projects table exists;
app.rag.ingest_path ships in parallel (lazy import). Source of truth:
PLAN.md §4.5, §4.4 item 2 (file_ops rationale); docs/FEATURES.md
§projects.

GOAL
1. app/projects/manager.py — create/list/get/delete projects: row +
   directory skeleton (instructions.md, docs/); read/write instructions;
   list files under docs/.
2. app/projects/api.py — APIRouter:
     GET/POST /api/projects · GET /api/projects/{id} (meta + files +
     chats) · PUT /api/projects/{id}/instructions ·
     POST /api/projects/{id}/files (upload into docs/, then
     rag.ingest_path if importable) · POST /api/projects/{id}/reindex
3. Instructions injection: context provider on the orchestrator seam —
   project chats get instructions.md as a tagged block. Span stage:
   project_context.
4. app/tools/file_ops.py — registry Tool (name exactly file_ops):
   read/list/search within the chat's project directory ONLY (path
   resolution rejects escapes); plain deterministic code, no AI (PLAN.md
   §4.4). Write support limited to project docs/ with a size cap.
5. tests/test_projects.py incl. path-escape rejection.

NON-GOALS
No UI (wave 3), no rag internals, no opencode delegation (Phase 4), no
main.py edits.

FILE SCOPE (hard boundary)
  app/projects/**  app/tools/file_ops.py  tests/test_projects.py
READ-ONLY: everything else.

INTERFACES
`router` export; endpoints verbatim (projects-ui codes against them).
Tool protocol verbatim.

CONSTRAINTS
Plan Mode first. Files under 300 lines. All paths resolved+checked against
the project root (realpath containment). English-only strings (fix any
stray non-English text before commit).

ACCEPTANCE
pytest -q passes: CRUD, instructions injection through gather_context,
file_ops reachable via tool loop, escape attempts rejected.

STOP CONDITION
Out-of-scope needs → report.

FINAL STEPS
pytest -q; commit `feat(projects): fs-first manager, api, file_ops tool`.
Report endpoints + injection behavior.
```

## Prompt: `p3/projects-ui`

```
CONTEXT
AI-Mega-App, Phase 3 wave 3. Backend merged: /api/projects CRUD,
instructions PUT, file upload+reindex; chats carry project_id. UI mirrors
claude.ai: project grid → workspace (instructions, files, project chats)
(PLAN.md §4.5). View contract: mount(el, store)/unmount; hash router;
src/api.ts typed client (chat-ux owns it this wave — consume existing
helpers or plain fetch; list needed additions in your report). Source of
truth: PLAN.md §4.2, §4.5; docs/FEATURES.md §projects-ui.

GOAL
1. src/views/projects.ts — grid: cards (name, updated, chat count), new
   project modal.
2. src/views/project.ts — workspace: instructions editor (PUT on save),
   files list + upload + reindex button, project chats list + "new chat in
   project".
3. web/css/projects.css — claude.ai-parity styling off theme.css
   variables.

NON-GOALS
No sidebar/nav registration (wave-4 wiring owns src/app.ts + router.ts),
no api.ts edits, no backend changes, no artifact panel.

FILE SCOPE (hard boundary)
  src/views/projects.ts  src/views/project.ts  web/css/projects.css
READ-ONLY: everything else.

INTERFACES
Endpoints from CONTEXT verbatim; export mount/unmount so wiring can
register routes #/projects and #/projects/{id}.

CONSTRAINTS
Plan Mode first. Files under 300 lines. DOMPurify all rendered content.

ACCEPTANCE
tsc --noEmit clean; manual proof with scripts/dev.sh: create project,
edit instructions, upload file, open a project chat (via direct hash URL
until wiring lands). Note results in the report.

STOP CONDITION
Endpoint drift or a needed api.ts helper → report, do not edit.

FINAL STEPS
tsc --noEmit; pytest -q stays green. Commit
`feat(web): project grid + workspace views`. Report + list api.ts/nav
needs for the wiring agent.
```

## Prompt: `p3/artifacts`

```
CONTEXT
AI-Mega-App, Phase 3 wave 3. Tier-1 artifacts are fully client-side:
right panel rendering markdown/HTML/SVG/JS in a sandboxed iframe
(sandbox="allow-scripts", no same-origin), Python via Pyodide in a web
worker — zero server risk, claude.ai parity (PLAN.md §4.6). The chat view
right-panel placeholder exists since Phase 1. Fenced code blocks arrive in
streamed markdown. Source of truth: PLAN.md §4.6; docs/FEATURES.md
§artifacts.

GOAL
1. src/artifacts/panel.ts — panel controller: mount/unmount into the right
   panel host element; artifact list per chat; tabs (preview/source).
2. src/artifacts/detect.ts — pure function: message content → artifact
   candidates (html, svg, javascript, python, markdown fences over a size
   threshold).
3. src/artifacts/sandbox.ts — iframe renderer: srcdoc,
   sandbox="allow-scripts", never allow-same-origin; console/error capture
   relayed via postMessage into the panel.
4. src/artifacts/pyodide.ts + web/workers/pyodide-worker.js — run Python
   in a worker: load Pyodide lazily (vendored/pinned URL decision → ask if
   vendoring ~10MB is unwanted; a config-served CDN path is acceptable —
   state the choice), stdout/stderr + result surfaced; 30s soft timeout
   terminates the worker.
5. web/css/artifacts.css.

NON-GOALS
No Tier-2 server exec (Phase 4 wires a Run-on-server button), no chat.ts
edits (chat-ux owns it; it will call your exported
`showArtifactsFor(message)` — export it), no backend.

FILE SCOPE (hard boundary)
  src/artifacts/**  web/workers/**  web/css/artifacts.css
READ-ONLY: everything else.

INTERFACES
Exports: initPanel(hostEl, store), detectArtifacts(text) ->
ArtifactCandidate[], showArtifactsFor(messageId). chat-ux integrates via
these three only.

CONSTRAINTS
Plan Mode first. Files under 300 lines. iframe sandbox flags exactly as
specified — allow-scripts only. All non-sandboxed rendering DOMPurify'd.

ACCEPTANCE
tsc --noEmit clean; manual proof: paste an HTML fence → renders in
sandboxed iframe; a Python fence runs in Pyodide and prints; an infinite
loop is terminated at timeout. Note results.

STOP CONDITION
Pyodide sourcing decision, or any file outside scope → ask.

FINAL STEPS
tsc --noEmit; pytest -q stays green. Commit
`feat(web): tier-1 artifact panel (iframe + pyodide)`. Report exports and
the Pyodide sourcing decision.
```

## Prompt: `p3/chat-ux`

```
CONTEXT
AI-Mega-App, Phase 3 wave 3. Backend merged: tool SSE events (tool_start,
tool_result), attachments upload endpoint, citations data in rag context,
memory API. Artifacts agent (parallel) exports initPanel/detectArtifacts/
showArtifactsFor from src/artifacts/panel.ts — integrate via those only.
You OWN src/api.ts this wave. Source of truth: PLAN.md §4.2, §4.6–§4.9;
docs/FEATURES.md §chat-ux.

GOAL
1. src/views/chat.ts — render tool_start/tool_result as collapsible tool
   chips in the stream; attachment button → POST
   /api/chats/{id}/attachments with progress; citation markers in
   assistant text become source-hover chips; artifact candidates get an
   "open in panel" affordance (detectArtifacts + showArtifactsFor);
   compaction notice row when a summary block is active.
2. src/api.ts — typed helpers: uploadAttachment, memories CRUD, projects
   CRUD (the list projects-ui reported), tool event types.
3. web/css/app.css — chip/citation/upload styles from theme variables.

NON-GOALS
No artifacts internals, no projects views, no nav/router registration
(wiring owns src/app.ts + src/router.ts), no backend edits.

FILE SCOPE (hard boundary)
  src/views/chat.ts  src/api.ts  web/css/app.css
READ-ONLY: everything else.

INTERFACES
SSE vocabulary: token, model_loading, tool_start, tool_result, title,
done, error — a stream ending otherwise renders connection-lost, still
the hard rule.

CONSTRAINTS
Plan Mode first. Files under 300 lines (extract chat submodules into
src/views/chat/ if needed — that path is yours by extension; declare it in
your plan). DOMPurify everywhere.

ACCEPTANCE
tsc --noEmit clean; manual proof with scripts/dev.sh: a fake-scripted
tool turn shows chips; upload attaches and the reply reflects it; an HTML
artifact opens in the panel.

STOP CONDITION
Artifact export drift or endpoint drift → report, do not adapt others'
files.

FINAL STEPS
tsc --noEmit; pytest -q stays green. Commit
`feat(web): tool chips, attachments, citations, artifact hooks`. Report
integrations + drift.
```

## Prompt: `p3/wiring`

```
CONTEXT
AI-Mega-App, Phase 3 wave 4 — the integration gate that killed the old
build ("built but not injected", Bug 1) exists because of you. Waves 2–3
merged: attachments/memory/projects routers, context providers, tools,
frontend views + artifact panel. You OWN app/main.py, src/app.ts,
src/router.ts. Source of truth: PLAN.md §5 (Phase 3 exit), §6 rule 005;
docs/FEATURES.md.

GOAL
1. app/main.py — include routers: attachments, memory, projects; confirm
   context providers register on startup (import side effects made
   explicit: call each feature's register() in one visible block).
2. src/app.ts + src/router.ts — register views: #/projects,
   #/projects/{id}, artifacts panel init in the app shell, sidebar nav
   items (Projects, Memory under Settings), api.ts helpers verified
   against every view's imports.
3. tests/test_phase3_wiring.py — one test per feature proving REACHABILITY
   end-to-end: upload → extraction span exists; project chat →
   instructions + rag + memory blocks present in the prompt sent to the
   fake; each tool listed in the /v1 request's tools array; memory CRUD
   via API.

NON-GOALS
No feature logic changes — wiring only. Behavior gaps get reported, not
patched.

FILE SCOPE (hard boundary)
  app/main.py  src/app.ts  src/router.ts  tests/test_phase3_wiring.py

CONSTRAINTS
Plan Mode first. Minimal diffs. Every register call in one commented
block so future audits see the full wiring surface in one place.

ACCEPTANCE
pytest -q + tsc --noEmit green; every wave-2/3 feature demonstrably
reachable per the wiring tests.

STOP CONDITION
A feature that cannot be wired without changing its files → stop, report
to integrator.

FINAL STEPS
pytest -q; tsc --noEmit; commit `feat(wiring): phase-3 integration`.
Report the wiring map (feature → mount point → proof test).
```

## Prompt: Phase 3 INTEGRATOR

```
ROLE: Integrator, Phase 3.

MERGE ORDER
Wave 1: p3/tools-core (alone, then recreate wave-2 worktrees from main).
Wave 2: p3/search → p3/needle → p3/attachments → p3/rag → p3/memory →
p3/projects (pytest between each).
Wave 3: p3/chat-ux → p3/artifacts → p3/projects-ui.
Wave 4: p3/wiring.

DEPENDENCY BATCH: collect the dependency requests from search (ddgs),
attachments (pymupdf etc.), artifacts (pyodide sourcing) and apply them in
ONE owner commit to pyproject.toml before merging those branches.

SEMANTIC-CONFLICT CHECKLIST
[ ] Every Tool module matches base.py protocol (names: web_search,
    fetch_url, file_ops, memory_save, memory_search — match config.tools
    keys exactly).
[ ] Context providers (attachments, rag, memory, projects) all register on
    the same seam function tools-core shipped; injection order documented.
[ ] needle_dispatch.emit_call signature matches the orchestrator's lazy
    import.
[ ] attachments→rag and projects→rag lazy imports resolve post-merge.
[ ] SSE tool events parsed by chat.ts match orchestrator emission; golden
    transcript updated once, intentionally.
[ ] Artifact exports used by chat-ux match panel.ts exports.
[ ] No non-owner touched config.yaml/app/config.py/app/schema.sql/
    orchestrator/app/main.py/src/api.ts/src/app.ts.

PHASE EXIT DEMO (on the box)
Daily-driver run: project with docs → ask a question → cited RAG answer;
web_search turn with provider shown; upload a PDF and discuss it; save +
recall a memory; HTML + Python artifacts render client-side; long chat
compacts; every stage visible in Debug.

FINAL STEPS
pytest -q + tsc --noEmit; remove worktrees; report merges, drift, demo.
```

## Prompt: Phase 3 VERIFICATION

```
ROLE: Verification agent, Phase 3. Read-only.
Per branch (all ten): diff-vs-FILE-SCOPE audit (flag every out-of-scope
file, especially frozen shared files and other agents' modules);
scratch-worktree pytest -q + tsc --noEmit; contract greps: every new stage
emits a span; tool names match config keys; zero hardcoded model aliases;
streams always end done|error in tests; iframe sandbox attribute is
allow-scripts only. Verdict table + merge/bounce per branch. No fixes, no
merges.
```

---

# Phase 4 — Code

Docker exec sandbox, Tier-2 artifacts, opencode integration, Code area UI
(PLAN.md §5 Phase 4). Exit: replaces Cursor for small/medium tasks.

**Operator pre-step (before wave 1):** install opencode on the box;
`opencode serve` as systemd unit on :4096; pin the version; smoke-test the
OpenAPI surface (`curl :4096/doc`) and record the event-stream endpoint
shape in docs/phase0-measurements.md (PLAN.md §4.4 [UNCERTAIN] — resolve
before UI work). Build nothing until this is recorded.

## Wave 1 (parallel)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| exec-sandbox | `p4/exec-sandbox` | `../AI-Mega-App-p4-exec` | `app/exec/**`, `docker/**`, `app/tools/run_code.py`, `tests/test_exec.py` | Phase 3 merged |
| opencode | `p4/opencode` | `../AI-Mega-App-p4-opencode` | `app/opencode/**`, `docs/opencode.md`, `tests/test_opencode.py` | Phase 3 + operator pre-step |
| code-ui | `p4/code-ui` | `../AI-Mega-App-p4-ui` | `src/views/code.ts`, `web/css/code.css` | operator pre-step |
| artifacts-t2 | `p4/artifacts-t2` | `../AI-Mega-App-p4-t2` | `src/artifacts/exec.ts` | Phase 3 merged |

## Wave 2 (single agent)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| wiring | `p4/wiring` | `../AI-Mega-App-p4-wiring` | `app/main.py` (OWNER), `src/app.ts`/`src/router.ts` (OWNER), `config.yaml`+`app/config.py` (OWNER: exec/opencode keys), `tests/test_phase4_wiring.py` | wave 1 merged |

## Prompt: `p4/exec-sandbox`

```
CONTEXT
AI-Mega-App, Phase 4 wave 1. Tier-2 execution: POST /api/exec runs code in
a short-lived Docker container — --network none, mem/cpu/pids limits,
read-only rootfs + tmpfs workdir, 30s timeout (PLAN.md §4.6 Tier 2). The
LAN has no auth, but tool-executed code is NOT the owner — the sandbox is
the security boundary. Tool protocol + registry from Phase 3. Source of
truth: PLAN.md §4.6, §4.7, §5 (Phase 5 sandbox-audit forthcoming);
docs/FEATURES.md §exec.

GOAL
1. docker/sandbox-python/Dockerfile + docker/sandbox-node/Dockerfile —
   slim images, non-root user, common libs (python: numpy/pandas/
   matplotlib; node: none beyond stdlib), no package installs at runtime.
2. app/exec/runner.py — run(code, lang, files: dict|None, timeout_s=30) ->
   ExecResult(stdout, stderr, exit_code, duration_ms, artifacts: dict of
   produced files under a size cap). Docker invocation with EXACTLY:
   --network none, --memory, --cpus, --pids-limit, --read-only, tmpfs
   workdir, auto-remove, hard kill at timeout. Span stage: exec.
3. app/exec/api.py — POST /api/exec {lang, code, files?} → ExecResult;
   rejects langs without an image.
4. app/tools/run_code.py — registry Tool (name exactly run_code) over the
   runner; config flag config.tools.run_code.enabled (key added by wave-2
   wiring — read defensively with a default of false until then).
5. tests/test_exec.py — runner tested against a FakeDocker (subprocess
   shim) asserting the exact docker argv (flags above are the contract);
   one optional integration test marked `@pytest.mark.docker` for the box.

NON-GOALS
No frontend, no main.py/config edits (wiring owns), no image publishing,
no opencode.

FILE SCOPE (hard boundary)
  app/exec/**  docker/**  app/tools/run_code.py  tests/test_exec.py
READ-ONLY: everything else.

INTERFACES
Endpoint + ExecResult verbatim (artifacts-t2 and run_code consume them).

CONSTRAINTS
Plan Mode first. Files under 300 lines. The docker argv list is built in
one function with the security flags as non-optional constants — a caller
cannot disable them.

ACCEPTANCE
pytest -q passes; argv contract test pins every security flag; tool
reachable through the tool loop fixture.

STOP CONDITION
Config keys or main wiring → leave to p4/wiring; report needs.

FINAL STEPS
pytest -q; commit `feat(exec): docker sandbox runner, /api/exec,
run_code`. Report the argv contract + image contents.
```

## Prompt: `p4/opencode`

```
CONTEXT
AI-Mega-App, Phase 4 wave 1. Division of labor (PLAN.md §4.4): no
workspace → chat model + artifact sandbox; real directory/repo on the box
→ opencode session scoped to it. opencode serve runs on :4096 (systemd,
version pinned; its event-stream shape recorded in
docs/phase0-measurements.md — build against the recorded shape). The
router may SUGGEST delegation; the user confirms — agent loops never nest
silently. opencode's provider must point at llama-swap /v1 via generated
opencode.json (generator, not hand/AI-written). Source of truth: PLAN.md
§4.4; docs/FEATURES.md §opencode.

GOAL
1. app/opencode/client.py — thin httpx client for the pinned OpenAPI
   surface: list_sessions, create_session(directory), send_prompt,
   stream_events(session_id) -> AsyncIterator[dict]. Base URLs from
   config.opencode.endpoints (list of {name, base_url}; key arrives via
   wave-2 wiring — read defensively).
2. app/opencode/confgen.py — deterministic generator: render opencode.json
   with a custom OpenAI-compatible provider at llama-swap's /v1 + model
   list from config (same pattern as swapgen; "generated — do not
   hand-edit" header); write-to-path function the Settings API can call.
3. app/opencode/api.py — APIRouter proxying to the client:
     GET /api/code/sessions · POST /api/code/sessions {directory} ·
     POST /api/code/sessions/{id}/prompt ·
     GET /api/code/sessions/{id}/events (SSE relay, terminating done|error
     like every stream in this app).
4. Delegation suggestion: pure function suggest_delegation(text,
   project_path) -> bool (path-on-box heuristic per PLAN §4.4 item 1) —
   exported for the orchestrator later; wiring decides where it surfaces.
5. docs/opencode.md — the PLAN-mandated doc: systemd unit, provider
   switching local llama-swap ↔ opencode zen (both directions, config
   snippets), VS Code + opencode workflow.
6. tests/test_opencode.py against a fake opencode server fixture.

NON-GOALS
No UI (code-ui parallel), no orchestrator/tool-loop integration (a
delegated agent is not an in-loop tool — PLAN §4.4 item 2), no main.py.

FILE SCOPE (hard boundary)
  app/opencode/**  docs/opencode.md  tests/test_opencode.py
READ-ONLY: everything else.

INTERFACES
Endpoints verbatim (code-ui codes against them). SSE relay events:
opencode event JSON passed through under event name `oc`, plus
done/error.

CONSTRAINTS
Plan Mode first. Files under 300 lines. Pin the opencode version in
client.py docstring + docs. Unreachable opencode → clean 503 with detail,
never a hang.

ACCEPTANCE
pytest -q passes: session CRUD against fake, event relay terminates
correctly, confgen golden output.

STOP CONDITION
Recorded event-stream shape ambiguous or missing → stop and ask the
operator to complete the pre-step; build nothing on guesses.

FINAL STEPS
pytest -q; commit `feat(opencode): client, api relay, confgen, docs`.
Report endpoints + the pinned version.
```

## Prompt: `p4/code-ui`

```
CONTEXT
AI-Mega-App, Phase 4 wave 1. A parallel agent ships (code against these
verbatim): GET/POST /api/code/sessions · POST /api/code/sessions/{id}/
prompt · GET /api/code/sessions/{id}/events (SSE: `oc` events passthrough
+ done/error). Web shell conventions: mount/unmount views, hash routes,
theme.css variables, DOMPurify. Source of truth: PLAN.md §4.4 (web app
surface list); docs/FEATURES.md §code-ui.

GOAL
src/views/code.ts + web/css/code.css — the Code area:
1. Session list (directory, status, updated).
2. New session: directory picker (text input + recent list from
   localStorage), endpoint picker when multiple opencode endpoints exist.
3. Session viewer: prompt composer + streamed event feed (renders `oc`
   events: plans, tool actions, file diffs as collapsible blocks;
   connection-lost on non-terminated streams — house rule).
4. "Open in VS Code" deep-link (vscode://file/<path>).

NON-GOALS
No backend edits, no nav registration (wiring), no delegation-suggestion
UI in chat (wiring decides placement; expose a small exported banner
component `renderDelegationSuggestion(dir, onConfirm)` for it).

FILE SCOPE (hard boundary)
  src/views/code.ts  web/css/code.css
READ-ONLY: everything else.

CONSTRAINTS
Plan Mode first. Files under 300 lines (split into src/views/code/ if
needed — declare in plan). DOMPurify all opencode-originated content.

ACCEPTANCE
tsc --noEmit clean; manual proof against the fake opencode fixture via
scripts/dev.sh: create session, send prompt, watch events stream, done
terminates cleanly.

STOP CONDITION
Endpoint drift → report; invent nothing.

FINAL STEPS
tsc --noEmit; pytest -q stays green. Commit
`feat(web): code area (opencode sessions)`. Report components exported.
```

## Prompt: `p4/artifacts-t2`

```
CONTEXT
AI-Mega-App, Phase 4 wave 1. Tier-1 artifact panel exists (src/artifacts:
panel/detect/sandbox/pyodide). A parallel agent ships POST /api/exec
{lang, code, files?} -> {stdout, stderr, exit_code, duration_ms,
artifacts}. Tier 2 = "Run on server" for code needing real deps (PLAN.md
§4.6). Source of truth: PLAN.md §4.6; docs/FEATURES.md §artifacts.

GOAL
src/artifacts/exec.ts — adds a "Run on server" action to python/js
artifacts in the panel: POSTs /api/exec, renders stdout/stderr/exit code
and returned artifact files (download links; images inline); busy state +
30s client timeout mirroring the server's. Integrate via the existing
panel extension point (add one if missing INSIDE src/artifacts/panel.ts —
that file is within your reach ONLY for registering the action hook; keep
the diff to a hook registration, under ~15 lines).

NON-GOALS
No runner changes, no Pyodide changes, no chat.ts edits.

FILE SCOPE (hard boundary)
  src/artifacts/exec.ts  src/artifacts/panel.ts (hook registration only)
READ-ONLY: everything else.

CONSTRAINTS
Plan Mode first. File under 300 lines. Endpoint shape verbatim.

ACCEPTANCE
tsc --noEmit clean; manual proof: python artifact → Run on server →
output rendered (fake backend acceptable).

STOP CONDITION
Panel refactor needed beyond the 15-line hook → stop and ask.

FINAL STEPS
tsc --noEmit; pytest -q stays green. Commit
`feat(web): tier-2 run-on-server for artifacts`. Report the hook diff.
```

## Prompt: `p4/wiring`

```
CONTEXT
AI-Mega-App, Phase 4 wave 2. Wave 1 merged: app/exec + run_code,
app/opencode (+ confgen, delegation suggester), code UI, tier-2 artifact
action. You OWN main.py, src/app.ts, src/router.ts, config.yaml,
app/config.py this wave. Source of truth: PLAN.md §4.4, §4.6, §5 (Phase 4
exit); docs/FEATURES.md.

GOAL
1. Config keys: exec: {enabled, images: {python, node}, timeout_s: 30,
   mem_mb, cpus, pids} · opencode: {endpoints: [{name, base_url}],
   confgen_path} · tools.run_code: {enabled: true}.
2. app/main.py — include exec + opencode routers.
3. src/app.ts + src/router.ts — register #/code, sidebar Code item;
   surface renderDelegationSuggestion in chat when
   suggest_delegation(...) info arrives (add the flag to the chat `done`
   payload via a ≤10-line orchestrator touch — orchestrator ownership
   granted for that diff only; keep it minimal and flagged in the
   commit).
4. tests/test_phase4_wiring.py — reachability: run_code in tools array
   and executes via FakeDocker; /api/exec 200; code session round-trip
   via fake opencode; config validates.

NON-GOALS
No feature logic; report gaps instead of patching.

FILE SCOPE (hard boundary)
  app/main.py  src/app.ts  src/router.ts  config.yaml  app/config.py
  app/chat/orchestrator.py (≤10-line delegation flag only)
  tests/test_phase4_wiring.py

CONSTRAINTS
Plan Mode first. Minimal diffs; single visible wiring block.

ACCEPTANCE
pytest -q + tsc --noEmit green; wiring tests prove reachability.

STOP CONDITION
Anything needing more than the granted orchestrator diff → stop and
report.

FINAL STEPS
pytest -q; tsc --noEmit; commit `feat(wiring): phase-4 integration`.
Report wiring map + the orchestrator diff verbatim.
```

## Prompt: Phase 4 INTEGRATOR

```
ROLE: Integrator, Phase 4.

MERGE ORDER
1. p4/exec-sandbox  2. p4/opencode  3. p4/artifacts-t2  4. p4/code-ui
5. p4/wiring

SEMANTIC-CONFLICT CHECKLIST
[ ] /api/exec request/response shape identical in runner api, run_code,
    and src/artifacts/exec.ts.
[ ] code.ts endpoint paths + `oc` event handling match app/opencode/api.py
    as shipped.
[ ] Security argv contract test still pins --network none, --read-only,
    limits — unchanged by any merge.
[ ] confgen output validates as opencode.json against the pinned version.
[ ] Delegation flag payload shape matches what wiring's UI reads.
[ ] Non-owners touched no shared files; artifacts-t2's panel.ts diff is
    hook-registration only (~15 lines).

PHASE EXIT DEMO (on the box)
run_code executes in Docker with network disabled (prove: code that curls
fails). Artifact "Run on server" works. Create an opencode session on a
real repo directory from the Code area, watch events, open in VS Code.
Delegation suggested for a repo-path request; user confirms.

FINAL STEPS
pytest -q + tsc --noEmit; remove worktrees; report.
```

## Prompt: Phase 4 VERIFICATION

```
ROLE: Verification agent, Phase 4. Read-only.
Per branch: diff-vs-FILE-SCOPE audit; scratch pytest -q + tsc --noEmit;
contract greps: docker security flags present as constants; every stage
spans; streams end done|error; no hardcoded model/endpoint literals
(config only); panel.ts diff ≤ hook registration. Verdict table +
merge/bounce. No fixes.
```

---

# Phase 5 — Reach + hardening

BrowserOS MCP, memory reviewer, bench panel, preflight, sandbox audit, full
docs (PLAN.md §5 Phase 5).

**Operator pre-step:** on the Windows host, install BrowserOS; determine its
MCP transport + whether the MCP server accepts non-localhost connections
(PLAN.md §4.12 [UNCERTAIN]); if localhost-only, set up the relay/SSH tunnel;
record endpoint + transport in docs/phase0-measurements.md before p5/browser
starts.

## Wave 1 (parallel)

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| browser | `p5/browser` | `../AI-Mega-App-p5-browser` | `app/tools/browser.py`, `app/mcp/**`, `tests/test_browser_tool.py` | operator pre-step |
| reviewer | `p5/reviewer` | `../AI-Mega-App-p5-reviewer` | `app/memory/reviewer.py`, `app/memory/queue.py`, `src/views/review.ts`, `web/css/review.css`, `tests/test_reviewer.py` | Phase 4 merged |
| bench | `p5/bench` | `../AI-Mega-App-p5-bench` | `scripts/llama_bench.py`, `app/debug/bench.py`, `src/views/debug.ts` (OWNER this wave), `tests/test_bench.py` | Phase 4 merged |
| preflight | `p5/preflight` | `../AI-Mega-App-p5-preflight` | `scripts/preflight.py` | Phase 4 merged |
| docs | `p5/docs` | `../AI-Mega-App-p5-docs` | `docs/features/**`, `README.md` | Phase 4 merged |

## Wave 2

| Agent | Branch | Worktree | FILE SCOPE | Depends |
|---|---|---|---|---|
| wiring | `p5/wiring` | `../AI-Mega-App-p5-wiring` | `app/main.py`, `src/app.ts`/`src/router.ts`, `config.yaml`+`app/config.py` (browser/reviewer keys), `tests/test_phase5_wiring.py` | wave 1 merged |

Plus an operator **sandbox audit checklist** (below) — not a Cursor agent.

## Prompt: `p5/browser`

```
CONTEXT
AI-Mega-App, Phase 5 wave 1. BrowserOS runs on the Windows host exposing
an MCP server with 31+ browser tools; our backend is an MCP CLIENT over
LAN. Off by default, per-chat toggle — browser actions are consequential
(PLAN.md §4.12). Transport + endpoint are RECORDED in
docs/phase0-measurements.md (operator pre-step) — build against that,
not guesses. Tool protocol from Phase 3. Source of truth: PLAN.md §4.12;
docs/FEATURES.md §browser.

GOAL
1. app/mcp/client.py — minimal generic MCP client for the recorded
   transport: initialize, list_tools, call_tool(name, args); typed errors;
   connect timeout from config.
2. app/tools/browser.py — registry Tool (name exactly `browser`,
   enabled default FALSE): exposes the MCP toolset to capable models as
   one dispatching tool ({action, args} against the discovered MCP tool
   list, schema built from list_tools at startup, cached, refresh
   endpoint-less — reconnect on error). Span stage: browser, fields:
   action, latency, error.
3. Per-chat toggle honored via ToolContext (a chat-level enabled_tools
   check the tool loop already performs — verify and consume, do not
   reimplement).
4. tests/test_browser_tool.py against a fake MCP server fixture.

NON-GOALS
No deep-research pipeline (browser is the escalation path, not primary —
PLAN §4.12 item 2), no UI, no wiring/config edits (wave 2).

FILE SCOPE (hard boundary)
  app/mcp/**  app/tools/browser.py  tests/test_browser_tool.py
READ-ONLY: everything else.

CONSTRAINTS
Plan Mode first. Files under 300 lines. Endpoint/transport from config
key config.browser (wiring adds it — read defensively, disabled when
absent). Unreachable BrowserOS → tool returns is_error result, chat
continues.

ACCEPTANCE
pytest -q passes: tool discovery, dispatch, error paths, disabled-by-
default, reachable through the tool loop fixture when enabled.

STOP CONDITION
Recorded transport missing/ambiguous → stop, ask operator.

FINAL STEPS
pytest -q; commit `feat(browser): MCP client + browser tool`. Report the
transport used + dispatch schema.
```

## Prompt: `p5/reviewer`

```
CONTEXT
AI-Mega-App, Phase 5 wave 1. The hermes-style self-improvement loop,
queue-first: after a turn, a background job on the utility model reviews
the transcript and may PROPOSE a memory write — proposals land in a
review queue in the UI; auto-accept optional per scope; nothing invisible
(PLAN.md §4.8). Memory store/api + background queue + on_turn_complete
hook exist. Source of truth: PLAN.md §4.8; docs/FEATURES.md §memory.

GOAL
1. app/memory/reviewer.py — background job (submitted via
   app.background.queue from the existing on_turn_complete flow — extend
   via a registration hook exported from app/background, NOT by editing
   background files; if no such hook exists, STOP CONDITION): utility
   model + json_schema response_format → zero or more proposals
   {scope, content, reason}. Span stage: memory_review.
2. app/memory/queue.py — proposals table access (DDL request via STOP
   CONDITION if absent), states proposed|accepted|rejected;
   auto-accept per scope from config.memory.auto_accept (wiring adds the
   key — defensive default: no auto-accept). API additions in
   app/memory/queue.py exposing `router`: GET/POST
   /api/memories/proposals (+ accept/reject).
3. src/views/review.ts + web/css/review.css — review queue UI (list,
   accept/reject, scope badges), mounted under Settings→Memory (wiring
   registers the route).
4. tests/test_reviewer.py — canned reviewer output → proposals queued;
   accept writes a memory; auto-accept path; reviewer failure is silent
   to chat.

NON-GOALS
No skill creation (Future §7), no background/queue.py edits, no wiring.

FILE SCOPE (hard boundary)
  app/memory/reviewer.py  app/memory/queue.py  src/views/review.ts
  web/css/review.css  tests/test_reviewer.py
READ-ONLY: everything else.

CONSTRAINTS
Plan Mode first. Files under 300 lines. Utility model alias from config.
Proposals are the ONLY write path — the reviewer never writes memories
directly.

ACCEPTANCE
pytest -q + tsc --noEmit green; queue round-trip proven end-to-end
against fakes.

STOP CONDITION
Missing background registration hook or proposals DDL → stop, request the
exact addition from the respective owner.

FINAL STEPS
pytest -q; tsc --noEmit; commit `feat(memory): reviewer + proposal
queue`. Report the review prompt schema + hook/DDL requests granted.
```

## Prompt: `p5/bench`

```
CONTEXT
AI-Mega-App, Phase 5 wave 1. Debug panel needs per-model tok/s sanity
numbers + llama-swap state + GPU snapshot (PLAN.md §4.16, §4.1 model
classes note). scripts/bench_models.sh exists from Phase 0; llama-bench
lives with the llama.cpp build. You OWN src/views/debug.ts this wave.
Source of truth: PLAN.md §4.16, §4.10; docs/FEATURES.md §debug.

GOAL
1. scripts/llama_bench.py — python wrapper over llama-bench for one model
   or all configured models; writes results to a bench_results table (DDL
   via STOP CONDITION) with timestamp, model, pp/tg tok/s.
2. app/debug/bench.py — APIRouter: POST /api/debug/bench/{model} (runs on
   the box, streams progress), GET /api/debug/bench (latest per model);
   plus GET /api/debug/gpu (nvidia-smi poll) and GET /api/debug/swap
   (proxy llama-swap's status/metrics endpoint from config).
3. src/views/debug.ts — new panel sections: model bench table with
   run-button, live GPU memory bars, llama-swap state (loaded model per
   group). Keep the existing waterfall untouched.
4. tests/test_bench.py — parser + endpoints against canned outputs.

NON-GOALS
No full benchmark suite (Future §4), no main.py edits.

FILE SCOPE (hard boundary)
  scripts/llama_bench.py  app/debug/bench.py  src/views/debug.ts
  tests/test_bench.py
READ-ONLY: everything else.

CONSTRAINTS
Plan Mode first. Files under 300 lines. llama-bench path + llama-swap URL
from config. Bench runs refuse to start while a chat stream is active on
the target group (check llama-swap state first).

ACCEPTANCE
pytest -q + tsc --noEmit green; endpoints proven with canned data.

STOP CONDITION
bench_results DDL → request from schema owner.

FINAL STEPS
pytest -q; tsc --noEmit; commit `feat(debug): bench panel, gpu + swap
state`. Report endpoints + panel sections.
```

## Prompt: `p5/preflight`

```
CONTEXT
AI-Mega-App, Phase 5 wave 1. Live hardware check, run on the box, never
CI (PLAN.md §4.10). Config carries the full roster + llama-swap URL.
Source of truth: PLAN.md §4.10; docs/FEATURES.md §testing.

GOAL
scripts/preflight.py — sequential checks with clear PASS/FAIL lines and
nonzero exit on any failure:
1. nvidia-smi present; expected GPU count/indices vs config.
2. llama-swap /health (or status endpoint from config) answers.
3. Every configured model: chat models answer 1 token via /v1 (this
   exercises swap for each big model — print swap latency); embed model
   answers /v1/embeddings; classifier returns valid schema JSON; needle
   answers.
4. Backend /health, /api/debug/traces answer if the app is running
   (skip-with-notice otherwise).
5. Docker present + sandbox images built (skip-with-notice if exec
   disabled).
Flags: --skip-swap (residents only), --json.

NON-GOALS
No fixes, no CI integration, no app code.

FILE SCOPE (hard boundary)
  scripts/preflight.py

CONSTRAINTS
Plan Mode optional (single file). Under 300 lines. Everything from
config.yaml — zero literals. Each check independently timed out.

ACCEPTANCE
python -m py_compile passes; a --json dry-run against the fake llama-swap
(base-url flag) exercises every check path in tests? No test file is in
scope — demonstrate via the fake with scripts/dev.sh and paste the output
in your report.

STOP CONDITION
Any second file → ask.

FINAL STEPS
pytest -q stays green; commit `feat(scripts): preflight hardware check`.
Report sample output.
```

## Prompt: `p5/docs`

```
CONTEXT
AI-Mega-App, Phase 5 wave 1. Key Rule 7: per-feature docs — what/why/
how-to-extend (PLAN.md §5 Phase 5, §6). All features are merged through
Phase 4; read the code, do not guess. Source of truth: PLAN.md; the
codebase itself; docs/FEATURES.md.

GOAL
docs/features/<name>.md for: inference (llama-swap+swapgen), router,
chat-sse, debug, tools, search, needle, attachments, projects, rag,
memory, artifacts, exec, opencode (extend existing docs/opencode.md via
link, do not duplicate), background, settings. Each ≤120 lines: What it
does · Why this design (link PLAN.md §) · Key interfaces (real
signatures, copied from code) · Config keys · Debug span stages · How to
extend (e.g. "add an extractor: one module in app/attachments/, register
mime prefixes"). Plus README.md rewrite: quickstart (box setup pointer to
phase-0 doc, scripts/dev.sh, preflight), architecture diagram from
PLAN.md §3, docs index.

NON-GOALS
No code changes of any kind, no CURSOR_RULES.md/PHASE_PROMPTS.md edits.

FILE SCOPE (hard boundary)
  docs/features/**  README.md

CONSTRAINTS
Plan Mode first (list the doc set). Signatures copied from source, cited
by path. Affirmative, terse prose.

ACCEPTANCE
Every listed feature has a page; every config key referenced exists in
config.yaml (grep-verify); pytest -q stays green (you touched no code).

STOP CONDITION
A doc reveals an undocumented interface mismatch → note it in the report
for the integrator; change no code.

FINAL STEPS
pytest -q; commit `docs: per-feature pages + README`. Report pages
written + mismatches found.
```

## Prompt: `p5/wiring`

```
CONTEXT
AI-Mega-App, Phase 5 wave 2. Wave 1 merged: browser tool + MCP client,
reviewer + proposal queue (+ requested DDL/hooks), bench panel,
preflight, docs. You OWN main.py, src/app.ts, src/router.ts, config.yaml,
app/config.py. Source of truth: PLAN.md §5 (Phase 5); docs/FEATURES.md.

GOAL
1. Config keys: browser: {endpoint, transport, timeout_s,
   enabled: false} · memory.auto_accept: {user: false, project: false,
   global: false} · debug.bench: {llama_bench_path} ·
   tools.browser: {enabled: false}.
2. app/main.py — include queue + bench routers; reviewer registration on
   the background hook.
3. src/app.ts + src/router.ts — register review view under Settings;
   per-chat browser toggle in the chat composer tools menu (≤15-line
   chat.ts touch, granted for that diff only).
4. tests/test_phase5_wiring.py — reachability: browser tool appears in
   tools array only when enabled+toggled; a fake-reviewed turn produces a
   proposal reachable via API; bench endpoints mount.

FILE SCOPE (hard boundary)
  app/main.py  src/app.ts  src/router.ts  config.yaml  app/config.py
  src/views/chat.ts (≤15-line toggle only)  tests/test_phase5_wiring.py

CONSTRAINTS
Plan Mode first. Minimal diffs, single wiring block.

ACCEPTANCE
pytest -q + tsc --noEmit green; wiring tests pass.

STOP CONDITION
Larger diffs needed → stop, report.

FINAL STEPS
pytest -q; tsc --noEmit; commit `feat(wiring): phase-5 integration`.
Report wiring map.
```

## Operator: sandbox audit checklist (Phase 5, no agent)

```
The backend has no auth (trusted LAN — owner decision); the SANDBOX is
still a boundary because tool-executed code is not the owner:
[ ] /api/exec containers: verify --network none (curl from inside fails),
    read-only rootfs, memory/pids limits enforced (fork bomb dies).
[ ] file_ops cannot escape project roots (attempt ../ and symlink
    escapes).
[ ] Artifact iframes: sandbox="allow-scripts" only; no same-origin.
[ ] browser tool default-off; enabling is per-chat and visible.
[ ] .env never served: grep static mounts; curl for /.env returns 404.
[ ] Generated files (llama-swap.yaml, opencode.json) written only to
    their configured paths.
Record results in docs/phase0-measurements.md §audit or a new
docs/audit.md via a trivial PR.
```

## Prompt: Phase 5 INTEGRATOR

```
ROLE: Integrator, Phase 5 (final).

MERGE ORDER
1. p5/preflight  2. p5/bench  3. p5/browser  4. p5/reviewer  5. p5/docs
6. p5/wiring

SEMANTIC-CONFLICT CHECKLIST
[ ] Reviewer's background-hook + proposals DDL requests were granted via
    single owner commits; reviewer imports match.
[ ] browser tool reads config.browser exactly as wiring shipped it;
    disabled by default end-to-end.
[ ] debug.ts sections coexist with the Phase 1 waterfall (bench owned the
    file — confirm no waterfall regression).
[ ] Docs signatures match merged code (spot-check 3 pages).
[ ] Wiring's chat.ts diff ≤15 lines; non-owners touched no shared files.

FINAL DEMO (on the box)
preflight green; bench numbers in Debug; browser tool toggled on in one
chat drives BrowserOS; a conversation yields a memory proposal, accepted
via UI, injected next turn; sandbox audit checklist complete. Run the
full Phase 3 daily-driver demo again as a regression sweep.

FINAL STEPS
pytest -q + tsc --noEmit; scripts/eval_router.py --min-accuracy 90 still
green; remove all worktrees; report.
```

## Prompt: Phase 5 VERIFICATION

```
ROLE: Verification agent, Phase 5. Read-only.
Per branch: diff-vs-FILE-SCOPE audit (granted micro-diffs checked against
their stated line budgets); scratch pytest -q + tsc --noEmit; contract
greps: spans on new stages, config-only endpoints/models, browser tool
default-disabled, streams end done|error. Verdict table + merge/bounce.
No fixes, no merges.
```
