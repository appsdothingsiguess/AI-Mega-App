# Handoff notes (agent-only)

Working notes for whichever Claude Code session picks this up next.
Not a planning doc, not user-facing — just context that isn't obvious
from the code alone. Delete or trim entries once they're stale.

## 2026-08-11 — bug: warmup not leaving resident models loaded after deploy — FIXED

**Root cause confirmed: hypothesis 1 (INFO logs invisible) was the whole story — the warmup loop was working correctly the entire time, just unobservable.**

Fix (`30ee188`): added `logging.basicConfig(level=logging.INFO, ...)` in `app/main.py` — uvicorn's default logging config (dictConfig) only sets up the `uvicorn`/`uvicorn.error`/`uvicorn.access` loggers, never the root logger, so it stayed at default WARNING and silently dropped every `app.*` `logger.info(...)` call, including the warmup loop's own observability. Also hardened `app/gpu/api.py::post_apply` to retry its post-apply warmup up to 3x (checking `all_residents_loaded`) instead of a single shot, since a reload-race ("group is shutting down") can still hit the first attempt.

**Live verification (2026-08-11 20:00, after `POST /api/gpu/apply` + `sudo systemctl restart ai-mega-app`):** journalctl now shows the full startup sweep — `warm-up starting`/`warm-up complete` for chat-default, dispatcher, utility, embed, classifier, then `warmup loop: all residents loaded, switching to steady-state (300s interval)` at 20:00:05, ~7s after restart. `curl :8080/v1/models` confirms all 5 residents `loaded`; coder/coder-small/vision correctly `unloaded` (non-resident swap group). Interestingly, the *previous* apply's post_apply warmup attempt (19:59:35) did hit the "group is shutting down" race on all 4 CPU/GPU1 residents — exactly hypothesis 2 — but the restart's own startup sweep recovered them cleanly regardless, so the retry hardening is defense in depth, not what fixed this particular symptom.

**Original report below, kept for context:**

Config regeneration is CORRECT (verified live): every `env:` pair now has
`CUDA_DEVICE_ORDER=PCI_BUS_ID` beside `CUDA_VISIBLE_DEVICES`, `resident` group =
`[dispatcher, utility, embed, classifier]`, `gpu0-main` = `[chat-default, coder,
coder-small, vision]`. `/home/john/llm-stack/serving/llama-swap/config.yaml`
matches `git` swapgen golden output. Services `active`.

**Symptom:** after `POST /api/gpu/apply` + `sudo systemctl restart ai-mega-app`,
`/v1/models` shows the CPU/GPU1 residents (dispatcher, utility, embed, classifier)
UNLOADED, and they stay unloaded over time — only `chat-default` comes up loaded.
Manually `curl`ing a `chat/completions` to `classifier` loads it immediately and
subsequently shows `loaded`, so llama-swap + config + GPU pinning are healthy;
the warmup loop is not doing its job.

**Root-cause hypotheses (investigate next session):**
1. **INFO logs invisible (confirmed).** No `logging.basicConfig`/`setLevel` anywhere
   in `app/` — Python default WARNING level suppresses all `logger.info(...)`
   output. So `warmup.py:60` "warm-up starting", `main.py:76` "warmup loop:
   starting sweep", and `main.py:80` "sweep complete" never reach journalctl.
   Old PID's `warm-up failed (...)` lines DID show because they're WARNING.
   → Means the warmup loop's only observability (WS-B's logging fix) is dead in
   production. Add logging config (INFO for `app.*`) or raise these to WARNING.
   This alone doesn't explain residents staying unloaded, but it hides whether
   the loop runs.
2. **`post_apply` single-shot warmup races llama-swap reload (likely).**
   `app/gpu/api.py:145` calls `warmup_resident_models(...)` immediately after the
   health poll. llama-swap's `-watch-config` reload kills all llama-servers; at
   that instant requests can fail with `{"error":"unspecific error: group is
   shutting down"}` (seen at 19:41). It is a single pass, NOT a retry — so if it
   fires mid-reload, residents stay cold with no recovery until the 300s sweep.
3. **Startup retry loop not actually warming (need to confirm).**
   `_warmup_loop` (main.py:57-88) retries every `_STARTUP_BACKOFF_S` (15s) while
   `all_residents_loaded()` is False, else settles to 300s. Wait-then-`/v1/models`
   after restart showed residents still unloaded >20s later → suggests the loop is
   NOT resolving them, or `app.state.llm_client`/`config` weren't picked up, or the
   loop died on a startup-phase exception (hidden by suppressed INFO + only
   WARNING exposure). Verify the task is alive and `all_residents_loaded` logic
   against live `/v1/models` shape (`status.value`).

**Deploy commands used (live):** `curl -X POST http://localhost:8000/api/gpu/apply`
then `sudo systemctl restart ai-mega-app`. Note: first apply was run BEFORE restart
(i.e. old code) → produced stale config missing `CUDA_DEVICE_ORDER`; re-ran apply
after restart → correct config. Verify config, don't assume.

**Verification note:** `python` on box = `.venv/bin/python` (repo root `.venv`).

**Related:** the whole point of WS-B (`fix/backend-reliability`, commit 86d6cf5 /
merged 0c8ac3d) was "residents hot at service start" — this deploy shows that goal
is not met in production. Treat as the top open item.

## Where things stand (2026-08-11 — post-audit fix plan)

Full audit completed; see `docs/AGENT_CONTEXT_MEGA.md` (facts, code-verified) and `docs/FIX_PLAN_2026-08-11.md` (execution). Four parallel workstreams in flight, one worktree each, disjoint FILE SCOPEs, all forked from `main` at `0170ca4`:

- **WS-A fix/config-drift** — swapgen emits `CUDA_DEVICE_ORDER=PCI_BUS_ID` alongside every `CUDA_VISIBLE_DEVICES=N`; revert `settings.local.yaml` drift (coder-small residency/ttl_s, routing overlay attachments + code_task rules, classifier timeout 6→90s); settings.json legacy ollama tags → -16k/-24k.
- **WS-B fix/backend-reliability** — classifier/utility hot at service start (warmup retry + stored task ref on `app.state`); `_on_turn_complete` on error/timeout paths; `reasoning_content` field on `ChatDelta` + parse in `llm_client` (no new SSE events — frozen vocab).
- **WS-C fix/web-gaps** — retry affordance on error banner; response time + usage inline in `.msg-meta`; fix misleading `chat.ts:35-36` comment; real `npx tsc` build committed with `web/src/**`.
- **WS-D docs/refresh** (this branch) — refresh stale "Phase 1 open / web unbuilt" prose in `AGENTS.md`/`CLAUDE.md`; add this HANDOFF entry.

**Items audited FIXED in tree (do not redo; commits `8c4f7b4`, `53dac3a`, `0170ca4`, `96602e4`, `52f5b31`, `e9cc8fc`, `ad84232`, `492e260`, `e751a8b`, `652c910`, `fac6c8e`, `655f67d`, `e7a1a30`):** scroll stick-to-bottom (web/src/views/chat.ts:92-94,178-191); nav-interrupt / no-abort-on-unmount (chat.ts:32-42 + store `activeChatStreaming`); clipboard `execCommand` fallback (markdown.ts:87-99); stop button (composer.ts:152, chat.ts:381-389); `model_loading` banner (chat.ts:267-269,161-166); summary banner UI (chat.ts:70-86); `reasoner`→canonical swap-name alias (orchestrator.py:227-250,381); `list_models` live state from `/v1/models` (settings/api.py:111-125); GPU inventory trace spam removed (gpu/api.py:69-73); shutdown drain 8s (background/__init__.py:26,54-63); router kwargs (orchestrator.py:317-325); error-path partial persistence (orchestrator.py:481-503); warmup per-model 60s timeout + logging (warmup.py:59-74).

**Still open (see FIX_PLAN for file:line):** warmup silent no-op risk (main.py:96 unstored task ref, `_warmup_loop` no try/except, `llm_client` only set by background.start); `_on_turn_complete` success-path only (orchestrator.py:456); `reasoning_content` dropped (types.py `ChatDelta` lacks field, llm_client._parse_stream_chunk:255 reads content only); web retry/usage-inline/thinking-display gaps; config drift (CUDA_DEVICE_ORDER missing, settings.local.yaml overlay drift, classifier timeout 6s, legacy ollama tags in settings.json).

**Exploration note (deferred, after all fixes live):** MoE ctx headroom via `--n-cpu-moe` offload for `chat-default` (Qwen3.6-35B-A3B). Precedent: `qwen3-coder-30b` stable with `-ncmoe 20` in llm-stack config (~2.9GB/GPU headroom). See `docs/AGENT_CONTEXT_MEGA.md` §9.

## Where things stand (2026-08-06 — post-feature-implementation live re-test)

Session implemented three user-requested features: regenerate button,
tokens/second display, and swap-aware routing (sticky routing to avoid
unnecessary GPU0 swaps). All features committed (8c4f7b4, 136 tests passing).
User re-tested and reported 5 new issues. **Nothing in this entry is fixed
yet except where noted.**

### 1. Sticky routing works but doesn't visually show "loading model" when swap occurs — FIXED (commit 0170ca4 line of work; chat.ts:267-269,161-166)
Swap-aware routing (prefer currently-loaded GPU0 model when classifier
confidence < 0.8) is implemented and working — confirmed by user. However,
when a swap DOES occur (classifier confidence >= 0.8, or no GPU0 model
currently loaded), the UI doesn't show the "loading model" banner. The
`model_loading` SSE event is sent by the orchestrator
(`orchestrator.py:400`), but the frontend may not be displaying it correctly,
or the swap is happening so fast that the banner flashes and disappears.
**Status: NOT YET INVESTIGATED. Next session: check if `model_loading` events
are being sent and received correctly; verify the banner display logic in
`chat.ts:235-237`.**

### 2. Navigating to Debug page while messaging interrupts it — FIXED (commit 53dac3a)
**Root cause:** Chat view's `unmount()` called `abort?.abort()`, which canceled
the SSE stream. In-app navigation triggers `unmount()` via the router, but
browser tab switching does NOT (only `pagehide`/`beforeunload` on tab close).
**Fix:** Don't abort the SSE stream on unmount. Track streaming state globally
in the store (`activeChatStreaming`) so it persists across view changes. The
stream continues in the background, and when the user returns to Chat, they
load the completed message from DB. The stop button still aborts explicitly.

### 3. Scrolling while generating still broken — FIXED (commit 53dac3a)
**Root cause:** The `wasNearBottom` check was calculated before the DOM update,
but the scroll assignment happened after in a single `requestAnimationFrame`.
If the user scrolled up between the check and the assignment, they were still
yanked back down. Also, the 80px threshold was too generous.
**Fix:** Use double-`requestAnimationFrame` to ensure layout is fully committed,
then re-check the scroll position inside the callback. Reduced threshold from
80px to 20px. Now only auto-scrolls if the user is STILL near the bottom after
the layout settles.

### 4. Can't tell if summarizer is doing anything in Debug mode — FIXED (summary banner UI, chat.ts:70-86)
User reports that the rolling summary feature doesn't show any visible
activity in the Debug panel. The backend generates summaries every 6 turns
(`background/summaries.py`, `config.yaml:233`), and the Debug view should
show a `summary` span when it runs. **Status: NOT YET INVESTIGATED. Next
session: (1) verify that `maybe_enqueue_summary` is being called after every
turn (`orchestrator.py:439-440` calls `_on_turn_complete`); (2) check if the
summary job is actually running (add logging to `summaries.py:110-130`);
(3) verify that the summary span is being emitted and reaches the Debug
SSE stream; (4) check if the summary is being written to `chats.summary`
in SQLite (query the DB directly to confirm).**

### 5. Classifier timeout for trace_id: 31a0033c-59c9-451a-b495-2e24223f2ee9 — ROOT CAUSE FOUND, PARTIALLY FIXED
**Root cause:** Classifier process was cold (not loaded yet) at 03:07:39.
llama-swap logs show:
- 03:07:39: classifier request arrived, proxy tried localhost:5801 → "connection refused"
- 03:07:45: classifier health check passed (process started)
- 03:07:45: request canceled ("context canceled") after 6s timeout
- 03:07:52: classifier finished loading (1m32s cold load!)
The 6s timeout (`config.yaml:226`) is too short for a cold classifier load.
The classifier is CPU-resident but still takes 1m32s to load on first request.

**Why wasn't the classifier warmed on startup?** The warmup loop (`app/main.py:56-66`)
should ping all resident models on startup, but journalctl shows NO warmup logs
after the 02:59:46 restart. The warmup task is created (`asyncio.create_task`)
but either isn't running or is failing silently before logging.

**Partial fix (commit 0170ca4):** Added explicit logging to the warmup loop so we
can see if it's running. Next restart should show "warmup loop: starting sweep"
in journalctl. If it still doesn't appear, the task isn't being created or is
crashing before the first log.

**Remaining issue:** Even with warmup, the 6s classifier timeout is too short
for a cold load (1m32s measured). **Fix options: (1) increase `routing.classifier.timeout_s`
to 90s or more; (2) ensure warmup actually loads the classifier before the first
request; (3) add a "classifier cold" warning in the UI when the first request
takes >10s.**

**User requirement:** "The classifier should always be hot including the 2 models
on CPU. Everything depends on those models so swapping should never kill it.
When the AI process spawns, the hot models should auto-load including chat-default
since that will probably always be the first one used."

This means:
- CPU-resident models (classifier, dispatcher, utility, embed) must be eagerly
  loaded on startup and never swapped out (ttl_s: 0 in config, which is correct)
- chat-default (GPU0) should also be eagerly loaded since it's the default
- The warmup must actually run and succeed for all resident models

### What was fixed in this session
- **Commit 8c4f7b4**: Regenerate button, tokens/second display, swap-aware routing, warmup timeout, real loaded state, residency drift fix, drain timeout reduction
- **Commit 53dac3a**: Scrolling fix (double-rAF + re-check), navigation interrupt fix (global streaming state)
- **Commit 0170ca4**: Warmup logging, dedup logic fix

## Where things stand (2026-08-06 — post-restart-fix live re-test)

Follow-up session after the classifier-timeout/context-truncation fixes
landed (`app/warmup.py`, summary compaction in `orchestrator.py`,
`settings.local.yaml` classifier `timeout_s`/`ttl_s` correction) and a
systemd unit fix (`--timeout-graceful-shutdown 10` added to
`ai-mega-app.service` — `systemctl restart` was hanging indefinitely
because uvicorn's default graceful shutdown waits forever for open SSE
connections, e.g. the Debug panel's `/api/debug/stream`, to close; fixed
live, confirmed working). User re-tested and reported 4 more issues.
**Nothing in this entry is fixed yet except where noted.**

### A. "All models unloaded after chat completion" — turned out to be a UI/API lie, not real unload — FIXED (`list_models` now queries `/v1/models`, settings/api.py:111-125)
`app/settings/api.py::list_models()` (`GET /api/models`, backs the
model-picker roster) **hardcodes `"loaded": False` for every model,
always** — it's a stub predating real llama-swap wiring
(`main.py:112`'s own comment calls it a placeholder pending "Phase 2's
`GET /api/models` adds resident/loaded flags", docs/FEATURES.md F3).
It never queries llama-swap for real state, so the UI *always* shows
every model as unloaded regardless of what's actually resident — this
is why the user saw "no models loaded" right after a restart and
assumed sending a message would fix it. **Real fix:** wire this to
llama-swap's actual running-state endpoint — confirmed live via
`curl http://127.0.0.1:8080/v1/models` that llama-swap already reports
per-model `status.value` (`"loaded"`/`"unloaded"`), and
`curl http://127.0.0.1:8080/running` gives full process detail
(cmd/proxy/ttl) for what's actually up. Either is a clean data source;
`/v1/models` is the smaller shape and probably the better fit for the
roster endpoint.

**However — while checking whether this was "just" a UI bug, found a
second, real problem in the same area, unresolved:** live `curl` right
after a fresh restart showed only `coder` actually running; `chat-default`,
`classifier`, `dispatcher`, `utility`, `embed` — all `resident: true`
and supposedly eagerly warmed by `app/warmup.py`'s startup
`_warmup_loop` — showed `"unloaded"`. `journalctl -u ai-mega-app` had
**zero** `warm-up complete`/`warm-up failed` log lines for any of them
across two full restart cycles (the only warm-up log line in the entire
journal is one stale `warm-up failed (embed)` from *before* this
session's embed-endpoint fix). That means `warmup_one()` isn't even
reaching its own `try`/`except` logging for most models — not failing
loudly, just never completing/logging at all. Prime suspect, not yet
confirmed: `settings.local.yaml` currently marks **`coder`, `coder-small`,
and `vision` as `resident: true`** in addition to the intended
`chat-default`/`dispatcher`/`utility`/`embed`/`classifier` set — that's
three ~24-32GB GPU0 models simultaneously demanding permanent residency
on a single 24GB 3090, which `config.yaml`'s own defaults correctly mark
`resident: false` for (GPU0 is a `swap: true` group by design, see
existing entry #3 below). If `warmup_resident_models`'s
`asyncio.gather` fires pings for all of them concurrently, GPU0 likely
thrashes/deadlocks trying to satisfy three mutually-exclusive residency
demands, which could stall the whole gather (Python's `asyncio.gather`
doesn't fail-fast per-task in a way that would explain zero logs from
the *independent* CPU-resident models unless something upstream — the
`llm.chat()` httpx call itself — hangs without ever raising). **Next
session: (1) revert `coder`/`coder-small`/`vision` to
`resident: false` in `settings.local.yaml` to match `config.yaml`
intent, restart, and check whether `warm-up complete` lines start
appearing for the CPU models; (2) if they still don't, add a per-call
`asyncio.wait_for` timeout around each `warmup_one` ping so one hung
model can't silently swallow the others' completion, and log entry/exit
explicitly (right now there's no "attempting warm-up" log, only
completion/failure, so a hang before either is invisible).**

### B. Browser-tab-switching bug (issue #5 in the 2026-08-05 entry below) — still open, not yet re-investigated this session
User confirms this is still reproducing. No new investigation done this
session beyond re-confirming it's not fixed — see existing entry #5
below for the known architecture gap (aborted SSE stream never persists
partial content).

### C. Scroll bug still reproducing despite the `requestAnimationFrame` fix
The `chat.ts` fix from the prior session (wrapping `sc.scrollTop =
sc.scrollHeight` in `requestAnimationFrame`) did not resolve what the
user is seeing. That fix only addressed a *timing* race (assignment
before layout); it does **not** address issue #8 in the 2026-08-05 entry
below (auto-scroll fighting a manual scroll-up during streaming, because
every token still force-scrolls unconditionally) — that's almost
certainly the actual bug the user is still hitting, since #8 was never
fixed, only the unrelated rAF timing issue was. **Next session: implement
the "only stick to bottom if already within N px of it" fix described in
#8**, not another timing tweak.

### D. Rolling summary never produces a summary message, and a `first_token_timeout` breaks the chat entirely — needs live repro
User's repro: chat titled around "show current system time" hit a
`first_token_timeout` error, and **no summary message appeared after
the error**. Two distinct things tangled together here, neither
confirmed root-caused yet:
- `first_token_timeout` breaking the chat outright suggests the SSE
  error path doesn't leave the conversation in a recoverable state —
  worth checking whether `orchestrator.py`'s error handling for that
  specific `LLMError` kind persists anything to SQLite or just aborts,
  and whether the frontend shows a retry affordance or just silently
  breaks (user says "the chat broken" — get the exact visible symptom:
  stuck spinner? console error? messages gone?).
- Separately, per existing entry #7 below, summaries were already known
  to be **generated server-side but never surfaced in the chat UI at
  all** — so "no summary message after this error" may be expected
  today regardless of the timeout (there was never a UI element for it
  to appear in). Don't conflate "summary didn't visibly appear" (#7,
  known, unfixed) with "summary generation itself failed because of the
  timeout" (unconfirmed) — check `chats.summary` in SQLite directly for
  that chat_id to tell which one actually happened before assuming a new
  bug.

**Priority suggestion for next session:** A (the real-loaded-state wiring
+ the warm-up hang) and D (chat-breaking error) are the sharpest —
A because the `settings.local.yaml` triple-resident drift could be
actively causing GPU contention beyond just cosmetics, D because a
"broken chat" is a hard stop for the user, not a cosmetic bug. C has a
known fix already written up (#8 below), just not applied yet.

## Where things stand (2026-08-05 — live UI bug sweep, NOT YET FIXED)

User ran a live test pass against the running app on `ailab` and reported
10 issues in one batch. This session investigated the code for each one
to classify it before any fixes land — **nothing below is fixed yet**,
this is triage only. Ordered roughly by severity/confidence.

### 1. Classifier fallback: `chat — fallback: timeout` — CONFIRMED, needs live repro
`routing.classifier.timeout_s` is `6.0` (`config.yaml`), `classifier` is
`resident:true` (`ttl: 0` in the generated `llama-swap.yaml`), so it
should already be loaded and answering in ~0.9-1.1s warm per the docs'
own numbers (`app/router/classifier.py` docstring). A hard 6s timeout
firing on a resident model suggests either: the classifier process
crashed/isn't actually up (check `curl :8080/health`, check
`llama-swap`'s own process list, not just the config file), CPU
contention from a concurrent `utility` job (both are CPU-resident and
share the same CPU budget — a summary job mid-flight could starve the
classifier), or general box load. **Next step: reproduce live and check
`journalctl`/llama-swap logs for the classifier process at the moment of
a timeout**, not a code fix — nothing in `classifier.py` looks wrong on
read-through.

### 2. GPU inventory spam in Debug — FIXED (trace spam removed, gpu/api.py:69-73)
`web/src/views/debug.ts:367` polls `/api/gpu/inventory` every 5s
(`gpuTimer = setInterval(refreshTelemetry, 5000)`) while the Debug view
is mounted, and `app/gpu/api.py:71`'s `get_inventory()` calls
`new_trace()` + a `gpu_inventory` span **on every single poll**. Every
5s the Debug view is open, a brand-new trace gets created and pushed to
the top of the traces list — this is why it looks like "a loop... new
entries every few seconds." Fix: telemetry polling shouldn't mint a new
trace per call; either stop tracing routine inventory polls entirely, or
reuse one trace_id for the polling session instead of `new_trace()` each
time.

### 3. "Chat-default reloading" every message — LIKELY NOT A BUG, is `gpu0-main` swap-group behavior
GPU0 runs `chat-default`/`coder`/`coder-small`/`vision` in one
`swap: true` group (`llama-swap.yaml`) because the 3090 can't hold two
~24GB big models at once — **only one of those four can be resident on
GPU0 at a time by design** (`PLAN.md` §4.1, Config B). If the user
alternates between a chat message and a code message, GPU0 swaps
chat-default out and coder in, then back — that's real VRAM-constrained
swapping, not a bug, and "check which model is loaded, skip reload"
isn't possible for that group without more VRAM. **If this is instead
reproducing on *consecutive* plain-chat messages with no coder/vision
message between them**, that would be a real bug — needs a live repro
with the exact message sequence, because nothing in `orchestrator.py`
forces a reload of an already-resident model.

### 4. Copy-to-clipboard button visible but doesn't copy — FIXED (clipboard `execCommand` fallback, markdown.ts:87-99)
`navigator.clipboard.writeText()` (`web/src/markdown.ts`, added this
session) requires a **secure context** — HTTPS or `localhost`. The app
is served over plain HTTP at `http://192.168.0.89:8000` (LAN IP, not
`localhost`), so `navigator.clipboard` is `undefined` there and the
button's click handler throws before `writeText` ever resolves/rejects,
silently doing nothing. **This needs a fallback path** (e.g.
`document.execCommand("copy")` via a hidden textarea, or catching the
`undefined` case explicitly and showing "unsupported" instead of hanging
silently). Also "should be instant" — current 1.5s "Copied!" revert
delay is probably fine, but the real complaint is likely just "nothing
visibly happens," which is the secure-context failure, not the delay.

### 5. Navigating away mid-stream and back loses the response — FIXED (error-path partial persistence, orchestrator.py:481-503)
`chat.ts`'s `unmount()` calls `abort?.abort()`, killing the in-flight SSE
connection. The assistant's streamed content only gets persisted to
SQLite in `orchestrator.py`'s `db: persist_assistant_message` span,
which runs **after** the stream completes normally — an aborted stream
never reaches it. So navigating away mid-generation and back reloads
history from the DB, which never got the partial (or even complete, if
the abort raced the final chunk) answer. This is an architecture gap,
not a one-line fix: either persist partial content on abort, or make
generation resumable/backgrounded independent of the view's lifecycle
(the latter matches how the title/summary background jobs already work
— worth reusing that pattern).

### 6. `reasoner`/`reasoner-alt` → 404 "no router for requested model" — FIXED (`reasoner`→canonical swap-name alias, orchestrator.py:227-250,381)
`PLAN.md` §4.1 / `swapgen.py`'s own docstring say `reasoner` is
"*same blob as chat-default*... enabled at the request layer, not a
separate swap entry — routing chat→reasoner costs zero load time."
`swapgen.py::_select_entries` correctly dedupes `reasoner` out of the
generated `llama-swap.yaml` (verified live — `reasoner` has no entry in
`/home/john/llm-stack/serving/llama-swap/config.yaml`). **But nothing
resolves the request-time model name.** `orchestrator.py:213`
(`resolved_model = result.model`) passes the literal string `"reasoner"`
straight to `llm_client.chat(model=resolved_model, ...)`
(`orchestrator.py:241`), and that goes straight to llama-swap, which has
no `reasoner` key → 404. The `thinking=model_entry.thinking` flag *is*
correctly threaded through separately, but the model name itself is
never translated to `chat-default`. **This is the actual fix needed:**
when `resolved_model`'s `ModelEntry.resident` collapsed it into another
canonical entry during swapgen, the orchestrator (or router) needs to
send the *canonical swap-slot name* to llama-swap while still using the
original entry's `thinking`/`max_tokens`/etc. Same root cause explains
`reasoner-alt`, though that one is additionally `enabled: false` in
`config.yaml` — it wouldn't work even with the alias fix until re-enabled.

### 7. Rolling summaries "don't seem to happen" — IMPLEMENTED SERVER-SIDE, NEVER SURFACED IN UI
`app/background/summaries.py` is real and wired: `maybe_enqueue_summary`
fires every `cfg.background.summary_every_n_turns` user turns, writes to
`chats.summary` in SQLite. **Confirmed: zero references to it anywhere
in `web/src/**`** — nothing fetches or displays `chats.summary`. This is
exactly the "built but not injected" failure mode `CLAUDE.md`/`PLAN.md`
call out as a rejected-PR condition — the backend half shipped without
the frontend half. Needs: an API surface for the summary (if `/api/chats`
doesn't already return it — check) and a UI spot to show it (Debug span
already exists at `summary` stage, so it's visible *there*, just not in
the chat view itself).

### 8. Auto-scroll fights manual scroll-up during streaming — CONFIRMED BUG
`chat.ts` calls `renderMessages(true)` on nearly every SSE token during
streaming (lines ~197-227), and `scroll=true` unconditionally does
`sc.scrollTop = sc.scrollHeight` every time. So scrolling up mid-stream
gets yanked back to the bottom on the very next token, which reads as
"can't scroll down" (you're fighting a forced re-snap every ~50-100ms
during generation). Standard fix: only force-scroll if the user was
already within some threshold (e.g. `scrollHeight - scrollTop -
clientHeight < 80`) of the bottom *before* the update — i.e. "stick to
bottom" only while already at the bottom, never yank someone back who
scrolled away on purpose.

### 9. No stop/interrupt button for in-flight generation — FIXED (stop button wired, composer.ts:152, chat.ts:381-389)
`chat.ts` already has an `AbortController` (`abort`) wired to cancel the
SSE stream on unmount, but there's no UI control that calls
`abort.abort()` while staying on the same view — `composer.ts`'s layout
only has a `send-btn`, no stop/cancel button. The plumbing (abort
controller, SSE cancellation) already exists; this is a real UI gap, not
a backend one. Wiring a stop button through the existing `abort` would
also need to (a) not lose the partial content already streamed (see #5 —
same underlying persist-on-abort gap) and (b) flip the composer back to
its idle state.

### 10. Model appears to not see full session history — LIKELY MODEL BEHAVIOR, PLUMBING VERIFIED CORRECT
`app/chat/history.py::build_llm_messages` returns **every** prior
message in the chat as OpenAI-format turns, called fresh before every
`llm_client.chat()` call (`orchestrator.py:239`), and the span already
records `message_count`/`messages` for the Debug view to show — this
matches what the user says they saw in Debug. The wiring looks correct
on read-through. Two real considerations, neither of which is a "send
fewer messages" bug: (a) **no system prompt is ever prepended** —
`build_llm_messages` only returns bare user/assistant turns, no framing
that tells the model it's a persistent app receiving full history each
call, which is exactly the kind of ambiguity a raw local model can
misinterpret and answer generically about "session storage." (b) small/
local models (this roster tops out at 35B-A3B) are meaningfully worse at
this kind of self-referential meta-question than frontier hosted models
even with correct context — this may just be a model-quality ceiling, not
a bug. **Recommend verifying via the Debug prompt tab on the exact turn
that produced this answer** (need `debug.store_prompts: true` per the
span's own doc comment) before assuming there's a plumbing bug — the
code path itself looks right.

### Also flagged, not yet triaged in depth (verify against `PLAN.md`/`docs/FEATURES.md` before treating as bugs)
- **Debug doesn't show if a model is ejected/evicted from a swap slot.**
  No code found that surfaces llama-swap eviction events at all — likely
  not yet implemented (`docs/FEATURES.md` doesn't call out an eviction
  event either), flag as a feature request, not a regression.
- **Tokens/sec + response time next to the model name in chat (LM
  Studio-style).** `PLAN.md` §4.16 says the Debug window is supposed to
  show real tok/s from llama.cpp's `timings`, and `debug.ts:253` already
  renders `predicted_per_second` **inside the Debug span detail view** —
  so the data is captured and shown *somewhere*, just not inline in the
  chat bubble next to the model name as requested. That inline placement
  is a real, reasonable feature request, not a bug — needs `msg.usage`/
  `msg.timings` threaded from the `done` payload into `ChatMsg` and
  rendered in the `msg-meta` row (`chat.ts` ~line 83-104 already has the
  right spot for it, just needs the fields).
- **Debug doesn't show model "thinking" in the stream.** Confirmed at the
  type level: `app/types.py::ChatDelta` has no `reasoning_content` field
  at all, and nothing in `llm_client.py` reads `reasoning_content` off
  llama.cpp's stream deltas. `PLAN.md` §4.2 says thinking tokens "arrive
  inside `token` and are tagged in the `done` payload" — that description
  doesn't match what's actually implemented; reasoning content isn't
  captured anywhere server-side yet. This is a real gap against the
  written contract, not a UI-only fix — needs `ChatDelta.reasoning_content`,
  threading through `llm_client.chat()`, and a Debug-view field to render
  it, in that order.

### Not yet verified live (deploy status unclear)
- **The 2026-08-02 background-shutdown-hang fix (`_STOP_DRAIN_TIMEOUT_S`,
  bug #4 in the entry below) — user reports the hang is STILL happening.**
  The service was restarted 2026-08-05 per this session's earlier work, so
  the fix should be live, but if the hang is still reproducing, don't
  assume the original diagnosis (uncapped background-job drain) was
  complete — re-check `app/background/__init__.py::stop()` end-to-end
  against a live repro rather than assuming the prior fix covers it. Get
  the exact symptom (how long, what's it doing, does it eventually
  recover or need a kill -9) before re-diagnosing.

**Nothing in this entry has been fixed yet — this is triage only, next
session should work through these roughly in the order listed (6 and 4
are the sharpest, highest-confidence root causes; 1 and 3 need live
repro before code changes; the "also flagged" section is scoping work,
not urgent bugs).**

## Where things stood (2026-08-02, later same day — CRITICAL ROUTER BUG) — FIXED (router kwargs, orchestrator.py:317-325)

**The Phase-2 router classifier has never actually run on live chat
traffic, since it was first wired.** `app/chat/orchestrator.py` called
`app.router.route()` as `_route(chat_row, text, attachments)` — no
`llm_client`, `config`, or `trace_id` kwargs. `route()`'s signature is
`route(chat, text, attachments, *, llm_client=None, config=None,
trace_id=None)`, so `llm_client` silently defaulted to `None` on every
real turn, and Layer 3 (the classifier) hit:
```
if llm_client is None:
    logger.warning("router: no llm_client for classifier, using fallback")
    return _fallback_result(fallback_model, elapsed(), "error")
```
instantly, every time — `source="classifier"`, `intent="chat"`,
`confidence=None`, ~0.1ms latency. **Every message that didn't match
Layer 1 (override) or Layer 2 (2+-word keyword rules) silently fell back
to `chat-default`**, no matter what it asked for. This is why "code me a
script" never routed to `coder`. `scripts/eval_router.py`'s 93.33% pass
(logged in the entry below) never caught this because it calls the
classifier directly, bypassing the orchestrator entirely — **that eval
number was real but was never proof the live app used it.**

Fixed in `app/chat/orchestrator.py`: `_route(...)` now passes
`llm_client=self.llm_client, config=self.config` (not `trace_id` — the
orchestrator already wraps the call in its own `span(trace_id, "route",
...)`, so passing it too would double-emit a span for the same stage).

**Why tests didn't catch it:** `tests/test_settings_api.py::
test_orchestrator_uses_router` monkeypatched `_route` with a fake whose
signature was `async def fake_route(chat, text, attachments) -> RouteResult`
— no `**kwargs`, no `llm_client`/`config` params. That fake *worked*
specifically because the orchestrator never passed those args — the test
would have hard-failed with a `TypeError` the moment the real call site
tried to pass them, which is exactly what happened when this got fixed
(it briefly broke, was corrected to accept the real kwargs). **Lesson:
a test double with a narrower signature than the real callee can mask a
missing-argument bug indefinitely — stubs should accept `**kwargs` or
mirror the real signature exactly, not just "whatever the code happens to
call it with today."** Added `test_no_override_forwards_llm_client_and_config_to_router`
in `tests/test_chat_sse.py` as an explicit regression test for this exact
failure mode.

**Second bug found while chasing a live 404 ("no router for requested
model"):** `app/gpu/swapgen.py::_select_entries` dedupes models sharing a
GGUF file (`chat-default` / `reasoner` share one blob) with documented
priority "resident:true over non-resident; ties broken by first in list."
The old code was `elif m.resident: file_to_canonical[m.file] = m.name` —
which re-fires for *every* later resident entry, so if both `chat-default`
and `reasoner` end up `resident:true` (as happened live, via
`settings.local.yaml` drift), `reasoner` — being later in `config.models`
— silently clobbers `chat-default` as the swap-slot survivor and
`chat-default` vanishes from `llama-swap.yaml` entirely. Fixed by
tracking whether the current canonical is already resident and only
letting a later entry displace it if it isn't. Regression test:
`test_both_resident_ties_keep_first_in_list` in `tests/test_swapgen.py`.

**Note (2026-08-05): this fixed a *different* 404 than issue #6 above.**
That earlier bug was about `settings.local.yaml` drift causing
`chat-default` itself to disappear from the swap config. Issue #6 above
is a separate, still-unfixed bug: `reasoner`/`reasoner-alt` are *supposed*
to be deduped away (that's correct, by design) but nothing translates
the request-time model name to the surviving canonical entry.

**Third: `PUT /api/settings/routing` 422'd on `classifier`.**
`RoutingConfig` (`app/config.py`) has `rules`, `attachments`, `intents`,
**and** `classifier`, but `RoutingPatch` (`app/settings/api.py`) only
declared `rules`/`intents` with `extra: forbid` — any Settings-UI save
that included classifier fields (model/timeout_s/confidence_threshold/
fallback_model) 422'd. Added `classifier: dict[str, Any] | None` to
`RoutingPatch` and a matching deep-merge branch in
`app/settings/store.py::update_routing` (mirrors the existing `intents`
handling).

**Fourth: `systemctl restart ai-mega-app` can appear to hang ~15-40s.** — FIXED (shutdown drain 8s, background/__init__.py:26,54-63)
Not a deadlock — `app/background/__init__.py::stop()` deliberately awaits
any in-flight background job before letting the app exit (title jobs are
fast via `dispatcher`, but a summary job on CPU `utility` is documented at
17.6-40s, `PLAN.md` §5). No timeout existed, so a restart right after a
chat turn (especially the 6th, which triggers a summary) could silently
block for tens of seconds with systemd's default 90s `TimeoutStopSec` as
the only backstop. Added a 15s bounded wait
(`_STOP_DRAIN_TIMEOUT_S`) with `asyncio.wait_for` + a warning log +
`BackgroundQueue.cancel()` fallback, so shutdown degrades visibly instead
of silently stalling. **User reports this is STILL reproducing as of
2026-08-05 — see "Not yet verified live" above, re-open this.**

**Fifth (not a bug, but confusing UX, worth documenting):**
`RoutingRule.keywords` rejects single-word entries by design (`app/config.py`
`_each_keyword_is_multi_word` validator) — word-boundary keyword rules are
deliberately restricted to 2+ word phrases to avoid false-positive
substring matches (`PLAN.md:228`, frozen decision). A user trying to add
`"file"` as a rule keyword gets a `ConfigError` — expected; use `"add
file"`/`"open file"` etc. instead.

**Sixth: live overlay drift that triggered bug #2 above.**
`routing.intents.tool_call_needed` had been set to `utility` via the
Settings UI at some point this session — wrong choice (`utility` is
CPU-bound, budgeted for background jobs at 17-40s/call; using it for a
live tool-calling turn would make every such message feel broken-slow).
Reverted to `chat-default` on the live overlay. `reasoner.resident` was
also `true` on the overlay (should be `false` — it shares chat-default's
blob and doesn't need its own residency); reverted.

**Status at time of writing:** all 6 fixes above are code-complete,
106 tests passing, `tsc --noEmit` clean. **`app/background/__init__.py`
and `app/background/queue.py` (fix #4) have NOT been deployed yet** — the
last `systemctl restart` predates that edit. A restart is needed before
the shutdown-timeout fix is live. The orchestrator/swapgen/settings fixes
(#1-3) were deployed and live-verified via curl before this note was
written.

**The user is planning to have a more powerful model do a deeper audit
of the router before further fixes** — given how long bug #1 went
undetected (the classifier layer effectively never ran since Phase 2
merged), treat the router/orchestrator seam as under-tested and don't
assume anything else in that path is correct without checking.

## Where things stood (2026-08-02, earlier)

**Phase 2 exit criteria are now both confirmed on live hardware (ailab):**
- `scripts/eval_router.py --base-url http://127.0.0.1:8080/v1 --min-accuracy 90`
  → **93.33% (126/135)**, passes the ≥90% gate. (Note: the `--base-url`
  must include `/v1` — `config.yaml`'s `llama_swap.base_url` does too —
  passing the bare host:port 404s every classifier call and silently
  routes everything to `chat`, which looks like a router bug but isn't.)
- GPU-reassignment demo: `PUT /api/settings/models/coder-small {"gpu":1}`
  → `POST /api/gpu/apply` → verified `/home/john/llm-stack/serving/llama-swap/config.yaml`
  updated (`CUDA_VISIBLE_DEVICES=1`) and `ai-mega-app.service`
  `InvocationID` unchanged (no restart) in ~34ms. Reverted back to gpu:0
  after.

**Test-isolation gap fixed (`e7a1a30`):** `tests/test_config.py`'s two
`ModelEntry` new-field tests didn't monkeypatch `OVERLAY_PATH` like the
rest of the suite does, so they silently depended on no
`settings.local.yaml` existing at repo root. Once the user actually used
the Settings UI (writing a real overlay), those two tests started failing
against real model paths/`resident` values instead of their own fixture
data. Fixed to match the existing pattern (see `test_settings_api.py`
`_isolate` fixture for the canonical version). **General lesson:** any
new test calling `load_config()`/`get_config()` with a custom path must
also monkeypatch `config_mod.OVERLAY_PATH` (and `store_mod.OVERLAY_PATH`
if touching the settings store) or it's implicitly coupled to whatever
happens to be in the real overlay file that day.

Full verification gate (`pytest`, `tsc --noEmit`, real `tsc` rebuild) is
green; `web/js/**` matches `web/src/**` exactly (only the settings_models
GPU-name-in-label diff, now committed as `e7a1a30`).

## Where things stood (2026-07-31)

All 7 Phase 2 branches are merged to `main` (`config-schema`,
`router-classifier`, `gpu-swapgen`, `background-utility`, `router-eval`,
`settings-api`, `settings-ui`). This was the user's **first live test**
of the app end-to-end (previously all fake-data/unit-test verified).
Two real bugs surfaced during that live test and are now fixed and
pushed (`fac6c8e`, `655f67d`). `docs/PHASE_PROMPTS.md` Phase 2 exit
criteria not yet run on live hardware: the GPU-reassignment reload demo
and `scripts/eval_router.py --min-accuracy 90` against the real
classifier. Don't assume those passed — nobody has run them yet.

`package-lock.json` is untracked at repo root (shows in `git status`).
Nobody has decided whether it should be committed or gitignored — ask
before doing either.

## Deployment model — read this before debugging "it's not updating"

- The FastAPI backend on `ailab` (`app/main.py`) mounts `web/` via
  `StaticFiles(..., html=True)` and serves files **straight off disk**.
  There is no build/bundle/deploy step for the frontend at request time.
- This means: editing `web/src/**` requires running `npx tsc` (real
  build, not just `--noEmit`) and the output lands in `web/js/**`,
  which **is checked into git** (not gitignored) and must be committed
  alongside the `.ts` source. A merge or edit that touches `web/src/**`
  without rebuilding `web/js/**` ships stale JS silently — this bit the
  `p2/settings-ui` merge earlier this session.
- Once `web/js/**` and `web/css/**` are correct on disk, they are
  **immediately live** — no `systemctl restart ai-mega-app` needed,
  just a browser hard-refresh (Ctrl+Shift+R) to bust cache. Only
  `app/**` (Python) changes require a service restart.
- The Windows laptop is a pure browser client hitting
  `http://192.168.0.89:8000` — nothing to `git pull` there, ever.
  **This also means `navigator.clipboard` (secure-context-only) fails
  there — see issue #4 above, this bit the copy-button feature the same
  day it shipped.**

## Two real bugs found + fixed this session (patterns worth remembering)

1. **`fac6c8e` — Settings view laid out as a vertical stack instead of
   rail+panel side-by-side.** Root cause: `settings.ts` does
   `el.className = "settings-view"` on the *same element* that has
   `id="view"`. `app.css` pins `#view { flex-direction: column }` by
   ID. An ID selector always beats a class selector regardless of
   source order or later declarations — so `.settings-view`'s own
   `flex-direction` never won, no matter what I set it to, until I
   scoped the override as `#view.settings-view` (ID+class beats bare
   ID). **General lesson: if a view reuses a shared host element by ID
   and re-styles it via a class, any property the ID rule also sets
   needs an `#id.class` override, not a bare `.class` one.**

2. **`655f67d` — Debug view showed the raw `chat_id` UUID as the trace
   row's "name", and defaulted to the wrong trace on open.** The title-
   generation background job (`app/background/titles.py`) calls
   `new_trace(chat_id)` — a **separate trace_id** from the actual chat
   turn's trace, sharing only `chat_id`, with exactly one span
   (`stage="title"`, `model=<title_model, usually "dispatcher">`).
   Traces are listed most-recent-first, and the title job runs *after*
   the turn it's titling, so it was almost always `traces[0]` — meaning
   opening Debug right after a chat showed a lone "title" span instead
   of the real turn. Fixed by resolving `chat_id` against the store's
   already-fetched `chats` list for display, and by auto-selecting the
   first trace that isn't title-only. **General lesson: any background
   job that calls `new_trace()` on the same `chat_id` as a live turn
   will outrank that turn in "most recent" ordering — the debug UI (or
   future features) needs to account for that whenever it assumes
   `traces[0]` means "the turn I just ran."**

## Tooling notes

- **Playwright is installed as a devDependency but Chromium can't
  launch on this box** — `chrome-headless-shell` needs `libasound.so.2`
  which isn't installed, and installing it needs
  `sudo npx playwright install-deps chromium` (the user approved this
  once this session but sudo needed an interactive terminal and never
  actually completed — this is still not installed). **Firefox works
  out of the box with no missing libs** (`npx playwright install
  firefox` once, then `const { firefox } = require('@playwright/test')`)
  — use Firefox for any future visual verification on this box unless
  someone installs the Chromium system deps.
- To actually verify a frontend fix instead of guessing from reading
  CSS: launch Firefox via Playwright against `http://127.0.0.1:8000`
  (this box is `ailab`, so localhost is the live app), screenshot, and/or
  `page.evaluate(() => el.getBoundingClientRect())` /
  `getComputedStyle(el)` to get real computed layout — this is how both
  bugs above were actually root-caused, not guessed at from screenshots
  alone. Guessing from a screenshot without checking the DOM/CSS wasted
  significant time before this was tried.
- `#/debug` opens a long-lived SSE connection
  (`streamDebugSpans`/`/api/debug/stream`), so
  `page.goto(..., { waitUntil: "networkidle" })` **times out** — use
  `waitUntil: "load"` plus a fixed `waitForTimeout` instead.
