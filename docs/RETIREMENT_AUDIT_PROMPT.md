# Retirement Audit Prompt — hand this to an agent session

**Purpose of this file:** a self-contained prompt to paste into a fresh agent
session (Claude Code, Pi, or otherwise) to produce the actual AI Mega App →
Pi.dev retirement plan. This file is the prompt itself — everything below the
horizontal rule is meant to be copied and given to that agent verbatim (or
run via `pi -p @docs/RETIREMENT_AUDIT_PROMPT.md` / similar). Above the rule is
context for a human deciding whether to run it.

**Why a separate agent, not this session continuing:** the audit is a large,
mostly-mechanical read-and-classify pass over the whole repo — a good fit for
a dedicated pass rather than folding into an already-long design conversation.
The prior conversation (`docs/PI_PLUGIN_BRAINSTORM.md`) already made the
*design* decisions this audit should assume as settled — don't re-litigate
them, execute against them.

**Known-unverified inputs the audit should NOT treat as settled fact:** the
Qwen3.5-9B GGUF weight size (estimated 5.06 GiB, unconfirmed — HF was blocked
from the session that produced the estimate) and its real decode tok/s on
GPU1 (bandwidth-scaled estimate, not measured). Both are called out again
below so the executing agent re-verifies rather than propagates the estimate
as fact.

---

## Prompt (copy from here down)

You are auditing `AI-Mega-App` for retirement of its agent/UI layer in favor
of Pi.dev (`@earendil-works/pi-coding-agent`), while keeping its services
layer. This is a **planning and classification task, not an execution task**:
produce a written plan the owner (Joey) reviews and approves before any file
is moved, deleted, or rewritten. Do not delete, move, or rewrite anything in
this pass — the deliverable is a document.

### Read first, in this order

1. `CLAUDE.md` — the project's own operating rules. Follow them for the
   *audit itself* (verification gate, boundaries, worktree discipline,
   config-file discipline) even though this pass produces a plan, not code.
2. `AGENTS.md`, `PLAN.md` §1 and §5 — why the old `app/`/`web/` codebase
   failed, and what "current phase" means.
3. `docs/AGENT_CONTEXT_MEGA.md` and `docs/HANDOFF.md` — read the
   **"2026-08-31 — Pi.dev pivot"** entry in `HANDOFF.md` specifically; it's
   the decision record for this whole retirement.
4. `docs/PI_PLUGIN_BRAINSTORM.md` — the design doc this audit executes
   against. It already decided: which capabilities move to Pi extensions vs.
   skills vs. prompt templates vs. nothing; that goosedump owns compaction +
   durable memory (not a custom `pi-memory` plugin); that the smart router
   is NOT worth rebuilding as a classifier (thinking-level routing only);
   that sub-agents split into a cheap serialized case (build first) and an
   optional GPU1 concurrent case (gated on two unverified numbers, see
   below); that `nexu-io/open-design` (Apache-2.0, 162 skills) is the design
   answer, not a custom plugin; and a specific shortlist of `L3tum/little-coder`
   extensions (`write-guard`, `security`, `checkpoint`, `compatibility`,
   `quality-monitor` — adopt; `subagent`+`evidence`+`lsp`+`extra-tools` —
   evaluate as one bundle; `token-limit-guard`, `model-preserve`,
   `tool-gating`/`turn-cap`/`finalize-warn`/`knowledge-inject` — skip, with
   reasons). Treat that doc as binding design intent; your job is to turn it
   into a concrete move/retire/rewrite list against the actual current repo
   tree, not to re-decide it.

### Task 1 — Classify every file/directory under `app/` and `web/`

For each top-level module (chat orchestrator, router, `tools/`, `rag/`,
`memory/`, `gpu/`, `debug/`, frontend views, etc.), assign one of:

- **RETIRE** — no Pi equivalent needed; delete. State why (e.g., "Pi's own
  agent loop replaces this").
- **KEEP AS SERVICE** — stays exactly where it is, unmodified; it's
  infrastructure, not agent/UI. Cross-check against the "What stays" list in
  `PI_PLUGIN_BRAINSTORM.md` (llama-swap, relay, benchmarks, ops scripts, GPU
  inventory, Qdrant if a future plugin needs it).
- **MIGRATE → Pi extension/skill/prompt-template/package** — name the exact
  target tier (per the brainstorm doc's tier model) and the exact
  destination path convention (`.pi/extensions/<name>/`,
  `~/.pi/agent/skills/<name>/SKILL.md`, `~/.pi/agent/prompts/<name>.md`).
  Flag anything you'd classify differently than the brainstorm doc implies,
  with your reasoning — don't silently override it.
- **UNCLEAR — ask** — genuinely ambiguous; list the specific question for
  Joey rather than guessing.

Produce this as a table: path → classification → target (if migrating) →
one-line reason.

### Task 2 — Documentation pass

For every doc in the pointer hierarchy (`PLAN.md`, `docs/FEATURES.md`,
`docs/PHASE_PROMPTS.md`, `docs/design-doc.md`, `.cursor/rules/*.mdc`,
`docs/CURSOR_RULES.md`, `AGENTS.md`, `CLAUDE.md` itself, `docs/HANDOFF.md`,
`docs/AGENT_CONTEXT_MEGA.md`), determine:

- Does it describe something being retired? → needs a retirement notice
  (pattern: how `CLAUDE.md` already marks `app/` as "post-mortem, not a
  foundation" — follow that convention, don't invent a new one).
- Does it describe a service that survives? → needs no change, or a small
  pointer update if its neighbors are retired.
- Is it now fully superseded (e.g., a whole `.cursor/rules/*.mdc` file that
  only existed to constrain the retired frontend)? → recommend archival, not
  silent deletion — name where it should be archived to (a `docs/archive/`
  convention, or whatever this repo already uses for superseded docs — check
  first, don't invent one).

Output: a table of doc path → verdict → specific edit needed (or "archive").
Do not make the edits — describe them precisely enough that a follow-up pass
can apply them mechanically.

### Task 3 — Gate the two unverified numbers before touching GPU1

`PI_PLUGIN_BRAINSTORM.md`'s sub-agent section is explicit that the GPU1
worker-slot path is gated on two measurements that were estimated, not run:

1. Actual Qwen3.5-9B Q4_K_M GGUF file size (`ls -l` after download) — decides
   whether utility-gpu's slot leaves headroom or not.
2. Real decode tok/s on GPU1 via
   `CUDA_VISIBLE_DEVICES=1 python3 scripts/bench_server.py --label coder-gpu1 --model <gguf> --model-class coder-small --ctx 32768`.

If this audit runs on `ailab` (check `hostname` per `CLAUDE.md`'s own
guidance), run both and report the real numbers instead of the doc's
estimates. If not on `ailab`, state plainly that these remain unverified and
must be run before any GPU1 reconfiguration — do not propagate the estimate
as if it were confirmed.

### Task 4 — Produce the final deliverable

A single markdown document, `docs/RETIREMENT_PLAN.md`, containing:

1. The Task 1 classification table (full repo coverage — every top-level
   module accounted for, nothing skipped silently).
2. The Task 2 doc-update table.
3. Task 3's verified (or explicitly still-unverified) numbers.
4. An ordered move list: what literally happens, in what order, with
   rollback notes for anything destructive (e.g., "archive `app/` to a
   branch/tag before deleting" — per `CLAUDE.md`'s git safety rules, never
   force-push or delete without the owner's explicit go-ahead).
5. An explicit "not touched by this plan" section restating the services
   that survive untouched, so a reviewer can sanity-check nothing essential
   got swept in by accident.

### Constraints (from this repo's own `CLAUDE.md` — follow them)

- Never hand-edit generated files (`llama-swap.yaml`, `web/js/**`).
- Never `git add -A`/`.`, force-push, or delete without explicit request —
  this pass doesn't touch git state at all beyond reading it.
- Ask first on anything touching `.cursor/`, CI, or hooks.
- A described blocker (an unclear file, a missing doc convention, an
  unverified number) is success. An improvised guess in its place is not —
  stop and list it as an open question instead.
- If you find yourself building tooling to do this audit (scripts, parsers)
  before you've actually read and classified anything, stop — that's
  scaffolding replacing the actual synthesis work. Read and classify by hand;
  this is a one-time pass, not a recurring pipeline.

Do not begin implementation. Stop after producing `docs/RETIREMENT_PLAN.md`
and report a summary (counts per classification, biggest open questions) —
Joey reviews and approves before anything moves.
