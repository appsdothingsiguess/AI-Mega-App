# AI Mega App → Pi.dev retirement plan

**Status:** planning only — requires Joey's approval before any move, deletion,
rewrite, service change, model download, or GPU reconfiguration.

**Audit basis (2026-09-06):** `CLAUDE.md`, `AGENTS.md`, `PLAN.md` §§1/5,
`docs/AGENT_CONTEXT_MEGA.md`, and the 2026-08-31 Pi.dev-pivot handoff were
read. `docs/PI_PLUGIN_BRAINSTORM.md`, identified by the brief as the binding
design, is **not in this checkout**. `docs/RETIREMENT_AUDIT_PROMPT.md` repeats
its asserted decisions and `docs/AGENT_PLATFORM_DECISION.md` is a compatible
decision record, but neither is silently treated as its replacement.

## Scope and classification rules

This is a retirement of the custom browser chat/agent product, not of the
local inference and operations estate. “KEEP AS SERVICE” means the underlying
module remains available to the surviving operational scripts; it does **not**
authorize keeping the old FastAPI chat/UI process running. “RETIRE” entries
are candidates for a later approved deletion only. `web/js/**` and `web/dist/**`
are generated/derived assets and must never be hand-edited in that later pass.

The brainstorm file’s absence makes any asserted Pi target provisional. The
targets below use the destination convention required by the brief, and are
limited to mappings already asserted there (GooseDump, open-design, and the
little-coder shortlist). No custom `pi-memory` extension is proposed.

## 1. `app/` and `web/` classification

| Path | Classification | Target | Reason |
|---|---|---|---|
| `app/` package root and `app/__init__.py` | RETIRE | — | Package identity belongs to the retired custom application. |
| `app/main.py` | RETIRE | — | Boots the retired FastAPI chat/UI server and mounts its API/static UI. |
| `app/chat/` (`__init__.py`, `api.py`, `context.py`, `history.py`, `orchestrator.py`, `streaming.py`, `turn.py`) | RETIRE | — | Pi supplies the conversation, stream, context, and agent-loop lifecycle. |
| `app/router/` (`__init__.py`, `classifier.py`, `router.py`, `rules.py`) | RETIRE | — | Binding intent is thinking-level selection in Pi, not a rebuilt classifier/router. |
| `app/background/` (`__init__.py`, `queue.py`, `summaries.py`, `summary_coverage.py`, `summary_policy.py`, `summary_runner.py`, `summary_status.py`, `titles.py`) | RETIRE | — | GooseDump owns compaction and durable-memory flow; Pi owns titles/session lifecycle. |
| `app/debug/` (`__init__.py`, `api.py`, `bus.py`, `trace.py`) | RETIRE | — | The browser Debug view and its app-local trace/SSE transport disappear with the UI. Preserve operational logs/capture scripts outside this package. |
| `app/settings/` (`__init__.py`, `api.py`, `store.py`) | RETIRE | — | This is the retired Settings UI/API overlay writer; human-owned `config.yaml` remains the service control plane. |
| `app/db.py` and `app/schema.sql` | RETIRE | — | SQLite chat/message/trace persistence only serves the retired application. Do not delete `data/` until retention/export requirements are approved. |
| `app/types.py` | RETIRE | — | Frozen types describe the old chat, router, SSE, and debug contracts. |
| `app/llm_client.py` | RETIRE | — | Pi uses the OpenAI-compatible relay/llama-swap endpoint directly; this custom client exists for the old orchestrator/classifier. |
| `app/warmup.py` | UNCLEAR — ask | — | It warms resident models through the retiring Python client. Joey must choose: replace it with an ops-only warmup helper, or intentionally stop automatic resident warmup. |
| `app/config.py` | KEEP AS SERVICE | Remains `app/config.py` pending an approved service-only extraction | `scripts/load_model_check.py`, `config_drift_check.py`, `model_state.py`, and swapgen import it. It is service configuration, but must shed retired UI/router schema only in a later scoped change. |
| `app/gpu/` (`__init__.py`, `inventory.py`, `swapgen.py`) | KEEP AS SERVICE | Remains `app/gpu/` | GPU inventory and generated llama-swap configuration are explicitly surviving infrastructure. |
| `app/gpu/api.py` | UNCLEAR — ask | — | It exposes the retiring `/api/gpu/*` FastAPI control surface. Retain only if Joey wants a service-only local API; otherwise replace its apply path with a documented operator command. |
| `app/gpu/rewarm.py` | UNCLEAR — ask | — | It depends on app-owned config/client lifecycle; retain only after the warmup ownership decision. |
| `app/**/__pycache__/` and all `.pyc` files | RETIRE | — | Runtime caches, never source or migration material. |
| `web/` root and `web/index.html` | RETIRE | — | Old browser shell for the replaced UI. |
| `web/src/app.ts`, `router.ts`, `store.ts`, `types.ts`, `api.ts` | RETIRE | — | Client-side shell, REST/SSE client, state, and routes are all Pi-replaced. |
| `web/src/views/` (`chat.ts`, `composer.ts`, `debug.ts`, `settings.ts`, `settings_models.ts`, `settings_routing.ts`) | RETIRE | — | Chat, model/router picker, Debug, and Settings are custom UI surfaces Pi replaces. |
| `web/src/markdown.ts` | RETIRE | — | Rendering helper used only by the retired browser views. |
| `web/css/` (`app.css`, `chat.css`, `composer.css`, `debug.css`, `settings.css`, `theme.css`) | RETIRE | — | Styling exists solely for the retired UI. |
| `web/js/` and `web/js/views/` | RETIRE | — | Checked-in generated JavaScript corresponding to `web/src`; delete only as generated output in an approved retirement commit, never edit it. |
| `web/dist/` and `web/dist/assets/` | RETIRE | — | Derived legacy build output, not a Pi asset. |
| `web/vendor/` (`README.md`, highlight/marked/DOMPurify assets) | RETIRE | — | Vendored browser dependencies used only by the retired renderer. |

### Pi migration inventory (not source-file moves)

These are capabilities mentioned in the missing brainstorm record, not a
claim that the listed old files can be copied as-is.

| Capability formerly attempted by the app | Classification | Exact target | Reason / caveat |
|---|---|---|---|
| Compaction and durable memory | MIGRATE | Existing GooseDump extension; no new `.pi/extensions/pi-memory/` | The stated decision makes GooseDump the owner. Validate stale-context/session-generation safety before retirement. |
| Thinking-level model choice | MIGRATE | `~/.pi/agent/prompts/model-selection.md` | Replace classifier routing with explicit Pi profile/prompt guidance only; no classifier port. Exact prompt name is provisional pending the missing brainstorm. |
| Design assistance | MIGRATE | `~/.pi/agent/skills/open-design/SKILL.md` | Adopt `nexu-io/open-design` (Apache-2.0, asserted 162 skills), rather than implement a custom design plugin. |
| Write protection, security, checkpoints, compatibility, quality monitoring | MIGRATE | `.pi/extensions/write-guard/`, `.pi/extensions/security/`, `.pi/extensions/checkpoint/`, `.pi/extensions/compatibility/`, `.pi/extensions/quality-monitor/` | Adopt the stated little-coder shortlist after source/license/compatibility review. |
| Subagent, evidence, LSP, extra-tools | UNCLEAR — ask | Candidate bundle under `.pi/extensions/` | Evaluate as one bundle, per the stated decision; do not install a partial bundle or imply approval. |
| Cheap serialized subagents | MIGRATE | `.pi/extensions/subagent/` only after the bundle evaluation | Build this case first; model/profile must be selected in Pi, not via old classifier code. |
| Concurrent GPU1 worker subagents | UNCLEAR — ask | `.pi/extensions/subagent/` plus an approved GPU1 profile | Prohibited until both Qwen3.5-9B gate measurements below exist and capacity policy is approved. |
| Token-limit guard, model preservation, tool gating/turn cap/finalize warning/knowledge inject | RETIRE | — | The asserted brainstorm decision says skip these; no old source maps to them and no replacement is planned. |

**Potential design discrepancy to resolve:** the companion platform decision says
AI Mega App `:8000` may remain behind the relay, while the Pi pivot says Pi
normally targets `:8081` → llama-swap and this audit retires the app server.
Joey must select the intended steady-state endpoint before `app/main.py` or
`app/gpu/api.py` is touched.

## 2. Documentation update plan

`docs/archive/` already exists and contains superseded planning documents;
use that established convention. Archive recommendations are proposals, not
actions in this pass.

| Document | Verdict | Mechanical follow-up edit |
|---|---|---|
| `CLAUDE.md` | Update | Retain the existing “post-mortem, not a foundation” language; change stack/architecture/current-phase passages to name Pi as agent/UI owner, remove retired API/SSE/router/frontend contracts, and retain service, relay, benchmark, config, and GPU rules. |
| `AGENTS.md` | Update | Make Pi + relay + llama-swap the entry topology; mark `app/`/`web/` pending-retirement reference only; retain operations and generated-config discipline. |
| `PLAN.md` | Archive then replace | Archive as `docs/archive/PLAN.md`; create an approved service-only plan that preserves §4.1/benchmark operations and explicitly labels §§4.2–4.16 retired/superseded by Pi. Do not silently edit historical rationale. |
| `docs/FEATURES.md` | Archive then replace | Archive as `docs/archive/FEATURES.md`; replace with a service feature inventory (model serving, relay, GPU/swapgen, benchmarks, optional Qdrant). Old UI/agent feature contract must not remain normative. |
| `docs/PHASE_PROMPTS.md` | Archive | Move to `docs/archive/PHASE_PROMPTS.md`; all numbered implementation waves are for the retired app. |
| `docs/design-doc.md` | Archive | Move to `docs/archive/design-doc.md`; it exclusively specifies the retired web UI. |
| `docs/CURSOR_RULES.md` | Update | Keep generic safety, testing, worktree, remote-box, and benchmark rules; remove/repoint old UI/backend/SSE phase-contract material and link the new service plan. |
| `.cursor/rules/001-stack.mdc` | Update | Replace custom FastAPI/browser-app stack assertions with the Pi client + retained service topology. |
| `.cursor/rules/002-boundaries.mdc` | Update | Retain generic safety; remove frozen SSE/schema/frontend contract language or mark it legacy. |
| `.cursor/rules/003-python-backend.mdc` | Update | Restrict to surviving service/ops Python modules; remove requirements tied to chat orchestration. |
| `.cursor/rules/004-frontend.mdc` | Archive | Copy to `docs/archive/cursor-rules/004-frontend.mdc`; it constrains only retired `web/**`. Ask before touching `.cursor/`. |
| `.cursor/rules/005-config.mdc` | Update | Preserve `config.yaml`/generated `llama-swap.yaml` ownership; remove Settings-UI overlay workflow if the API is retired. |
| `.cursor/rules/006-testing.mdc` | Update | Retain service and benchmark tests; mark retired UI/chat/SSE golden tests as legacy until deletion approval. |
| `.cursor/rules/007-git-worktrees.mdc` | No substantive change | Keep; only replace references to phase branches if they remain. |
| `.cursor/rules/008-remote-box.mdc` | Update | Retain GPU/model/sudo guidance; correct its SSH-first wording to the current `hostname`-first guidance. |
| `.cursor/rules/009-subagents.mdc` | Update | Keep generic worktree safety but replace Cursor phase-wave orchestration with Pi’s evaluated subagent policy and GPU1 gate. |
| `.cursor/rules/010-benchmark-eval-methodology.mdc` | Update | Retain methodology; remove retired classifier-specific glob/claims after router removal. |
| `.cursor/rules/011-ui-design.mdc` | Archive | Copy to `docs/archive/cursor-rules/011-ui-design.mdc`; it exclusively binds the retired UI. Ask before touching `.cursor/`. |
| `docs/HANDOFF.md` | Update | Preserve the 2026-08-31 pivot as history; add a dated retirement-status note pointing to this plan and stating the old app remains until approved retirement. |
| `docs/AGENT_CONTEXT_MEGA.md` | Update | Replace old app architecture/current-defect inventory with service-only state and the Pi topology; preserve roster/relay/Qwen3.6 safety facts. |

Adjacent documents that must be cross-checked in the implementation pass:
`docs/AGENT_PLATFORM_DECISION.md`, `docs/PI_GOOSEDUMP_INTEGRATION.md`, and
`docs/RETIREMENT_AUDIT_PROMPT.md`. The last should be archived after this plan
is accepted so it is not mistaken for an executable design decision.

## 3. GPU1 worker-slot gate — passed for the resident Pi coding worker

This audit ran on hostname **`ailab`**. The candidate was downloaded from
`unsloth/Qwen3.5-9B-GGUF`, checksum-verified, and benchmarked through the
repository's isolated server harness on GPU1.

| Required fact | Result | Consequence |
|---|---|---|
| Qwen3.5-9B Q4_K_M GGUF byte size | **Verified** — 5,680,522,464 bytes (5.29 GiB), SHA-256 `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8`. | Fits as the sole GPU1 coding-worker resident. |
| GPU1 32K real decode | **Verified** — 67.42 / 66.74 / 66.24 tok/s; **66.80 tok/s average**, 6,405 MiB peak. The harness currently rejects its documented `coder-small` label, so its equivalent supported `coder` prompt class was used. | Deployed as resident `coder-sub` with 32K context, Flash Attention, reasoning off, and one slot. |

The prior Qwen3.6 GPU1 worker is disabled and inactive. `utility-gpu`, CPU
`utility`, and `dispatcher` are retired from the generated roster. Startup
warmup now loads `chat-default` (GPU0) and `coder-sub` (GPU1) through
llama-swap; `pi-capture-relay` on `:8081` remains the Pi route.

## Ordered approved-execution sequence

1. Joey supplies/recovers `docs/PI_PLUGIN_BRAINSTORM.md`, resolves the `:8000`
   versus direct-`:8081` steady-state topology, and decides the warmup/API
   ownership questions. Update this plan if those answers alter a row.
2. Validate Pi in real coding sessions: normal relay, GooseDump compaction,
   durable-memory calls, session restart/reload/fork stale-context handling,
   large tool output, BrowserOS MCP fallback, and direct search/fetch. Do not
   retire the old harness merely because an extension installs.
3. With explicit authorization to acquire the model, perform the two GPU1
   measurements above using the existing harness; make a go/no-go decision
   for the optional concurrent lane from measured size, VRAM, and tok/s.
4. Create a named preservation point (tag and/or dedicated archival branch)
   containing the complete old application before deletion. Confirm clean
   intended paths, data retention/export needs, and rollback checkout steps.
   No force push, merge, or deletion is authorized by this plan.
5. Apply documentation notices/archives first, including a clear transition
   date and rollback pointer. Changes to `.cursor/`, CI, and hooks require a
   separate explicit approval.
6. In a dedicated scoped worktree, retain and test the service subset:
   config/swapgen, GPU inventory, relay, benchmarks, ops scripts, and any
   approved replacement for warmup/apply. Remove app/UI dependencies from
   surviving scripts before deleting their old package parents.
7. Stop and archive the custom UI/application only after Pi validation and
   service checks pass. Delete source and its generated `web/js`/`web/dist`
   companions together in an explicit-path commit; never hand-edit generated
   output. Keep the preservation point as rollback.
8. Run the appropriate gate for the actual changed scopes (at minimum
   `git diff --check` for docs; pytest for Python/service changes; TypeScript
   only while `web/src` remains in scope), then have Joey review the final
   diff and service state before any deploy/restart.

## Explicitly not touched by this plan

- llama.cpp, llama-swap, the model roster, model files, and generated
  `llama-swap.yaml`;
- production/diagnostic relays (`:8081` and `:8082`), capture handling, and
  the Pi memory service;
- Qwen3.6 isolated-worker mode and its safety rules;
- benchmark/evaluation and operational scripts, GPU inventory/swapgen, and
  model-placement knowledge;
- Qdrant and any future plugin’s vector use (no current app `rag/` directory
  exists to migrate); and
- repository data, services, Git history/branches/tags, CI, hooks, and
  `.cursor/` files.

## Open questions for Joey

1. Where is the binding `docs/PI_PLUGIN_BRAINSTORM.md`, and should it be
   restored to this branch before an implementation pass?
2. Is direct Pi → `:8081` → llama-swap the intended steady state, or must a
   service-only `:8000` API remain? If the latter, which endpoints are owned?
3. Who owns resident-model warmup and GPU apply after the old FastAPI Settings
   API is gone: an operator command, a small service-only API, or llama-swap?
4. What data-retention/export period applies to `data/app.db` before its
   retired chat/trace schema is removed?
5. May the Qwen3.5-9B GGUF be downloaded for the GPU1 gate, and what minimum
   tok/s/VRAM acceptance thresholds approve the concurrent lane?
