# Parallel agent prompts (human copy-paste)

**For you, not for agents to ingest.** Paste into new Cursor Agent chats. Agents load `.cursor/rules/008-git-discipline.mdc` automatically.

## One folder = one branch (read this first)

Every Agent chat in the same Cursor window/folder shares **one** `git checkout`. If any agent switches branches, **all** chats see it — commits and WIP land on the wrong branch.

**Parallel tasks require git worktrees** — one folder per task, one Cursor window each.

### Setup (before opening builder chats)

From your main clone on `main`:

```bash
git pull origin main
git worktree add -b phase1/cot-thinking-display ../megaapp-cot main
git worktree add -b phase1/markdown-tool-rendering ../megaapp-md main
git worktree add -b phase1/project-isolation-prompt-refresh ../megaapp-iso main
```

Open **File → Open Folder** for each `../megaapp-*` path. Run builder prompts only in that window.

Cleanup when done:

```bash
git worktree remove ../megaapp-cot
```

Alternative: Cursor **`/worktree`** in Agent chat (creates isolated folder automatically).

---

## Option A workflow

1. **Worktrees** — one per task (above)
2. **One builder chat per worktree window** — paste builder prompt
3. **Integrator chat** — main clone on `main` only; audits all branches
4. You: optional UI smoke tests; approve merges

---

## Agent plan order (required)

Tell the agent (or enforce in prompt):

| Order | Step |
|-------|------|
| **1 — FIRST** | Git pre-flight only (`status`, correct branch, `git log main..HEAD`) — **no code edits** |
| 2 … n−1 | Implementation in FILE SCOPE |
| **n — LAST** | Pytest, commit, `008` completion report — **no new code after this** |

If the agent’s plan starts with “edit MessageBubble” before git pre-flight, **reject the plan** and ask it to reorder.

---

## Builder prompt (template)

```markdown
You are a **builder** agent for Prompter X. Follow `.cursor/rules/008-git-discipline.mdc` exactly. Commits are mandatory (FILE SCOPE below).

## Workspace
This Cursor window is an isolated worktree for **one** task.
- Expected folder: <e.g. ../megaapp-cot>
- Expected branch: `phase1/<task-name>`
- **Do NOT run `git checkout` to any other branch.**

## Plan order (mandatory)
1. **FIRST:** 008 pre-flight only — report results before any code change
2. Implementation (FILE SCOPE only)
3. **LAST:** pytest, commit, completion report

## Task
<description>

## Branch
`phase1/<task-name>` — already checked out in this worktree (user created worktree from `main`). If branch is missing, **stop and report** — do not checkout another branch in a shared clone.

## FILE SCOPE
- <paths>

## FORBIDDEN
- Files outside FILE SCOPE (except minimal test fallout, separate commit)
- `git checkout` / `git switch` to other branches
- merge, rebase, cherry-pick, push
- Unrelated fixes in one commit

## Acceptance
- Behavior: <…>
- Tests: `python -m pytest -q --basetemp=.pytest-tmp/run` from repo root
- Baseline: 240 passed / 2 failed OK unless **new** failures
- Manual UI: deferred to human

## When done
Commit with: `feat(<task>): <message>`
Completion report per 008 (branch, `git log main..HEAD`, files, pytest, merge notes).
```

---

## Example: CoT thinking (your prompt — worktree-adjusted)

```markdown
You are a **builder** agent for Prompter X. Follow `.cursor/rules/008-git-discipline.mdc` exactly. Commits are mandatory.

## Workspace
Worktree folder: `../megaapp-cot` — branch `phase1/cot-thinking-display` must already be checked out here.
**Do NOT `git checkout` other branches.**

## Plan order (mandatory)
1. **FIRST:** 008 pre-flight — report before any edits
2. Backend + frontend implementation
3. **LAST:** pytest, commit `feat: render CoT thinking blocks in chat UI`, completion report

## Task
CONTEXT: @web/src/components/MessageBubble.tsx @app/chat_orchestrator.py

Bug #6: CoT reasoning is debug-only (`llm_reasoning`). Add user-visible thinking UI.

BACKEND: Emit `{"type": "thinking", "content": "..."}` when reasoning exists (not gated on sse_trace).

FRONTEND: MessageBubble — collapsible "Thinking" block above response; client.ts event type if missing.

## Branch
`phase1/cot-thinking-display` — worktree already on this branch from `main`.

## FILE SCOPE
- app/chat_orchestrator.py
- web/src/components/MessageBubble.tsx
- web/src/api/client.ts (thinking event type only if missing)

## FORBIDDEN
- protocols.py, types.py, router.py, project_manager.py, App.tsx
- git checkout to other branches; merge/rebase/cherry-pick/push

## Acceptance
- deepseek-r1: collapsible Thinking block; no empty block without reasoning; main stream OK
- pytest from repo root; manual UI deferred

## When done
008 completion report.
```

---

## Integrator prompt

Run from **main clone only** (`AI Megaapp` on `main`). Integrator may `git checkout` each branch to audit — **no parallel builders running in that folder**.

```markdown
You are an **integrator** for Prompter X. Follow `008`. Read-only audit first — merge only if I ask.

## Branches
1. `phase1/<branch-a>` — <task>
2. `phase1/<branch-b>` — <task>

Per branch: `git log main..HEAD`, `git diff main..HEAD --name-only`, checkout + pytest.
Reject stacked branches. Table: branch | commits | files | pytest | merge-ready | notes.
```

---

## Human manual UI (optional)

Per worktree: uvicorn (repo root) + `npm run dev` in `web/`. Use different ports if running multiple worktrees.
