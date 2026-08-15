# Feature requests (not yet scoped into PLAN.md/FEATURES.md)

Ideas raised by the user during live use, kept separate from `docs/FEATURES.md`
(Part B, F1-F19 — those are the frozen, planned spec items) so they don't get
mistaken for already-decided work. Nothing in this file is approved or
scheduled; each item needs an owner decision + a PLAN.md/FEATURES.md update
before implementation, per `CLAUDE.md`'s "ask first" rule for anything
touching `config.yaml` keys or frozen contracts.

## 1. Slash commands in chat

**Request (2026-08-11):** typing `/summarize` (or similar) in the composer
should trigger an action directly, rather than everything going through the
plain chat/tool-routing path.

**Why this doesn't exist today:** `docs/PHASE_PROMPTS.md` and `PLAN.md` don't
define any slash-command surface — the composer is plain-text-in,
SSE-stream-out (`PLAN.md` §4.2), and the rolling summary
(`app/background/summaries.py`) is purely automatic, firing every
`background.summary_every_n_turns` user turns with no manual trigger. This
was confirmed by grep across `PLAN.md`/`docs/FEATURES.md`/`docs/PHASE_PROMPTS.md`
— zero mentions of "slash command" anywhere.

**Shape, if built:**
- Composer-side parsing (`web/src/composer.ts`) for a leading `/word` token,
  distinct from the message text sent to the backend — needs a decision on
  whether commands are client-side-only (e.g. `/summarize` calls a new
  `POST /api/chats/{id}/summarize` directly, bypassing the turn-count
  cadence) or routed through the existing chat/SSE path with a new
  server-side command layer.
- `/summarize` specifically would need a force-enqueue path in
  `app/background/summaries.py` (currently only `maybe_enqueue_summary`,
  gated on `turn_count % every_n == 0`) — an on-demand variant that ignores
  the cadence.
- Other candidate commands (not requested yet, just the obvious set once the
  mechanism exists): `/clear`, `/title`, `/model <name>` (would overlap with
  the existing model-picker override at `app/chat/history.py::set_model_override`).
- Needs a debug span per command invocation (rule `004-observability`) and
  tests, same as any other feature — not exempt from the verification gate.

## 2. `@` to reference local files in chat

**Request (2026-08-11):** typing `@` in the composer should let the user
reference a local file (path/filename) to pull into context, similar to
IDE-style `@file` mentions.

**Why this doesn't exist today:** the current attachment model
(`docs/FEATURES.md`, `config.yaml`'s `attachments:` section) is upload-based
(image/code_file → routed via `routing.attachments`), not a live filesystem
reference. There's no filesystem-browse-and-inline mechanism in the composer
today.

**Shape, if built:**
- Composer-side `@` trigger with an autocomplete dropdown — needs a backend
  endpoint to list/search files under an allowed root (almost certainly
  scoped to `projects/<id>/` per the existing filesystem-first projects
  design in `PLAN.md`, **not** arbitrary filesystem access — this is a real
  security boundary decision, not just a UI feature).
- Needs a decision on how the referenced file's content enters the prompt
  (inlined verbatim, chunked/embedded via the existing `rag/` +
  `VectorStore` pipeline, or read fresh at send time), and whether large
  files get truncated/summarized before inlining.
- Overlaps conceptually with `tools/file_ops` (already listed as an enabled
  tool per the live chat screenshot taken this session) — worth checking
  whether `@`-mention should just be a UI shortcut that emits the same
  tool-call the model could already make, rather than a parallel code path.

## 3. Larger context window

**Request (2026-08-15):** user wants bigger context lengths across the
model roster — current conversations feel like they run out of room too
fast.

**Current numbers (`config.yaml`, as of this entry):** `chat-default`/
`reasoner` 32768, `reasoner-alt` 8192, `coder` 16384, `coder-small` 8192,
`vision` 8192, `dispatcher` 4096, `utility` 8192, `embed` 2048, `classifier`
4096. All are per-model `ctx:` keys, config-only (no code change needed to
raise them) — but each is an "ask first" change per `CLAUDE.md`'s
config-file-discipline rule, since `ctx` is a `config.yaml` key.

**Tradeoffs to weigh before approving specific numbers:**
- KV-cache memory scales with `ctx` — for GPU0 swap-group models
  (`chat-default`/`reasoner`, `coder`, `coder-small`, `vision`), a bigger
  `ctx` eats into the 24GB 3090's headroom shared across the whole
  `gpu0-main` swap group; the box only has one 3090, so this is a real
  budget, not free.
- CPU-resident models (`utility`, `embed`, `classifier`) hold their KV
  cache in system RAM (64GB total) — cheaper headroom, but these are also
  the ones already found to be CPU-thread-contended under load (see
  `docs/HANDOFF.md`'s 2026-08-11 session-5 entry and the `--threads`
  capping fix) — a bigger `ctx` here increases per-token compute, not just
  memory, which interacts with that same contention issue.
- `reasoner-alt` (DeepSeek-R1-32B) is already the most memory-heavy
  non-resident model at only 8192 ctx; raising it costs more per-swap VRAM
  than a same-size bump to a smaller model would.
- Raising `first_token_timeout_s`/`routing.classifier.timeout_s` may also
  need revisiting if bigger contexts measurably slow prompt processing on
  a cold swap — these were already bumped once for underestimated cold-load
  latency (see `config.yaml`'s own inline comments), a `ctx` increase could
  reopen that margin.

**Not implemented — this is a logged request only,** pending an owner
decision on which models get how much more context and a live VRAM/RAM
check before changing `config.yaml`.

## Not yet decided

- Whether either feature is in scope before or after the current
  `docs/FIX_PLAN_2026-08-11.md` Wave 2 items land.
- Whether commands/mentions are markdown-visible in the persisted message
  text or stripped before storage.
