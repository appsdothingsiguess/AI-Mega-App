# Parallel agent prompts (human copy-paste)

**For you, not for agents to ingest.** Paste these into new Cursor Agent chats. Agents already load `.cursor/rules/008-git-discipline.mdc` automatically.

## Option A workflow

1. **One builder chat per task** (2–4 chats) — paste **Builder prompt** below, fill `<…>`
2. Wait for each builder’s `008` completion report
3. **One integrator chat** — paste **Integrator prompt**, list all branches
4. You: optional manual UI smoke tests, approve merges

---

## Builder prompt

```markdown
You are a **builder** agent for Prompter X. Follow `.cursor/rules/008-git-discipline.mdc` exactly (always-applied). Commits are mandatory when this prompt includes a FILE SCOPE.

## Task
<one-sentence description of the fix or feature>

## Branch
`phase1/<task-name>` — create from `main` ONLY (never from another task branch).

## FILE SCOPE (only these files may change in feat commits)
- <path/one>
- <path/two>

## FORBIDDEN
- Editing files outside FILE SCOPE (except minimal test fallout per 008, separate commit)
- `git checkout -b` from any branch other than `main`
- merge, rebase, cherry-pick, push
- Bundling unrelated fixes in one commit

## Acceptance
- Behavior: <what should work when done>
- Tests: `python -m pytest -q --basetemp=.pytest-tmp/run` from repo root (required)
- Baseline: 240 passed / 2 failed is OK unless you introduce **new** failures
- Manual UI: deferred to human (do not start uvicorn/npm unless this task explicitly says so)

## Before any edits (008 pre-flight)
1. `git status` && `git branch --show-current`
2. Branch setup per 008 (new branch from `main` if needed)
3. `git log main..HEAD --oneline` must be empty on a new branch, or only this task's commits

## When done
Commit all in-scope work. Completion report per 008:
- branch name
- `git log main..HEAD --oneline`
- `git diff main..HEAD --name-only`
- pytest results (note new vs baseline failures)
- manual UI: deferred to human
- merge notes / overlap warnings
```

---

## Integrator prompt

```markdown
You are an **integrator** agent for Prompter X. Follow `.cursor/rules/008-git-discipline.mdc`. Read-only audit first — do not merge/push unless I explicitly ask.

## Branches to audit
1. `phase1/<branch-a>` — task: <short description>
2. `phase1/<branch-b>` — task: <short description>
(add 3–4 as needed)

## Per branch (run in order)
1. `git log main..HEAD --oneline` — reject **stacked** branches (multiple unrelated commit subjects)
2. `git diff main..HEAD --name-only` — compare to that task's declared FILE SCOPE
3. `git checkout <branch>` && `python -m pytest -q --basetemp=.pytest-tmp/run`
4. Mark merge-ready: yes / no / cherry-pick-only (list SHAs)

## Merge plan (propose only — wait for my approval)
- Order: <branch first, … last>
- Shared-file risk: `App.tsx`, `app/main.py`, `app/chat_orchestrator.py`
- Do NOT merge kitchen-sink branches whole — cherry-pick unique commits only
- Pytest on `main` after each merge

## Deliverable
Table: branch | commits | files | pytest | merge-ready | notes

Do not implement features unless fixing merge-conflict fallout I approve.
```

---

## Example: four parallel tasks

| Chat | Branch | FILE SCOPE hint |
|------|--------|-----------------|
| 1 | `phase1/<task-a>` | backend-only |
| 2 | `phase1/<task-b>` | frontend nav |
| 3 | `phase1/<task-c>` | one component + tests |
| 4 | `phase1/<task-d>` | schemas + orchestrator |

Then one integrator chat listing all four branches.

---

## Human manual UI testing (optional)

Agents use pytest only. When you want to click through the UI:

```bash
# Terminal 1 — repo root
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2
cd web && npm run dev
```

Browser: **http://localhost:5173/**

### Parallel testing without branch switching

```bash
git worktree add ../prompter-<task> phase1/<task-branch>
```

Each worktree: own uvicorn + `npm run dev`.
