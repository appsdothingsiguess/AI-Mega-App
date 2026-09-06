# GPU Deal Agent — Build Document

**Date:** 2026-09-06
**Status:** Certified build-ready
**Location:** `gpu-deal-agent/` subfolder of AI-Mega-App
**Upstream spec:** `SPEC.md` (reference architecture — this document is authoritative for implementation)

---

## 1. What we're building

A local-first GPU deal acquisition engine that searches online marketplaces for
legitimate GPUs matching capability constraints (VRAM, vendor, budget, condition)
and returns verified, ranked results. The system uses Pi SDK as the browser
worker harness, NeoBrowser as the authenticated browser backend, and the existing
local model stack for the rare cases where LLM interpretation is needed.

The first query:

```
Find the best legitimate NVIDIA GPU with ≥24 GB VRAM,
under $800 total (item + shipping + fees),
used/refurbished/new, excluding auctions and broken cards.
```

### What it is not

- Not a purchasing agent (no bids, no cart, no checkout)
- Not a general browser agent (marketplace domains only)
- Not an LLM-dependent data pipeline (deterministic extraction, LLM navigates)

---

## 2. Architecture decisions

| Decision | Choice | Why |
|---|---|---|
| Project boundary | Subfolder of AI-Mega-App | Shares infra context; own package.json/tsconfig |
| Language | TypeScript / Node.js 22+ | Pi SDK is TypeScript-native; MCP adapter is TS |
| Worker harness | Pi SDK (`createAgentSession`) | Proven ephemeral sessions, MCP adapter, tool control |
| Worker fallback | Mode C custom loop (`pi-agent-core` Agent) | If full Pi SDK is awkward, drop one layer |
| Browser backend | NeoBrowser MCP server | Real Chrome, real sessions, verified actions, `read` tool |
| LLM role | Navigation only + ambiguous classifier | LLM drives browser; code extracts data |
| Data extraction | Deterministic parsing of `read`/`observe` output | No hallucinated prices/conditions/ratings |
| Database | SQLite (better-sqlite3) | Local, append-only observations, auditable |
| Dashboard | Local web UI reading SQLite | Separate from Pi integration; history + monitoring |
| Parent integration | Pi extension (high-level tools) | Pi sees compact results, not browser traces |

### Core architectural rule

```
The LLM NAVIGATES.  Code EXTRACTS.  The LLM NEVER reports a price.
```

The browser worker (Pi SDK session) uses NeoBrowser tools to search, click,
scroll, and open listings. Once on a page, `read` dumps visible text and
`observe` returns structured elements. Marketplace adapter code parses that
output with regex/string matching. The LLM only interprets content when
deterministic rules produce an ambiguous result (e.g., "RTX 3090 with EK
waterblock installed" — is it a card or a waterblock?).

---

## 3. System topology

```
Pi (parent agent)
  │
  │ gpu_find_deals / gpu_verify_deal
  ▼
┌─────────────────────────┐
│  GPU Deal Engine        │
│  (TypeScript, this pkg) │
│                         │
│  ┌───────────────────┐  │
│  │ Query Planner     │  │  ← GPU catalog + criteria → search queries
│  └────────┬──────────┘  │
│           │              │
│  ┌────────▼──────────┐  │
│  │ Pi SDK Worker      │  │  ← createAgentSession + SessionManager.inMemory()
│  │ (per marketplace)  │  │
│  │                    │  │
│  │  tools:            │  │
│  │   - NeoBrowser MCP │  │  ← navigate, click, type, scroll, read, observe
│  │   - submit_results │  │  ← custom tool, returns parsed data to engine
│  │                    │  │
│  │  NO: bash, edit,   │  │
│  │  write, shell, fs  │  │
│  └────────┬──────────┘  │
│           │              │
│  ┌────────▼──────────┐  │
│  │ Marketplace       │  │  ← deterministic parsing of read/observe output
│  │ Adapter (per site)│  │
│  └────────┬──────────┘  │
│           │              │
│  ┌────────▼──────────┐  │
│  │ Rejection Rules   │  │  ← auctions, accessories, broken, PCs
│  │ GPU Matching      │  │  ← catalog alias match
│  │ Price Normalization│ │  ← item + shipping + fees = known_total
│  │ Condition Norm    │  │
│  │ Deduplication     │  │
│  └────────┬──────────┘  │
│           │              │
│  ┌────────▼──────────┐  │
│  │ Ambiguous         │  │  ← LLM classifier (local model, strict JSON schema)
│  │ Classifier        │  │  ← ONLY for cases rules can't resolve
│  └────────┬──────────┘  │
│           │              │
│  ┌────────▼──────────┐  │
│  │ Ranking + Verify  │  │  ← weighted score, re-open top N in fresh workers
│  └────────┬──────────┘  │
│           │              │
│  ┌────────▼──────────┐  │
│  │ SQLite             │  │  ← append-only observations, search runs, alerts
│  └────────┬──────────┘  │
│           │              │
│  ┌────────▼──────────┐  │
│  │ Dashboard (web UI) │  │  ← reads SQLite, shows history/trends/source health
│  └───────────────────┘  │
└─────────────────────────┘
```

---

## 4. Technology stack

| Concern | Choice | Notes |
|---|---|---|
| Runtime | Node.js 22+ | |
| Package manager | pnpm | Lock it |
| Pi SDK | `@mariozechner/pi-coding-agent` | `createAgentSession`, `SessionManager`, `defineTool` |
| Pi agent core | `@mariozechner/pi-agent-core` | `Agent`, tool types, event types |
| Pi AI | `@mariozechner/pi-ai` | Model definitions, `streamSimple`, OpenAI-compat models |
| Pi MCP adapter | `pi-mcp-adapter` | `createMcpAdapter` for NeoBrowser connection |
| Browser | NeoBrowser MCP server | `read`, `observe`, `navigate`, `click`, `type`, `scroll`, `js` |
| SQLite | `better-sqlite3` | Synchronous, mature |
| Validation | Zod | Schema validation for candidates, classifier output |
| Tests | Vitest | |
| Dashboard | Express + static HTML/JS | Minimal; reads SQLite directly |

---

## 5. NeoBrowser tools — usage contract

### Tools the worker uses

| Tool | Purpose | Who interprets output |
|---|---|---|
| `navigate` | Load marketplace URLs | Worker (LLM) decides where to go |
| `observe` | Get interactive elements with stable refs | Worker uses refs to click/type |
| `click` | Click elements (filters, listings, buttons) | Worker decides what to click |
| `type` | Type search queries | Worker provides search terms |
| `scroll` | Scroll result lists | Worker decides when to scroll |
| `read` | **Extract visible page text** | **CODE parses this, not LLM** |
| `js` | Run JS in page context (escape hatch) | Code-generated queries, deterministic |

### Verified action statuses

Every mutation (`click`, `type`, `navigate`) returns:

- `succeeded` — action worked, page changed as expected
- `failed` — something broke
- `blocked` — anti-bot wall detected
- `needs_human` → maps to spec's `HUMAN_REQUIRED`
- `uncertain` — action ran but outcome unclear

The worker must check these statuses and abort on `blocked`/`needs_human`.

### Tools the worker NEVER gets

`login`, `upload`, `download`, `revoke_session`, any tool outside NeoBrowser.

### Domain allowlist

Set `NEOBROWSER_ALLOW_DOMAINS` per marketplace. eBay worker gets `ebay.com`
only. No cross-domain navigation.

---

## 6. Pi SDK worker — implementation contract

### Session creation

```typescript
import { createAgentSession, SessionManager, SettingsManager } from "@mariozechner/pi-coding-agent";
import { defineTool } from "@mariozechner/pi-agent-core";
import { createMcpAdapter } from "pi-mcp-adapter";

const neoAdapter = createMcpAdapter({
  config: {
    mcpServers: {
      neobrowser: {
        command: "neobrowser",
        args: ["serve", "--stdio"],
        env: {
          NEOBROWSER_ALLOW_DOMAINS: "ebay.com",
          NEOBROWSER_POLICY: "safe",
          NEOBROWSER_TOOLSET: "core",
        },
      },
    },
  },
});

const submitResults = defineTool({
  name: "submit_results",
  description: "Submit extracted listing data for this marketplace search",
  parameters: SubmitResultsSchema, // Zod → TypeBox
  execute: async (toolCallId, params) => {
    candidateCollector.receive(params);
    return { content: [{ type: "text", text: "Results submitted." }], details: {} };
  },
});

const { session } = await createAgentSession({
  model: localModel,               // OpenAI-compat endpoint
  thinkingLevel: "off",            // no reasoning overhead
  sessionManager: SessionManager.inMemory(),
  settingsManager: SettingsManager.inMemory({
    compaction: { enabled: false }, // ephemeral, no compaction
    retry: { enabled: false },
  }),
  tools: [],                       // no built-in file tools
  noTools: "builtin",              // strip read/bash/edit/write
  customTools: [submitResults],
  resourceLoader: new DefaultResourceLoader({
    systemPromptOverride: () => marketplaceWorkerPrompt(marketplace, task),
    extensionFactories: [neoAdapter],
  }),
});
```

### Worker lifecycle

```
create session (in-memory, no built-in tools, Neo MCP only)
  → session.prompt(marketplaceSearchTask)
    → worker navigates marketplace using Neo tools
    → worker calls read to get page text
    → worker calls submit_results with raw text + element data
  → candidateCollector.wait(timeout)
  → session.abort() if timeout
  → session.dispose()
```

### Worker constraints

| Constraint | Value | Enforced by |
|---|---|---|
| Max tool calls | 40 | Pi SDK agent step limit / application counter |
| Wall-clock timeout | 5 minutes | AbortController + session.abort() |
| Max candidates | 30 per source | submit_results schema validation |
| Domain allowlist | per marketplace | NEOBROWSER_ALLOW_DOMAINS |
| No file tools | — | noTools: "builtin" |
| No purchase actions | — | NeoBrowser tools don't include cart/bid |

### What the worker submits

The worker calls `submit_results` with the **raw `read` output** and
**`observe` element data** for each listing it found. It does NOT parse
prices, conditions, or seller ratings. It submits evidence. The marketplace
adapter (deterministic code) does the parsing.

```typescript
interface WorkerSubmission {
  marketplace: string;
  listings: Array<{
    url: string;
    pageText: string;        // raw output from NeoBrowser read
    elements?: string;       // raw output from NeoBrowser observe
    workerNotes?: string;    // LLM can flag "this might be an auction"
  }>;
  status: "complete" | "partial" | "human_required" | "blocked";
  humanRequiredReason?: string;
}
```

---

## 7. Data extraction — marketplace adapters

Each marketplace adapter implements:

```typescript
interface MarketplaceAdapter {
  id: MarketplaceId;
  displayName: string;
  allowedDomains: string[];

  buildSearchQueries(criteria: DealCriteria, catalog: GpuCatalog): SearchQuery[];
  buildWorkerPrompt(task: MarketplaceSearchTask): string;

  // DETERMINISTIC: parse raw page text into structured listing data
  parseListingFromText(pageText: string): ParsedListing | null;
  parseSearchResultsFromText(pageText: string): ParsedSearchResult[];

  // marketplace-specific signals from parsed data
  detectAuction(parsed: ParsedListing): boolean;
  detectCondition(parsed: ParsedListing): CanonicalCondition;
  extractPrice(parsed: ParsedListing): PriceComponents | null;
  extractSeller(parsed: ParsedListing): SellerInfo | null;
  canonicalizeUrl(url: string): string;
  extractListingId(url: string): string | null;
}
```

### eBay adapter (priority 0) — parse strategy

eBay's `read` output for a listing page will contain text like:

```
EVGA GeForce RTX 3090 FTW3 Ultra 24GB GDDR6X
US $599.99
Free shipping
Condition: Pre-Owned
Buy It Now
Seller: gpu_deals_2024 (99.2% positive feedback) 1,234 items sold
```

The adapter parses this with patterns:

```typescript
const EBAY_PRICE = /US \$([0-9,]+\.\d{2})/;
const EBAY_SHIPPING = /(?:Free shipping|\+ US \$([0-9,]+\.\d{2}) shipping)/;
const EBAY_CONDITION = /Condition:\s*(.+)/;
const EBAY_LISTING_TYPE = /(?:Buy It Now|Place bid|Best Offer)/;
const EBAY_SELLER = /Seller:\s*(\S+)\s*\(([0-9.]+)%/;
```

If `read` output is too noisy, fall back to `js`:

```typescript
// Targeted DOM extraction — deterministic, no LLM
const jsQuery = `JSON.stringify({
  price: document.querySelector('[data-testid="x-price-primary"]')?.textContent,
  shipping: document.querySelector('[data-testid="x-shipping"]')?.textContent,
  condition: document.querySelector('[data-testid="x-item-condition"]')?.textContent,
  title: document.querySelector('h1')?.textContent,
  seller: document.querySelector('[data-testid="str-title"]')?.textContent,
})`;
```

### The `js` escape hatch

NeoBrowser's `js` tool runs JavaScript in page context (policy-gated under
`safe` or `developer` mode). This is the deterministic extraction guarantee:
if `read` output is unreliable for a marketplace, `js` with targeted
`querySelector` calls extracts exactly the fields we need. The LLM never
interprets prices — `js` returns the raw DOM text, code parses it.

**Rule:** `js` queries are defined in the marketplace adapter code, not
generated by the LLM. The worker prompt tells the LLM to call `read` (and
optionally a wrapper tool that runs predefined `js`), never to compose its
own JavaScript.

---

## 8. GPU catalog

Static JSON, editable without code changes.

```json
{
  "id": "nvidia-rtx-3090",
  "vendor": "nvidia",
  "canonical_name": "GeForce RTX 3090",
  "vram_gb": 24,
  "architecture": "ampere",
  "aliases": ["rtx 3090", "geforce 3090", "3090 24gb"],
  "exclude_alias_context": ["waterblock", "backplate", "cooler", "box only"],
  "active": true
}
```

Initial catalog (NVIDIA ≥24 GB for local AI):

| Model | VRAM | Architecture |
|---|---|---|
| GeForce RTX 3090 | 24 GB | Ampere |
| GeForce RTX 3090 Ti | 24 GB | Ampere |
| GeForce RTX 4090 | 24 GB | Ada Lovelace |
| GeForce RTX 5090 | 32 GB | Blackwell |
| TITAN RTX | 24 GB | Turing |
| RTX A5000 | 24 GB | Ampere |
| RTX A5500 | 24 GB | Ampere |
| RTX A6000 | 48 GB | Ampere |
| RTX 6000 Ada | 48 GB | Ada Lovelace |

---

## 9. Deterministic rules (run before any LLM call)

### Rejection pipeline

```
parsed listing
  → auction check (listing type text, bid presence)
  → broken/parts check (keyword scan)
  → accessory check (keyword scan + context)
  → complete PC/laptop check
  → price sanity (implausible low, placeholder $1/$0/free)
  → PASSED: proceed to GPU matching
  → REJECTED: persist with reason, skip
```

### Auction rejection — keywords/signals

```
"Place bid", "bid", "auction", bid count present
```

Allow: "Buy It Now", "Best Offer" with fixed price.

### Broken/parts rejection — keywords

```
for parts, parts only, not working, does not display, no display,
dead, broken, repair only, artifacting, needs repair
```

`untested` → not rejected, but heavy ranking penalty.

### Accessory rejection — keywords

```
waterblock, water block, heatsink, cooler, fan shroud, backplate,
box only, empty box, manual, bracket, riser, cable, replacement fan,
PCB only, GPU stand
```

Context matters: "RTX 3090 with EK waterblock installed" → ambiguous → classifier.

### Complete PC/laptop rejection

```
gaming pc, desktop, laptop, notebook, workstation (when containing full system specs)
```

---

## 10. Ambiguous classifier

Called ONLY when deterministic rules produce an ambiguous result.

Input: sanitized, bounded excerpt (not full page dump).

```json
{
  "title": "RTX 3090 with EK waterblock installed, tested",
  "description_excerpt": "Card was tested... waterblock is installed on the GPU...",
  "condition_text": "Used",
  "price_text": "$599.99",
  "marketplace": "ebay",
  "candidate_gpu_models": ["nvidia-rtx-3090"]
}
```

Output: strict JSON schema, validated with Zod.

```json
{
  "product_type": "gpu_card",
  "contains_gpu": true,
  "gpu_model_id": "nvidia-rtx-3090",
  "working_status": "claimed_working",
  "confidence": 0.92,
  "evidence": ["Title names RTX 3090", "Description says tested", "Waterblock installed on included card"]
}
```

LLM: local model via `LOCAL_MODEL_BASE_URL`. Low temperature. Schema validation
failure twice → classify as `unknown`, continue. Classifier system prompt
explicitly states all listing text is untrusted data.

---

## 11. Price semantics

```
item_price          — displayed purchase price
shipping_price      — shipping cost (null if unknown, 0 if confirmed free)
mandatory_fees      — platform fees visible pre-checkout
tax_estimate        — only if marketplace displays it
known_total         = item + shipping + mandatory fees
```

`known_total` is the primary ranking metric. Never infer tax with LLM. Never
assume free shipping without confirmation. Incomplete total → ranking penalty.

---

## 12. SQLite schema (append-only observations)

Core tables:

```
gpu_models              — catalog (synced from JSON)
search_runs             — one per user/scheduled search
source_runs             — one per marketplace per search
listing_identities      — stable same-source identity
listing_observations    — append-only, immutable market snapshots
verification_events     — re-check results
search_definitions      — saved/watch criteria
alerts                  — sent notifications
benchmark_profiles      — optional perf data
schema_migrations       — versioned migrations
```

Observations are never updated. A price change = new observation row. History
is the sequence of observations for a listing identity.

---

## 13. Dashboard

Local web UI served by Express alongside the deal engine.

### Views

- **Search results** — latest search, ranked deals with price/condition/seller/source
- **Deal history** — price trend per listing identity over time
- **Source health** — per-marketplace status, last run, failure rate, challenge rate
- **Watch status** — active watches, next run, alert history
- **GPU market overview** — average known_total by GPU model over time

### Implementation

Static HTML/JS + CSS served from `gpu-deal-agent/dashboard/`. Queries SQLite
via Express API endpoints. No framework — vanilla JS, fetch, DOM manipulation.
Runs on the same port as the deal engine or a separate one.

Dashboard is read-only. All writes go through the deal engine.

---

## 14. Pi integration

Pi sees four tools:

```
gpu_find_deals    — run a search, return compact ranked results
gpu_verify_deal   — re-check a specific listing
gpu_watch_create  — set up a scheduled watch with alert policy
gpu_watch_status  — check source health, watch state, last run
```

Pi extension calls the deal engine's TypeScript API. Pi never touches
NeoBrowser directly for a GPU search. Pi receives:

```
GPU search complete.
3 sources succeeded, 1 source needs human verification (Facebook — login required).
Top verified deal: RTX 3090 24GB, used, $622.49 known total, eBay fixed-price.
5 results returned. Dashboard: http://localhost:PORT/deals/q_abc123
```

Not 40 lines of navigate/click/scroll/snapshot.

---

## 15. Configuration

### `.env.example`

```
LOCAL_MODEL_BASE_URL=              # e.g. http://localhost:8081/v1
LOCAL_MODEL_API_KEY=               # local-placeholder or real key
NEOBROWSER_COMMAND=                # path to neobrowser binary
GPU_DEAL_DB_PATH=./data/gpu-deals.sqlite
DASHBOARD_PORT=3100
ALERT_NTFY_URL=
ALERT_DISCORD_WEBHOOK=
```

No hardcoded IPs, ports, model names, or Neo tool names.

### `config/default.json`

```json
{
  "worker": {
    "maxToolCalls": 40,
    "timeoutMs": 300000,
    "maxCandidatesPerSource": 30,
    "maxConcurrentWorkers": 2
  },
  "verification": { "topN": 5 },
  "ranking": {
    "weights": {
      "price": 0.45,
      "seller": 0.20,
      "condition": 0.15,
      "verification": 0.10,
      "aiValue": 0.10
    }
  },
  "neobrowser": {
    "policy": "safe",
    "toolset": "core",
    "maxTabs": 5
  }
}
```

---

## 16. Implementation phases

### Phase 0 — Scaffold

- Repository scaffold inside `gpu-deal-agent/`
- `package.json`, `tsconfig.json`, Vitest config
- Core TypeScript types (all interfaces from this doc)
- Error taxonomy (typed error codes, not strings)
- Config loader (`.env` + `config/default.json`)
- `IMPLEMENTATION_STATUS.md`
- **Gate:** `pnpm test` passes, `tsc --noEmit` passes, no runtime deps on Neo/Pi/model

### Phase 1 — GPU catalog + deterministic rules

- GPU catalog loader from JSON
- Alias matching pipeline
- Auction/broken/accessory/PC rejection rules
- Money parser + known_total math
- Condition normalization
- **Gate:** ≥30 tricky title fixtures pass deterministically, price math exact

### Phase 2 — SQLite persistence

- Migrations (idempotent, versioned)
- Repositories for all tables
- Append-only observation writes
- Query helpers (by GPU model, by marketplace, by date range)
- **Gate:** write + read + migration tests pass on temp DB

### Phase 3 — NeoBrowser adapter

- `BrowserPort` interface
- NeoBrowser MCP connection (discover tools at runtime, never hardcode)
- Health check, timeout, cancellation
- `read` output capture
- `js` execution wrapper (predefined queries only)
- **Gate:** adapter can list Neo tools, perform one navigate + read

### Phase 4 — Pi SDK worker

- Ephemeral session with `SessionManager.inMemory()`
- `submit_results` custom tool with Zod schema
- Worker system prompt (navigation only, not extraction)
- Neo MCP adapter loaded via `createMcpAdapter`
- Step limit + timeout enforcement
- **Gate:** mock worker test submits valid results, invalid schema rejected

### Phase 5 — eBay adapter + end-to-end

- eBay search query builder
- eBay `read` output parser (regex patterns)
- eBay `js` fallback queries
- eBay URL/listing ID canonicalization
- Integration: worker navigates eBay → read → adapter parses → rules filter → persist
- **Gate:** `gpu-deal search --sources ebay --min-vram 24 --exclude-auctions --limit 10` returns structured persisted results with zero auctions in accepted set

### Phase 6 — Classifier

- Sanitized input builder
- Strict JSON schema output validation
- Local model call via `LOCAL_MODEL_BASE_URL`
- Retry once on schema failure, then `unknown`
- Fixture tests including prompt injection attempts
- **Gate:** deterministic cases never call classifier; ambiguous cases produce valid output

### Phase 7 — Verification + ranking

- Top-N selection from unverified candidates
- Verification worker (fresh Pi SDK session, re-opens listing)
- New observation on material change
- Weighted ranking with transparent score breakdown
- **Gate:** verified deals show current prices; changed price = new observation

### Phase 8 — Dashboard

- Express server serving static UI
- API endpoints reading SQLite
- Search results view, deal history view, source health view
- **Gate:** dashboard loads, shows real data from completed searches

### Phase 9 — Pi extension

- `gpu_find_deals`, `gpu_verify_deal`, `gpu_watch_status` tools
- Compact result formatting for Pi context
- **Gate:** Pi sees summary, not browser traces

### Phase 10 — Scheduler + alerts

- Watch definitions, interval runner, per-watch lock
- Source cooldown on repeated failures
- Console + ntfy/Discord alert sinks
- Alert deduplication
- Verify-before-alert
- **Gate:** unattended search runs while Neo is available; same deal doesn't alert twice

### Phase 11+ — Additional marketplaces

Facebook Marketplace, Jawa, Newegg — one at a time, each with own adapter,
fixtures, and `read` parser. Only after core pipeline is proven on eBay.

---

## 17. Rules for the coding agent

1. Read this document and `SPEC.md` before editing. This document wins on conflicts.
2. Read `IMPLEMENTATION_STATUS.md` before every phase.
3. Patches ≤120 changed lines when practical. One subsystem per patch.
4. Tests with every behavioral change. Run targeted tests after each patch.
5. Full test suite at each phase gate.
6. **Never let the LLM extract/report prices.** Prices come from `read`/`js` output parsed by code.
7. **Never hardcode Neo tool names.** Discover from the installed MCP server.
8. **Never hardcode model endpoints.** Use `LOCAL_MODEL_BASE_URL` from config.
9. **Never add purchase/bid/message/cart actions.**
10. **Never bypass CAPTCHAs.** Report `HUMAN_REQUIRED` and stop.
11. **Never generate `js` queries from LLM output.** All `js` queries are static strings in adapter code.
12. No marketplace adapter before core normalization/rejection tests exist.
13. Do not store auth cookies/tokens in project files.
14. If a requirement is unclear, choose the safest deterministic behavior and document it.

---

## 18. Spec deviations from SPEC.md

| Topic | SPEC.md says | This document says | Why |
|---|---|---|---|
| Repo boundary | Separate repository | Subfolder of AI-Mega-App | Shares infra context, simpler for now |
| Data extraction | Worker submits structured candidates | Worker submits raw `read` output; code parses | Eliminates hallucinated prices/conditions |
| Worker submission | `RawCandidate` with parsed fields | `WorkerSubmission` with raw page text | LLM doesn't interpret, code does |
| `js` tool usage | Not mentioned | Explicit escape hatch for deterministic DOM extraction | NeoBrowser capability unknown at spec time |
| Dashboard | Not in spec | Local web UI on SQLite | Needed for monitoring; Pi is conversational, not visual |
| Mode C fallback | Custom tool loop from scratch | Use `pi-agent-core` Agent class directly | Same result, less code to own |

---

## 19. Open questions

1. **NeoBrowser `read` fidelity** — how clean is the text output for structured
   marketplace pages? Needs smoke test in phase 3. If noisy, `js` fallback is ready.
2. **Pi SDK step/timeout enforcement** — does the SDK's agent loop respect
   external abort signals cleanly? Needs verification in phase 4.
3. **NeoBrowser concurrent sessions** — can multiple workers run simultaneous
   Neo sessions via separate profiles? `NEOBROWSER_PROFILE` supports isolation
   but needs testing.
4. **eBay DOM stability** — how often do eBay's CSS selectors / data-testid
   attributes change? The `js` queries need a maintenance plan.
5. **Terms of service** — automated browsing of eBay/Facebook at scheduled
   intervals may trigger anti-bot measures or violate ToS. Mitigation: low
   frequency (6-hour intervals), human-like navigation via Neo, respect for
   rate limits and challenges. Not a technical problem, but a policy risk.

---

## 20. Definition of done (v1)

- [ ] CLI search returns ranked, verified GPU deals from eBay
- [ ] Pi invokes `gpu_find_deals` and sees compact results
- [ ] At least eBay + one additional source works
- [ ] Zero auctions in accepted results when excluded
- [ ] Accessories, empty boxes, PCs, broken cards rejected
- [ ] Prices extracted deterministically (no LLM price reporting)
- [ ] Observations persisted in SQLite (append-only)
- [ ] Top results re-verified before display/alert
- [ ] Source failures isolated (one down ≠ all down)
- [ ] Human challenges reported without bypass
- [ ] Dashboard shows search history and source health
- [ ] Watch jobs run unattended while Neo + model stack available
- [ ] Alert deduplication works
- [ ] No paid APIs required
- [ ] No purchase/bid/message actions exist
- [ ] Unit/fixture tests meet quality targets

---

# End of build document
