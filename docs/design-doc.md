# Local LLM — UI Design Document

Design reference for the web client described in `PLAN.md` / `FEATURES.md`. Scope: visual system, screen inventory, component states, and the rationale behind each call. Companion prototype: `Local LLM.dc.html` (fully simulated — fake router, fake streaming, fake debug traces; no real backend).

## 1. Visual system

**Why not claude.ai's actual look.** The spec asks for claude.ai-grade *parity of structure* (sidebar → chat → context panel, model picker in composer, per-message model label) — not its branded skin. This doc gives that structure an original identity: a graphite/indigo "local infra tool" palette instead of Claude's cream/orange, because this product's whole reason for existing is that it's *not* a hosted vendor product — it's the user's own GPU box, and the UI should read as an internal tool, not a knockoff.

**Palette (dark only — this is a developer tool run at a desk, not a marketing surface):**
- Background: `#0e0f13` (app) / `#131419` (sidebar) / `#1a1b22` (panels, cards) / `#22232c` (hover/elevated)
- Border: `#2a2b35`
- Text: `#eceef2` (primary) / `#9296a3` (secondary) / `#5c606d` (muted)
- Accent (interactive, selection, links): indigo `#6e6afd`, hover `#7d7aff`, soft fill `rgba(110,106,253,.12)`
- Status: success `#3ecf8e`, warning `#e8b339`, error `#f0576a`
- Route-source colors (debug + composer badges): override `#e8b339`, rule `#3ecf8e`, classifier `#6e6afd`, fallback `#5c606d`

**Type:** IBM Plex Sans (UI copy) + IBM Plex Mono (model names, file paths, spans, token counts, code). The pairing signals "infrastructure," not "product marketing" — mono wherever a value is a literal system token (model alias, file path, span name), sans everywhere else.

**Density:** compact by default (this is a power-user tool with 19 features, not a landing page) — 13–14px body text, 8px base spacing unit, thin 1px borders instead of shadows/elevation.

## 2. Information architecture

Left rail (icons + labels, collapsible): **New chat**, **Chats** (recents), **Projects**, **Code**, **Settings**, **Debug** (opens as its own view, per spec's "standalone window" intent — modeled here as a distinct route since the prototype is one tab). Home is a plain new chat — Projects/Code/Debug are nav destinations, never gates.

## 3. Screens

### 3.1 Chat
- Composer: text input, attach button, per-chat **tool toggle chips** (web_search, browser, file_ops — reflects F9's per-chat consequential gating), and a **model picker** grouped by class (General / Reasoning / Coding / Vision), each entry showing device (GPU0/GPU1/CPU) and resident/swap badge. Selecting a model sets `model_override`; "Auto" clears it back to router control.
- Message list: user + assistant bubbles; assistant messages carry a **per-message model label** and, when routed, a small **route badge** (override/rule/classifier + confidence) — clicking it deep-links to the Debug view's matching trace.
- Streaming states: `model_loading` banner (simulates the measured cold-swap wait) → token-by-token stream → **tool call chips** (`tool_start`/`tool_result`) inline when a tool fires → terminal `done` (never left hanging — mirrors the real done/error contract).
- Artifact detection: a fenced code/HTML block in a finished message opens the right-side **artifact panel** (Preview/Source tabs) — detection is presented as client-side per spec, never blocking the stream.

### 3.2 Projects
Grid of project cards → workspace: **Instructions** editor, **Files** list (ingested/pending state per file), **Project chats**, **Project memory** tab. Filesystem-first framing kept literal (`projects/<id>/instructions.md`).

### 3.3 Code (opencode delegation)
Session list + "New session" flow scoped to an **allowed root** (directory picker limited to registered project/repo paths, per F6's `allowed_roots` guard) + a simulated session event stream. When a chat message names a real repo/directory path rather than a pasted snippet, the router flags it `delegate_suggested` and the assistant message grows a **delegation chip** ("Delegate to opencode → <dir>"); clicking it opens the confirm popup (§3.6), and confirming creates a Code session scoped to that directory and navigates there. Never auto-delegates.

### 3.4 Settings
Left tab rail, one tab per config surface in FEATURES.md §A1/§4.14: **Models** (roster table — alias, class, device, resident/ttl, ctx, enable toggle), **Providers** (API keys for Tavily / opencode zen / Anthropic-future, written to `.env` and redacted on read-back — never `config.yaml`; model→endpoint map placeholder for the future multi-box case), **Routing** (classifier on/off + confidence threshold + keyword rules), **Tools** (per-tool enable + consequential flag — toggling the consequential `browser` tool on requires a confirm popup before it takes effect), **opencode** (per-host enable + URL for `ubuntu`/`windows`, the read-only `allowed_roots` list, and a local-vs-zen confgen provider switch), **BrowserOS** (global enable, MCP server URL, a "Test connection" action that opens a result popup, discovered-tool list), **Memory** (scopes + review queue), **Debug** (master toggle, store-prompts, retention), **Appearance** (theme, show-thinking). Every control mirrors a real `config.yaml`/overlay key named in FEATURES.md so the doc and the prototype stay traceable to the spec.

### 3.6 Popups / confirmation modals
A single reusable centered modal (title + body + Cancel/Confirm) covers every place the spec calls for an explicit confirm rather than a silent state change: enabling the consequential `browser` tool in Settings → Tools, testing the BrowserOS MCP connection, saving a provider API key, and confirming a chat→opencode delegation. Modals never appear unprompted — always triggered by a direct user action — and always leave a Cancel path back to the prior state.

### 3.5 Debug (critical, most detailed screen per spec's "built first, not last")
Three-pane: **trace list** (per turn, filterable by chat) → **waterfall** (span rows, width = duration, colored by stage) → **span detail** (raw prompt/response toggle, token counts + tok/s **sourced from llama.cpp timings, never client estimate**). Top strip: live **GPU telemetry** bars (GPU0/GPU1 VRAM used/total) and **llama-swap state** (loaded / loading model). Route chip shows source + confidence; dispatcher-assisted steps are marked "who decided vs. who emitted."

## 4. Component states worth naming
- **Model badge states:** resident (always warm) / swap-eligible (may cold-load) / loading (spinner + elapsed) — distinguishes the one thing users most need to not mistake for "broken."
- **Route badge colors** are the single palette reused in composer, message list, and Debug — one visual vocabulary for "why did it pick this model," per spec's debug-first philosophy.
- **Tool chip states:** pending → success/error, consequential tools (browser) show an amber confirm state before running.
- **Connection states:** streaming / connection-lost (SSE watchdog) / reconnected — never a silent hang.

## 3.7 Modularity, persistence and memory editing
Per-alias config is editable, not just viewable: each Models roster row has an Edit affordance exposing device (GPU0/GPU1/CPU), context length, and API endpoint as inline fields, with explicit Save/Cancel (toggling a model's enabled switch stays instant — no save step for booleans). The Providers "model → endpoint map" and the BrowserOS MCP URL are the same pattern: free-typed fields get an explicit Save button, never silent autosave. Project Instructions keeps live typing in the textarea but requires clicking Save to confirm the write (mirrors the real `PUT instructions.md` semantics) — a toast confirms persistence. Memory is fully CRUD: an "Add memory" row (scope + text + Add) creates new facts, and every active memory fact has a delete control, in addition to the existing review-queue accept/reject.

A small transient toast (bottom-center, auto-dismiss) is the confirmation pattern for every explicit Save — distinct from the modal (§3.6), which is for actions requiring a yes/no decision before they happen. Save = toast after; consequential action = modal before.

## 4.1 Fixed defects
An earlier build pass had a `line-height` unit bug (a generic px-appending style helper turned `1.65` into `1.65px`, collapsing wrapped lines on top of each other) and unconstrained tool-chip labels wrapping under their pill. Both are fixed: unitless CSS properties (`line-height`, `font-weight`, `opacity`, `z-index`, flex/grid indices) are never given a `px` suffix, and interactive chip labels get `white-space: nowrap`.

## 5. What the prototype fakes (and says so nowhere in the UI)
Router decisions, token streaming, debug spans, GPU usage, and title generation are all simulated client-side with representative timings and copy pulled from the real measured numbers in PLAN.md (e.g., cold-swap ~12s compressed for demo pacing, dispatcher near-instant). No network calls, no real models.
