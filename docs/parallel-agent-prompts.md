# Parallel Agent Prompts (human copy-paste)

**For you, not for agents to ingest.** Paste these into new Cursor Agent chats when running a parallel wave by hand. The per-phase task list and FILE SCOPEs live in `docs/PHASE_PROMPTS.md` — this file is the *mechanics*: how to set up worktrees, what a builder/integrator prompt looks like, and the Cursor 3 patterns that keep a wave safe.

Cursor auto-loads the always-apply rules (`001-stack`, `002-boundaries`); the git rule (`007-git-worktrees`) is agent-requested — name it in the prompt so it loads.

---

## One folder = one branch (read this first)

Every Agent chat in the same Cursor window/folder shares **one** `git checkout`. If one agent switches branches, **all** chats in that folder follow — commits land on the wrong branch. **Parallel tasks therefore require git worktrees: one folder per task, one Cursor window each.**

### Setup (from the main clone on `main`)

```bash
git pull origin main
git worktree add ../AI-Mega-App-p1-llm-client   -b p1/llm-client   main
git worktree add ../AI-Mega-App-p1-chat-sse      -b p1/chat-sse     main
git worktree add ../AI-Mega-App-p1-web-shell     -b p1/web-shell    main
```

Open each `../AI-Mega-App-*` folder in its own Cursor window (**File → Open Folder**). Run one builder chat per window. Or use Cursor's native worktree option in the Agents Window, which creates the isolated folder for you.

Cleanup when a branch is merged: `git worktree remove ../AI-Mega-App-p1-llm-client`.

Branch/worktree naming: branches `p<phase>/<task>`, folders `../AI-Mega-App-p<phase>-<task>` (matches `docs/PHASE_PROMPTS.md`).

---

## Wave workflow

1. **Plan Mode first (Shift+Tab).** For any multi-file task, plan read-only, review the plan, save it to `.cursor/plans/`. Plan with a high-capability model (Composer 2 / Claude Opus); execute with a faster one (Composer 1.5 / Sonnet).
2. **Dependency order.** Run the "Wave 1 (sequential)" task that defines shared files/interfaces *first* and merge it before the parallel wave — worktrees prevent file conflicts, not semantic ones (an interface change one agent makes that another consumes).
3. **One builder chat per worktree window.** Paste the builder template below.
4. **Integrator chat** on the main clone only — audits every branch, merges on your say-so.
5. You: optional UI smoke test; approve merges.

### Agent plan order (enforce in the prompt)

| Order | Step |
|---|---|
| **1 — FIRST** | Git pre-flight only (`git status`, `git branch --show-current`, `git log main..HEAD`) — **no code edits** |
| 2 … n−1 | Implementation inside FILE SCOPE |
| **n — LAST** | `pytest` + `tsc --noEmit`, commit, completion report — **no new code after** |

If a plan starts with an edit before git pre-flight, reject it and ask for a reorder.

---

## Optional: write the contract first

For a wave with any overlap risk, drop a contract at `.cursor/agent-contracts/<task-id>.md` before starting (see `docs/CURSOR_RULES.md` §9). It makes review deterministic — the integrator checks the diff against the contract's FILE SCOPE, not taste.

---

## Builder prompt (template)

```markdown
You are a **builder** agent for the AI Mega App. Follow `.cursor/rules/007-git-worktrees` exactly, plus the always-on 001-stack / 002-boundaries. Architecture source of truth: @PLAN.md. Commits are mandatory (FILE SCOPE below).

## Workspace
This Cursor window is an isolated worktree for ONE task.
- Expected folder: <e.g. ../AI-Mega-App-p1-llm-client>
- Expected branch: `p1/<task-name>`
- Do NOT `git checkout` any other branch in this shared checkout.

## Plan order (mandatory)
1. FIRST: git pre-flight only — report results before any code change.
2. Implementation — FILE SCOPE only.
3. LAST: `python -m pytest -q --basetemp=.pytest-tmp/run` (repo root) + `npx tsc --noEmit`, commit, completion report.

## Task
<one-paragraph description; @-reference the relevant docs/FEATURES.md spec and any interface files>

## Branch
`p1/<task-name>` — already checked out in this worktree (created from `main`). If it's missing, STOP and report — do not checkout another branch in a shared clone.

## FILE SCOPE
- <explicit paths — the only files you may edit>

## FORBIDDEN
- Files outside FILE SCOPE (except minimal test fallout in a separate `fix(...)` commit)
- Frozen interfaces in @AGENTS.md; generated files (`llama-swap.yaml`, `web/js/**`)
- `git checkout`/`git switch` to other branches; merge, rebase, cherry-pick, push
- Unrelated changes bundled into one commit

## Acceptance
- Behavior: <what to demo>
- Every new pipeline stage writes a debug span; every SSE stream ends with `done`/`error`
- Tests: `python -m pytest -q --basetemp=.pytest-tmp/run` from repo root + `npx tsc --noEmit`
- Manual UI: deferred to human unless the task is UI E2E

## When done
Commit `feat(<task>): <message>`. Completion report per 007: branch, commits vs `main`, `git diff main..HEAD --name-only`, pytest/tsc result, overlap notes.
```

---

## Integrator prompt

Run from the **main clone only** (on `main`, no parallel builders active in that folder — the integrator may `git checkout` each branch to audit).

```markdown
You are an **integrator** for the AI Mega App. Follow `.cursor/rules/007-git-worktrees`. Read-only audit first — merge only if I ask.

## Branches
1. `p1/<branch-a>` — <task>
2. `p1/<branch-b>` — <task>

Per branch: `git log main..HEAD --oneline` (reject stacked branches), `git diff main..HEAD --name-only` (vs that task's FILE SCOPE), checkout + `python -m pytest -q --basetemp=.pytest-tmp/run` + `npx tsc --noEmit`.
Deliver a table: branch | commits | files | pytest | tsc | merge-ready (yes/no/cherry-pick) | notes.
Flag semantic/interface drift between branches. Merge/push only when I say so.
```

---

## Human manual UI (optional)

Per worktree: `uvicorn` from repo root + serve `web/` (tsc watch). Use different ports if running multiple worktrees at once. CI and unit tests run against a fake llama-swap, so no GPU is needed to verify a wave — only live-hardware checks (`scripts/preflight.py`) need the box.
