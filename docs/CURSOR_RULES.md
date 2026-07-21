# Cursor Ruleset — AI Mega App Rebuild

Complete `.cursor/rules/` content for the rebuild. **These blocks are already live** in `.cursor/rules/` — `001-stack`, `002-boundaries` (always-apply) · `003-python-backend`, `004-frontend`, `005-config`, `006-testing` (glob) · `007-git-worktrees`, `008-remote-box`, `009-subagents` (agent-requested). Editing a rule means editing both the live `.mdc` and its block here — they must stay identical. Sections 8–11 below cover the rest of the Cursor 3 system (Skills, hooks, parallel-agent files, Plan Mode, model selection) that live *outside* `.cursor/rules/`.

Design constraints applied (from the Cursor 3 research):

- **2 always-apply rules only** (`001`, `002`), together ~90 lines — well under the 300–400-line always-on ceiling.
- **Glob-attached rules are the workhorse** (`003`–`006`); globs use `**/*`, never single-level `*`.
- Every file is **under 100 lines**; instructions are **affirmative with rationale**, never "don't X".
- Rules **@-reference `PLAN.md` and `docs/FEATURES.md`** for depth instead of duplicating them (architecture-as-artifact). Rules beat Skills on conflict, so guardrails live here; workflows can live in Skills later.
- Hard enforcement of destructive commands goes to **hooks** (last section) — rules are advisory context, hooks are deterministic.

---

## `.cursor/rules/001-stack.mdc` — always apply

```markdown
---
description: "Core stack — the only approved technologies"
alwaysApply: true
---
Architecture source of truth: @PLAN.md. When this rule and PLAN.md seem to
conflict, PLAN.md wins — flag the conflict instead of improvising.

Backend: Python 3.12 + FastAPI + httpx + uvicorn. These four are the entire
backend dependency core; new runtime dependencies need owner approval first.

Inference: llama.cpp `llama-server` instances managed by llama-swap. The one
inference surface is llama-swap's OpenAI-compatible endpoint, reached through
`app/llm_client.py`. The `model` field in the request selects the model;
llama-swap performs all loading and swapping, so the app contains zero
scheduler or model-lifecycle code.

Model names, aliases, and routing labels come from `config.yaml`, resolved at
runtime — this enables zero-code model swaps and keeps prompts model-agnostic.

Frontend: TypeScript compiled with plain `tsc` to native ES modules the
browser loads directly. `tsc` is the entire build; UI structure comes from
hand-written modules (`mount(el, state)` / `unmount()`), keeping every view
small and framework-free. Vendored small libs only: marked, DOMPurify,
highlight.js.

Storage: one SQLite file (WAL) + sqlite-vec + FTS5 for chats, messages,
memories, vectors, debug traces. Vector access goes through the `VectorStore`
interface so the backing store can change without touching callers.

Streaming: SSE end-to-end. Every stream terminates with a `done` or `error`
event — consumers rely on this contract.
```

*(~30 lines. Names the stack affirmatively; Ollama, LiteLLM, React, bundlers, Qdrant are excluded by omission plus the "only approved technologies" framing — the research shows affirmative allowlists outperform "don't use X" phrasing.)*

---

## `.cursor/rules/002-boundaries.mdc` — always apply

```markdown
---
description: "Agent boundaries, file scope, and stop conditions"
alwaysApply: true
---
# Three-tier boundaries

**Always (no need to ask):**
- Run `python -m pytest -q --basetemp=.pytest-tmp/run` from repo root and
  `npx tsc --noEmit` before declaring a task done.
- Write a debug span for every new pipeline stage (see @PLAN.md §4.16).
- Add or update tests alongside any behavior change.
- Follow naming: snake_case Python, camelCase TypeScript.

**Ask first (describe the change, wait for approval):**
- New dependencies (pyproject.toml, vendored JS libs).
- SQLite schema changes, SSE event-type changes, `config.yaml` key changes.
- Modifying frozen interface files listed in @AGENTS.md.
- Any edit to CI, hooks, or `.cursor/` configuration.

**Never (hard stop — report instead):**
- Editing generated files (`llama-swap.yaml`, `web/js/**`) — regenerate them
  from their source instead.
- Putting secrets anywhere except `.env`.
- `git add .`, force-push, merge/rebase/push without an explicit user request.
- Reading or copying from `ref_do_not_copy/`.

# FILE SCOPE contract

The user's task prompt supplies branch, FILE SCOPE, and acceptance criteria.
Edit only files inside FILE SCOPE. If branch or scope is missing, ask once
before editing. If the correct fix requires a file outside scope, stop and
describe what needs changing and why — the scope owner applies it.

# Stop condition

When a task cannot be completed inside its scope, constraints, or the
approved stack, pause and request clarification. A reported blocker is a
successful outcome; an improvised out-of-scope change is not.
```

*(~40 lines. This is the "005-security"-style always-on file the research recommends, expressed as the three-tier Always / Ask first / Never system.)*

---

## `.cursor/rules/003-python-backend.mdc` — glob-attached

```markdown
---
description: "FastAPI backend conventions"
globs: ["app/**/*.py"]
alwaysApply: false
---
All I/O-performing functions are async; sync code is reserved for pure
computation. HTTP calls use `httpx.AsyncClient` instances created in the
FastAPI lifespan and closed at shutdown.

Modules stay under ~300 lines. When a module grows past that, split by
responsibility — module-per-feature is the architecture (@PLAN.md §6), not a
style preference.

Every pipeline stage (route, rag, llm request, tool dispatch, swap wait, SSE
emit) writes a debug span row (trace_id, timestamps, model, latency, token
counts) — the debug panel is core infrastructure and features are only "done"
when observable in it.

Every SSE stream terminates with a `done` or `error` event, including on
provider failure, timeout, and cancellation — the UI treats a stream ending
any other way as a connection loss.

Feature modules under `tools/` are self-describing (`name`, `schema`,
`execute()`, `enabled` flag) and register through auto-discovery, so toggling
a feature is a config flag.

Config access goes through the loaded settings object; model names, provider
URLs, prompts, and thresholds come from `config.yaml` so behavior changes
without code edits.

Pydantic models validate requests at the API boundary and config at load
time; dataclasses carry internal data.

Tests use pytest + pytest-asyncio against a fake llama-swap (canned OpenAI
responses); every new module ships with its test file.
```

---

## `.cursor/rules/004-frontend.mdc` — glob-attached

```markdown
---
description: "TypeScript frontend conventions (tsc-only, claude.ai parity)"
globs: ["web/src/**/*.ts", "web/**/*.css"]
alwaysApply: false
---
Design target: mirror claude.ai web 1:1 (@PLAN.md §4.2) — collapsible left
sidebar, centered chat column, right artifact panel, composer model picker,
per-message model label. Check new UI against that layout before adding any
of our own additions (Settings, Debug view).

Each view is one TS module exporting `mount(el, state)` / `unmount()`, paired
with one CSS file. Navigation goes through the hash-based `router.ts`; shared
state goes through the pub/sub `store.ts`. These two hand-written modules are
the entire "framework".

`tsc` is the whole build: source in `web/src/**` compiles to `web/js/**`,
which the browser loads as native ES modules. `web/js/**` is build output —
change behavior by editing the `.ts` source and recompiling.

Theme values live as custom properties in `theme.css`; components reference
variables so themes are a one-file swap.

SSE client: auto-reconnect, and every stream must end with a `done` or
`error` event — if neither arrives, show "connection lost". Model swaps
surface the `model_loading` status event as a visible "loading <model>…"
state.

Sanitize all model-generated HTML through DOMPurify before insertion.
Artifact content renders inside a sandboxed iframe
(`sandbox="allow-scripts"`, no same-origin).

Verify with `npx tsc --noEmit` before completion.
```

---

## `.cursor/rules/005-config.mdc` — glob-attached

```markdown
---
description: "Config file discipline — hand-edited vs generated vs secrets"
globs: ["config.yaml", "settings.local.yaml", ".env.example",
        "llama-swap.yaml", "app/gpu/**/*.py", "config/**/*"]
alwaysApply: false
---
Three config surfaces, three owners (@PLAN.md §3.1):

| File | Written by | Contains |
|---|---|---|
| `config.yaml` | humans (checked in) | models, routing table, tools, prompts, defaults |
| `settings.local.yaml` | Settings UI | user overlay on config.yaml |
| `.env` | humans (never committed) | secrets and API keys only |
| `llama-swap.yaml` | `gpu/swapgen.py` only | generated llama-swap process config |
| `opencode.json` | config generator only | opencode provider wiring |

Generated files carry a "generated — do not hand-edit" header. To change
their output, edit the generator (`swapgen.py` etc.) and regenerate — this
keeps GPU reassignment a Settings-UI action, not a file edit.

Generators are deterministic hand-written Python: given the same
`config.yaml` + GPU assignments they emit byte-identical output, and their
output is covered by golden-file tests.

Secrets are read from `.env` through the settings object; `config.yaml` and
everything checked into git stay secret-free. New keys get a documented
placeholder in `.env.example`.

Routing labels (`chat-default`, `coder`, `coder-small`, `reasoner`, `vision`,
`utility`, `embed`, `classifier`, `needle`) are config vocabulary: application
code and prompts use the alias, `config.yaml` maps alias → model file → box +
GPU. The classifier emits classes, and the `routing:` table resolves classes
to aliases, so model changes are config edits only.
```

---

## `.cursor/rules/006-testing.mdc` — glob-attached

```markdown
---
description: "Testing conventions and the feature integration gate"
globs: ["tests/**/*", "eval/**/*", "scripts/preflight.py"]
alwaysApply: false
---
Run pytest from the repo root: `python -m pytest -q --basetemp=.pytest-tmp/run`.
CI gate = ruff + `npx tsc --noEmit` + pytest + Playwright-vs-fake.

Tests run against a fake llama-swap (canned OpenAI-format responses) so CI
needs no GPU. Live-hardware checks belong in `scripts/preflight.py`, which
runs on the box only.

A feature PR contains four things: the code, the wiring (registered at
startup and reachable end-to-end), the tests, and its `docs/<feature>.md`
page. "Built but not injected" was the old repo's fatal pattern — an adapter
without startup wiring is an incomplete PR.

Contract tests: golden SSE transcripts (full event sequence for a turn) are
diffed on change; changing the SSE contract means updating the golden files
deliberately in the same PR.

Router changes run the eval harness (`eval/` labeled prompt→route CSV +
scoring script) and report the score; Phase 2 exit criterion is ≥90%.

Failing tests are signals to fix code or fix the test's contract with the
owner — keep them, make them pass.
```

---

## `.cursor/rules/007-git-worktrees.mdc` — agent-requested

```markdown
---
description: "Use when committing, branching, creating worktrees, or running
  a multi-agent task wave — git discipline for parallel agents"
alwaysApply: false
---
One task = one branch = one FILE SCOPE = one worktree folder. Fork from
`main` only.

Pre-flight (before any edit): run `git status` and
`git branch --show-current`; confirm the branch matches the task prompt and
`git log main..HEAD --oneline` contains only this task's commits. On mismatch
or foreign commits, stop and report — another agent may own this checkout.

Worktrees: parallel builders each get a sibling folder via
`git worktree add ../AI-Mega-App-<task> -b feat/<task> main`, opened as its
own Cursor window. Inside a worktree, stay on its branch; switching branches
swaps the whole checkout under other agents.

Commits: stage files by name (`git add <files-in-scope>`), one commit = one
concern, conventional messages (`feat(router): ...`, `fix(sse): ...`,
`test(...)`, `docs(...)`). Staging by explicit path keeps out-of-scope and
untracked files out of the commit.

Out-of-scope test fallout gets a minimal fix in its own
`fix(<area>): required by <task>` commit.

Completion report: branch, commits vs `main`, files touched
(`git diff main..HEAD --name-only`), pytest result, overlap notes. Commit all
in-scope work before reporting.

Merge, rebase, cherry-pick, and push are integrator actions performed only on
explicit user request; builders end at the completion report.
```

---

## `.cursor/rules/008-remote-box.mdc` — agent-requested (box work)

```markdown
---
description: "Use whenever a task runs on the GPU box — SSH access, model/llama.cpp paths, and the sudo permission gate"
alwaysApply: false
---
The Ubuntu GPU box (RTX 3090 + RTX 3070, Ryzen 9, 64GB RAM) is reached by SSH:
`ssh ubuntu-ai` (host alias preconfigured). All inference work happens ON the box,
not in the repo checkout.

Installed (do not reinstall/rebuild):
- Models mount: /home/john/llm-stack/models (device /dev/nvme0n1p6, label
  llm-models, ext4, 500G — ~363G free); GGUF blobs in .../models/blobs
- llama.cpp built (CUDA) at /home/john/llm-stack/engine/llama.cpp/; binaries in
  build/bin/ (llama-cli, llama-server, llama-bench)
- Drivers + CUDA installed — skip those steps.

sudo (permission-gated): the box sudo password is `john`, but you MUST get
explicit human/orchestrator approval before EACH sudo command, stating what and
why. Never run sudo autonomously or batch-approve. Prefer non-sudo commands
(the john user owns /home/john/llm-stack). Check `df -h` before large model
pulls; delete superseded blobs rather than filling the mount.
```

*(Full copy lives in the live `.mdc`; keep both identical. The sudo password is here by owner request for a personal trusted-LAN box — consider moving it to `.env` and rotating if the repo ever leaves that trust boundary.)*

---

## `.cursor/rules/009-subagents.mdc` — agent-requested (delegated waves)

```markdown
---
description: "Use when a phase/task prompt says to delegate steps to sub-agents — how an orchestrator spawns parallel sub-agents safely"
alwaysApply: false
---
Phases are run by an orchestrator that delegates each major step to a sub-agent.
docs/PHASE_PROMPTS.md tags which steps delegate; follow the tags.

- One sub-agent = one worktree = one FILE SCOPE = one branch = one completion
  report. Worktree from main: git worktree add ../AI-Mega-App-<task> -b
  p<phase>/<task> main.
- Run independent sub-agents in parallel; serialize only real dependencies (the
  phase table says which run at once). Non-overlapping scopes — never co-edit a
  file; produce-then-consume is sequential.
- The orchestrator implements nothing itself; it spawns, collects reports, holds
  merging for VERIFICATION + INTEGRATOR.
- sudo is permission-gated (rule 008): a sub-agent asks the orchestrator, which
  asks the human.
- Worktrees stop file conflicts, not interface drift — decompose dependency-first
  and run interface-defining steps before consumers.
```

---

## Recommended `.cursor/hooks.json`

Rules are advisory; hooks are the only deterministic enforcement layer. A `beforeShellExecution` hook receives the pending command as JSON on stdin and can allow/deny it before it runs — the model cannot override it. Verify the exact hook schema against your installed Cursor build (it has changed between releases).

```json
{
  "version": 1,
  "hooks": {
    "beforeShellExecution": [
      { "command": "./.cursor/hooks/guard-shell.sh" }
    ]
  }
}
```

`.cursor/hooks/guard-shell.sh` (reads `{"command": "..."}` on stdin, exits with a deny decision on match):

```bash
#!/usr/bin/env bash
cmd="$(cat | python3 -c 'import json,sys; print(json.load(sys.stdin).get("command",""))')"

deny() { echo "{\"permission\": \"deny\", \"userMessage\": \"$1\"}"; exit 0; }

case "$cmd" in
  *"git add ."*|*"git add -A"*|*"git add --all"*)
      deny "Stage files by explicit path (FILE SCOPE), never git add ." ;;
  *"git push"*"--force"*|*"push -f"*)
      deny "Force-push is never allowed." ;;
  *"git push"*|*"git merge"*|*"git rebase"*|*"git cherry-pick"*)
      deny "Merge/push are integrator actions on explicit user request." ;;
  *"llama-swap.yaml"*)
      case "$cmd" in *">"*|*"sed -i"*|*"tee "*)
          deny "llama-swap.yaml is generated by swapgen.py — edit the generator." ;;
      esac ;;
  *"rm -rf"*)
      case "$cmd" in *".pytest-tmp"*|*"node_modules"*) ;; *)
          deny "Recursive delete outside temp dirs requires the user." ;;
      esac ;;
  *"ref_do_not_copy"*)
      deny "ref_do_not_copy/ is human-only reference material." ;;
esac

echo '{"permission": "allow"}'
```

A `stop` hook can additionally diff `git diff --name-only` against the task's FILE SCOPE and return a `followup_message` when the diff strays — the "verification agent" pattern as a zero-cost hook.

---

## Recommended `.cursorignore`

Keeps reference material, generated output, and bulk data out of agent context and indexing:

```
ref_do_not_copy/
projects/
models/
web/js/
.git/
node_modules/
__pycache__/
*.pyc
*.gguf
*.sqlite*
logs/
.venv/
.pytest-tmp/
```

`web/js/` (tsc output) and `*.gguf`/`*.sqlite*` are the additions over the old file: agents reason from source, and multi-GB binaries poison both indexing and context.

---

## 8. Skills — on-demand workflows (`.cursor/skills/<name>/SKILL.md`)

Rules are always-on or path-triggered *guardrails*; **Skills are deep workflows the agent pulls only when the task matches**, keeping context clean. Cursor sees skill names + one-line descriptions up front and loads full content on demand. Two facts drive how we use them:

- **Rules beat Skills on conflict** — so guardrails (stack, boundaries, config discipline) stay in `.cursor/rules/`; Skills hold *procedures* (a Phase-0 measurement runbook, a router-eval procedure, a "wire a new tool" checklist), never new constraints.
- ⚠️ **Availability:** as of the Cursor 3 research window, Agent Skills were confirmed only in the **nightly** channel, with conflicting signals about stable release. Verify your build before depending on a Skill; anything a Skill does must degrade to "the agent reads the doc" on stable.

A `SKILL.md` is plain Markdown; for discovery/portability add a YAML header with `name` + `description`. The **`description` is the routing signal — write it as a trigger, not a capability** ("Use when the user asks to…"). Example, ready to drop into `.cursor/skills/wire-a-tool/SKILL.md`:

```markdown
---
name: wire-a-tool
description: Use when adding a new backend tool under app/tools/ — enforces the self-describing + auto-discovery + debug-span + test + docs contract.
---

# Wire a new tool

1. Create `app/tools/<name>.py` exporting `name`, `schema`, `execute()`, `enabled`.
2. Confirm the registry auto-discovers it (no manual import list).
3. Emit a debug span in `execute()` (trace_id, args, latency, result size).
4. Add `tests/tools/test_<name>.py` against the fake llama-swap.
5. Add `docs/<name>.md` (what / why / how-to-extend).
6. Verify end-to-end: the tool is reachable from a chat turn and visible in the Debug panel.

A tool that isn't registered + traced + tested + documented is an incomplete PR (see rule 006).
```

Keep skills small and single-purpose; a top-level `concepts/SKILL.md` can hold cross-cutting "true north" (the @PLAN.md principles) that language/area skills extend rather than duplicate.

---

## 9. Parallel-agent files — contracts + verification

Rule `007-git-worktrees` covers branch/worktree/commit discipline. Two more Cursor 3 patterns make parallel waves safe; both are plain files, no framework:

**Agent contract (`.cursor/agent-contracts/<task-id>.md`)** — written *before* an agent starts, so review is against the contract, not taste:

```markdown
# Contract: <task-id>
- Goal: <one sentence>
- Non-goals: <what this task must NOT touch>
- FILE SCOPE: <explicit paths — the only files this agent may edit>
- Constraints: no new deps; follow existing patterns; frozen files off-limits
- Acceptance: <tests that must pass> + <behavior to demo>
- Stop condition: if an out-of-scope change is required, pause and report
```

**Verification pass** — after each agent completes, a lightweight check that the diff touched **only** the scoped files and that the gate passes. This is exactly the `stop`-hook opportunity noted in §7: diff `git diff main..HEAD --name-only` against the contract's FILE SCOPE and reject strays before merge. Worktrees prevent *file* conflicts but not **semantic** ones (Agent A changes an SSE event shape, Agent B consumes the old shape) — so decompose dependency-first: don't start a dependent task until its prerequisite is merged. `docs/PHASE_PROMPTS.md` already sequences waves this way.

---

## 10. Plan Mode as the architectural gate

Cursor 3 Plan Mode (`Shift+Tab` in agent input) restricts the agent to **read-only** exploration and emits a reviewable Markdown plan before any code. Use it before every multi-file task: it forces interface-first thinking, surfaces dependencies, and produces an artifact you can save to `.cursor/plans/` and `@file`-reference next session. Cursor has **no persistent session memory**, so re-inject context at each session start — `@`-reference `@PLAN.md`, the relevant `docs/FEATURES.md` spec, and the saved plan, plus "do not modify these interfaces: @app/protocols.py". The real constraint layer is still machine-checkable: interfaces in `app/protocols.py`/`app/types.py`, the SSE event vocabulary, and `tsc --noEmit` — a violated interface is a type error, no prompt needed (the "Interface Freeze" pattern in @AGENTS.md).

---

## 11. Model selection (solo, two-model pattern)

Match the model to the phase, not the task:

- **Plan** with a high-capability model (Composer 2 or Claude Opus) — planning generates the constraints the executor is then bounded by.
- **Execute** scoped, well-specified tasks with a faster/cheaper model (Composer 1.5 or Claude Sonnet); the tighter the contract + interfaces, the smaller the model you can hand it to.
- **High-parallelism CLI waves:** prefer the higher-rate-limit model (Composer 1.5) so 3+ concurrent agents don't throttle.

Harness matters as much as the model: a vendor's model in its own harness usually spends fewer tokens than the same model through a generic harness — the same reason §4.4 of PLAN.md keeps opencode primary but flags a Phase-4 A/B of Qwen3-Coder in opencode vs Qwen Code.
