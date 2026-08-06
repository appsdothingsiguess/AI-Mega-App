# Handoff notes (agent-only)

Working notes for whichever Claude Code session picks this up next.
Not a planning doc, not user-facing — just context that isn't obvious
from the code alone. Delete or trim entries once they're stale.

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

### 2. GPU inventory spam in Debug — CONFIRMED BUG
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

### 4. Copy-to-clipboard button visible but doesn't copy — CONFIRMED REGRESSION (I introduced this)
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

### 5. Navigating away mid-stream and back loses the response — CONFIRMED GAP
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

### 6. `reasoner`/`reasoner-alt` → 404 "no router for requested model" — CONFIRMED BUG, root cause found
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

### 9. No stop/interrupt button for in-flight generation — CONFIRMED MISSING FEATURE
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

## Where things stood (2026-08-02, later same day — CRITICAL ROUTER BUG)

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

**Fourth: `systemctl restart ai-mega-app` can appear to hang ~15-40s.**
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
