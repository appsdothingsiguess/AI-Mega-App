# Handoff notes (agent-only)

Working notes for whichever Claude Code session picks this up next.
Not a planning doc, not user-facing — just context that isn't obvious
from the code alone. Delete or trim entries once they're stale.

## Where things stand (2026-08-02, later same day — CRITICAL ROUTER BUG)

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
of silently stalling.

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
