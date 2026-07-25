# AI Mega App — Build Plan v2

**Date:** 2026-07-23 (rev 6 — **Phase 0 + 0.5 complete; every guess in rev 5 replaced by a measurement.** Five decisions reversed: tensor-split → **solo-GPU0 pinning** (`CUDA_VISIBLE_DEVICES`, ~3x faster than a degenerate split and structurally OOM-free); sqlite-vec → **Qdrant** behind `VectorStore`; Cactus Needle → **Hammer2.1-1.5b** dispatcher; residents → **CPU-resident `utility`+`embed`** (Config B); reasoner → **Qwen3.6-35B-A3B thinking** (same weights as `chat-default`). Classifier taxonomy re-frozen to the 6 categories that actually measured 91.76%. rev 5: tensor-split benchmarks. rev 4: fleet correction, no cross-box RPC. rev 2: TypeScript, hermes-style memory, claude.ai 1:1 UI, no auth.)
**Status:** Phase 0 closed 2026-07-23 (`docs/phase0-measurements.md`, `docs/PHASE0_FINDINGS_SUMMARY.md`). **Phase 1 authorized.** Supersedes `prompter_x_complete_spec.md` (kept only as a post-mortem reference).
**Confidence tags:** [FACT] verifiable · [INFERENCE] reasoned · [UNCERTAIN] guess, verify before building.

---

## 1. Why the old plan and codebase are not the base

The repo is evidence of what went wrong, not a foundation. Lessons extracted:

1. **Ollama was the wrong center of gravity.** The whole model layer (ModelScheduler, `ollama_model_names` alias mapping, keep_alive semantics, warmup quirks) was custom code compensating for Ollama's weak programmatic model management. The new spec bans Ollama. [FACT — visible across `app/model_scheduler.py`, `HANDOFF_OLLAMA_INTEGRATION.md`, README]
2. **The classifier became a project of its own.** A ~4.4k-token prompt for a 1.5–3B model, truncation bugs (`num_ctx=4096` silently broke it), an eval ledger, and ~a third of recent commits are classifier fixes. Free-text JSON from a small model is fragile. [FACT — git log, `docs/classifier_prompt*.md`]
3. **Components built but never wired.** DDG search adapter existed, tested, and was never injected at startup (Bug 1). The plan had per-file task ownership but no integration gate per feature. [FACT — `docs/phase1-open-bugs.md`]
4. **Error paths were an afterthought.** Provider failures killed SSE streams silently (Bug 2). Debug tracing arrived late instead of being the first thing built — the new spec correctly makes the debug panel critical.
5. **UI never reached Claude.ai parity** because the plan treated frontend as "extend existing" last-mile work (Bugs 3–4: no home chat, no project grid, no model labels).
6. **Three config files + frozen-file contracts** (`settings.json`, `litellm_config.yaml`, `.env`, frozen `project_manager.py`) created coordination overhead that blocked simple fixes (Bug 4 needed a "frozen file owner approval" to add one field).
7. **LiteLLM added a layer without earning it.** Its value was multi-provider abstraction; the new spec is local-first with one OpenAI-compatible endpoint (llama-swap), so LiteLLM is dead weight. [INFERENCE]
8. **Old spec's hardware assumptions are stale.** It targeted an 8GB RTX 3070 on WSL2; the actual environment became a dedicated LAN GPU box, and the new spec is multi-GPU Ubuntu 26.04. Every VRAM budget in the old spec is void. [FACT]

Carry-forward that *did* work: SSE streaming contract, filesystem-first projects, intent→model mapping in config not code, keyword-rules-before-classifier ordering, graceful degradation rules, Cursor rules discipline (worktrees, FILE SCOPE, no `git add .`).

---

## 2. Prior art (how others built this)

| Project | What to take | What to avoid |
|---|---|---|
| **Odysseus** (spec's stated near-identical target) | Python backend + **vanilla JS/CSS/HTML frontend — no React** [FACT — repo language stats]. Module structure: chat, agents, deep research, documents, notes, model "cookbook" (hardware-aware model serving), blind model comparison. Docker compose variants per GPU vendor. | It's a monolith of many productivity modules (email, calendar, notes) — those are Future-tier for us, don't clone wholesale. |
| **hermes-agent** (Nous Research) | One agent core serving every surface (CLI/API/gateway) — platform differences live at the entry point. Plugin system where memory providers and context engines are swappable single-select components. Skills-from-experience loop. [FACT — repo docs] | It's TypeScript/Ink and agent-first, not chat-app-first. Take the architecture shape, not the code. |
| **Open WebUI / LibreChat** | Proven patterns: chat title auto-generation via a designated small "task model", client-side Pyodide code execution for artifacts, hybrid RAG (BM25 + vectors), per-chat model override UI. | Both are large frameworks (SvelteKit/React, heavy plugin systems) — exactly what Key Rule 2 forbids copying. |
| **llama-swap** (mostlygeek) | Go proxy, zero deps, OpenAI+Anthropic compatible, on-demand model start, **groups** (`swap: false` = pinned residents like embedder/classifier; `swap: true` = exclusive big-model slot), TTL auto-unload, per-model macros incl. `${PORT}`, web UI with metrics. Actively released (v201, Apr 2026). [FACT] | — |
| **llama.cpp router mode** (`--models-dir`, `--models-preset`, `--models-max`) | Native multi-model switching landed in llama-server; one resident model per worker, full unload/reload on switch. [FACT] | Doesn't replace llama-swap for us: no group pinning semantics as rich, and llama-swap also fronts non-llama.cpp backends later. See §4.1 decision. |
| **vLLM Semantic Router / RouteLLM** | Validates the thesis: route with a **tiny dedicated classifier** (they use ModernBERT), not a chat model with a giant prompt. Confirms reasoning-vs-non-reasoning split is the highest-value routing axis. [FACT] | Full Rust/Envoy deployment is massive overkill for single-user. |
| **Cactus Needle** *(evaluated, dropped — §4.7)* | 26M single-shot function-calling model, distilled from Gemini; emits one JSON tool call from query+tool list; trivially CPU-hostable. [FACT] | It is **single-shot only** — it picks and fills one tool call; it does not do multi-step tool loops or reasoning. Use as a *dispatcher assist*, never the agent loop. [FACT — Cactus's own framing] |
| **BrowserOS** | Chromium fork with built-in **MCP server exposing 31+ browser tools** (navigate, click, scrape, screenshot) to any MCP client; supports local model providers. Integration = we are an MCP client of the browser. [FACT] | Don't build browser automation ourselves; also don't depend on it for core chat (it's a separate install). |
| **opencode** | `opencode serve` = headless HTTP server with OpenAPI spec, basic-auth option, CORS flags; official `@opencode-ai/sdk` (TS) generated from that spec; config is plain `opencode.json` (custom providers = any OpenAI-compatible `/v1` base URL). [FACT] | Don't wire opencode *inside* the chat tool loop (the old handoff's open question). It stays a delegated, separately-surfaced coding agent. See §5.6. |

---

## 3. Target architecture

```
┌────────────────────────── Any machine on LAN ──────────────────────────┐
│  Browser → Web UI (TypeScript→ES modules + CSS, SSE)                   │
│  BrowserOS (optional, host machine) ← MCP → backend                    │
│  opencode serve (optional, host machine, for host-side coding)         │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ HTTP/SSE (LAN)
┌────────────────────── Ubuntu 26.04 GPU box ────────────────────────────┐
│  FastAPI backend (Python 3.12) — the only "app"                        │
│   ├─ chat orchestrator (stream loop, tool loop)                        │
│   ├─ router (override → rules → classifier)                            │
│   ├─ tools/ (search, files, bash-sandbox, browser-mcp, …) toggleable   │
│   ├─ rag/ + memory/  ── SQLite+FTS5 (text) + Qdrant (vectors)          │
│   ├─ gpu/ (nvidia-smi inventory → llama-swap config generator)         │
│   ├─ debug/ (per-turn trace store + SSE tap)                           │
│   └─ static file serving for web/                                      │
│                                                                        │
│  llama-swap (:8080) ── groups ──► llama-server instances (llama.cpp)   │
│   ├─ group "resident" swap:false → classifier, embed, utility (CPU)    │
│   │                                 + dispatcher (GPU1, 3070)          │
│   └─ group "gpu0-main" swap:true → one big model at a time on the 3090 │
│                                    (CUDA_VISIBLE_DEVICES=0, no split)  │
│                                                                        │
│  Qdrant (:6333, Docker) — vectors only, behind VectorStore             │
│  opencode serve (:4096) — coding agent on the box                      │
│  Artifact sandbox: Docker containers (server-side exec) + iframe/      │
│  Pyodide (client-side)                                                 │
└────────────────────────────────────────────────────────────────────────┘
```

**Deployment decision:** the backend runs *on* the Ubuntu box; the "application that communicates to the Ubuntu computer" (spec §3) is the browser. This removes an entire cross-machine API surface. Host-machine needs (opencode on host, BrowserOS) are reached from the backend over LAN as clients of *their* servers. [INFERENCE — simplest topology satisfying spec §3, §4, §6, §15]

**Web vs desktop (spec §4):** web app. Reasons: zero packaging, updates instantly, reachable from any device, and Odysseus proves the pattern. Desktop wrapper (Tauri) only if a Future need demands OS integration. [INFERENCE]

### 3.1 Tech stack (per Key Rules 1–3)

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI + httpx + uvicorn | Async SSE-native, already known, minimal deps. No LiteLLM, no LangChain, no agent framework. |
| Frontend | **TypeScript compiled with plain `tsc` → native ES modules. No React, no bundler, no framework.** | Owner approved TS ("light"). `tsc` is the entire build: `web/src/**/*.ts` → `web/js/**/*.js`, browser loads modules directly. All frontend source lives under `web/` — there is no top-level `src/`. Each view = one TS module + one CSS file; Rule 3 (no 1000-line files) enforced by module-per-component. Markdown render: `marked` + DOMPurify (small vendored libs); highlight: `highlight.js`. |
| Storage | **SQLite (WAL) + FTS5** for chats, messages, memories, chunk text, debug traces, settings-overlay — **plus Qdrant for vectors**, reached only through the `VectorStore` interface | Single-user scale for the relational half. **The sqlite-vec escape hatch fired in Phase 0** [FACT — measured, `docs/phase0-measurements.md` §6]: at 100k×768-dim vectors sqlite-vec's KNN p95 was 105ms against a ~50ms interactive bar, and the cost is brute-force scan, not query shape (the documented rowid-prefilter hybrid form was already used). Qdrant is one Docker service in the data plane — the price rev 2 refused to pay, now paid on evidence. `SqliteVecStore` stays in-tree as the fallback impl and the conformance-suite reference; nothing outside `app/rag/store.py` knows which is live. |
| Projects | Filesystem-first (`projects/<id>/instructions.md`, `docs/`) — the one part of the old app that worked | Keep, but thread/message storage moves to SQLite (filesystem JSON threads made model-attribution and search painful). |
| Inference | llama.cpp `llama-server` instances, managed by **llama-swap** | Spec §1. See §4.1. |
| Config | `config.yaml` (one file, checked in with defaults) + `.env` (secrets only) + generated `llama-swap.yaml` (machine-written, never hand-edited) | Two hand-edited files instead of three. Settings UI writes a `settings.local.yaml` overlay. |

If DOM complexity grows past ~30 components, revisit with lit-html (still no bundler) before ever reaching for React.

---

## 4. Feature designs (Critical list, spec order)

### 4.1 llama.cpp + llama-swap (spec §1, §2, §14)

**Decision: llama-swap in front of plain `llama-server` instances — not llama.cpp's native router mode.** Rationale: router mode keeps one resident model per worker and lacks llama-swap's group semantics (pin classifier/embed/utility/dispatcher resident while big models swap — Phase 0 confirmed this group semantics is load-bearing, see §4.1); llama-swap also fronts any OpenAI/Anthropic-compatible backend if we ever add vllm/whisper/SD servers, and has TTL, metrics, and a monitoring UI we'd otherwise write. [FACT re: capabilities; INFERENCE re: choice]

**Hardware (single box, locked):** the backend box (`ailab`) is Ubuntu 26.04, Ryzen 9, 64GB RAM, **RTX 3090 (24GB) = GPU0 + RTX 3070 (8GB) = GPU1**. A second LAN machine (PC2, one more 3070) remains *future*; llama-swap only manages processes on its own machine, so PC2 would be one more llama-swap plus a `model → endpoint` map in `config.yaml` — additive, not a redesign. Cross-box RPC stays rejected: it pools VRAM without parallelizing compute. [FACT — llama.cpp `tools/rpc/README.md`]

**Placement: Config B — solo-GPU pinning, CPU residents. The rev-5 tensor-split design is REVERSED.** [FACT — measured, `docs/phase0-measurements.md` §8, §11]

Three measurements killed the 32GB-pool design:

1. **`--tensor-split 3,1` is ~3x slower than it looked, and the rev-5 numbers were measured wrong.** Pinning a model to one card via a degenerate `--tensor-split 1,0` under `-sm tensor` measured ~40 tok/s on `chat-default`, while a clean `CUDA_VISIBLE_DEVICES=0` restriction of the *same model* gave 126–131 tok/s. `-sm tensor`'s cross-GPU sync machinery does not degrade gracefully to a single device. **Use `CUDA_VISIBLE_DEVICES`, never a degenerate tensor-split, to place a model.**
2. **Solo-GPU0 beats the split outright.** `chat-default` pinned solo to GPU0: **127–133 tok/s** vs **103–115 tok/s** for the same model spanning both cards. The split cost throughput *and* headroom.
3. **The split structurally breaks Config B.** A tensor-split big model claims ~5.9GB of the 3070, leaving ~2.2GB — so a GPU-resident `utility` (6.6GB) **fails to boot at all** (OOM). rev 5's own adopted benchmarks were taken in a shape incompatible with the resident set rev 5 assumed.

The locked shape:

| Device | Role | Contents | Placement mechanism |
|---|---|---|---|
| **GPU0 — RTX 3090 (24GB)** | the one big-model swap slot, one model at a time | `chat-default` (kept warm) ⇄ `coder` / `reasoner` / `vision` / `coder-small` | `CUDA_VISIBLE_DEVICES=0`, `-ngl 999`, **no `--tensor-split`** |
| **GPU1 — RTX 3070 (8GB)** | always-warm small-model lane | `dispatcher` (Hammer2.1-1.5b, ~1.07GB) | `CUDA_VISIBLE_DEVICES=1`, llama-swap group `swap: false` |
| **CPU — Ryzen 9** | never on a turn's synchronous critical path | `classifier`, `utility`, `embed` | `--device none -ngl 0`, `ttl: 0` |

Measured headroom at 32k ctx with `chat-default` on GPU0 and the dispatcher generating concurrently on GPU1: **~2.6GB free on GPU0**, zero errors over 100 requests, no throughput penalty to either model. KV-cache quantization (`--cache-type-k/v q8_0`) recovers only ~250MB on this MoE (GQA keeps KV small) at ~3% throughput cost — free insurance, not a lever to rely on. [FACT — §11]

**Why `utility` and `embed` are on CPU, and the contradiction that looks like an error.** §5 measured CPU `utility` in isolation at 3.3 tok/s (42s for a 128-token summary) and called it a hard fail. §8 Test 3 re-measured it under real concurrent load with real transcripts: **17.6–21.8s for a 100-token summary**. Both numbers are real; they answer different questions, and the decision rests on the concurrent one — a background job nobody awaits synchronously. Moving `utility` to the 3070 makes it ~13x faster but **degrades the dispatcher's own latency 5–7x** (0.10s → 0.24–0.73s) through compute contention, and shrinks GPU1 headroom to ~370MB. Dispatch latency is the one thing on the critical path, so it wins. `embed` on CPU costs 11.7–34ms/call, comfortably inside budget; an `embed-gpu` alias exists as an explicit opt-in (~2.5ms) for anyone who wants it and accepts the contention.

**Locked roster — every row measured.** Speed floor: nothing under ~25 tok/s at its working quant. Full detail and raw logs: `docs/phase0-measurements.md` §2, §9, §13.

| Alias | Model | Quant | VRAM | Measured | Where | Role |
|---|---|---|---|---|---|---|
| `chat-default` | Qwen3.6-35B-A3B (MoE) | Q4_K_M | 21.9GB @32k | **127–133 tok/s** (solo GPU0, concurrent) | GPU0, **kept warm** | General chat + native tool calling. Structured JSON: 100% parse / 100% schema-valid with `--reasoning off`. |
| `coder` | Qwen3-Coder-30B-A3B-Instruct | **Q5_K_M** | 24.5GB | 129 tok/s real | GPU0 (swap) | Locked quant: Q5 clears the ≥100 tok/s bar with headroom; Q6_K also passes but costs +3GB for marginal gain. **6/6 on debug-diagnosis** — beat both reasoners, including a trick prompt both missed. |
| `reasoner` | **Qwen3.6-35B-A3B, thinking mode** — *same blob as `chat-default`* | Q4_K_M | (shares the warm model) | 126 tok/s real | GPU0, **no swap** | 7/7 reasoning, 5/6 debug-diagnosis. Enabled at the request layer, not a separate swap entry — routing chat→reasoner costs zero load time. |
| `reasoner-alt` | DeepSeek-R1-Distill-Qwen-32B (dense) | Q4_K_M | 23.6GB | 44 tok/s | GPU0 (swap), off by default | Kept on disk, config-switchable. 7/7 reasoning but 3/6 debug-diagnosis with **two confidently-wrong fixes** — the failure mode that matters. |
| `vision` | Qwen3-VL-32B-Instruct + mmproj | Q4_K_M | 25.1GB | 21–43 tok/s | GPU0 (swap) | **6/6** vs Gemma-3-27b-it's 5/6; also cheaper prompt-side (54 vs 276 image tokens). |
| `coder-small` | **Qwen2.5-Coder-7B** (Qwen3-Coder has no dense 7B — it ships only as 30B-A3B and a "Next" 3B-active MoE) | Q4_K_M | 6.0GB | 114–121 tok/s | GPU0 (swap) | Fast fallback / parallel coding lane. |
| `dispatcher` | **Hammer2.1-1.5b** (was `needle`) | Q4_K_M | ~1.07GB | 0.07–0.21s/call, 260–306 tok/s | GPU1, resident | Single-shot tool-call emission **and** title generation (8/8 rubric, 0.042s). 79.0% call_f1 on a 6-tool registry, 63.75% on a deliberately hard 13-tool overlapping registry. |
| `utility` | Qwen3-8B | Q4_K_M | — (CPU) | 17.6–21.8s / 100-tok summary | CPU, resident | Summaries, compaction, memory review. **Not titles** — the dispatcher took that job. |
| `embed` | nomic-embed-text-v2-moe | Q4_K_M | — (CPU) | 11.7–34ms/call | CPU, resident | recall@1 96.67%, recall@5/10 100%. |
| `classifier` | Qwen3-1.7B | Q8_0 | — (CPU) | 0.283s/item | CPU, resident | **91.76%** on the real 6-category taxonomy — clears the ≥90% gate. Requires `--reasoning off` + a few-shot prompt (see §4.3). |

**Dropped, with reasons worth remembering:** *Cactus Needle 26M* — ~0.9–1.1s/call against a 50ms bar on its reference JAX server, and Cactus's production runtime that would fix that **cannot build on x86_64** (`cactus-kernels/CMakeLists.txt` hardcodes ARM NEON with no x86 path). *FunctionGemma-270M* — a real full finetune reached 88.3% call_f1 on a fresh holdout (beating Hammer's 79.0%) at 5–6x less VRAM, but 36.5% under 13-tool registry pressure and higher per-call latency (0.29–0.34s) despite higher raw tok/s. Kept as the documented secondary candidate; the finetune recipe and GGUF conversion are done and reusable. *Qwen3-32B dense* — redundant with the MoE. *Tool-retrieval pre-filtering* — the standard "tool-RAG" pattern measured *worse* (60.9% at top-k=5 vs 63.75% unfiltered), because retrieval itself misses the right tool 5–9% of the time and every miss is unrecoverable.

**Two hard operational rules that came out of Phase 0** — both belong in generated configs, not in prompts:

- **Suppress thinking at the server level, never with a prompt convention.** A `/no_think` suffix worked on Qwen3-1.7B and silently failed on Qwen3.6-35B-A3B and on one `qwen3-4b.gguf` conversion, which kept reasoning regardless and returned empty `content` at any budget. swapgen must emit **`--reasoning off`** for `classifier`, and the backend must be able to set it per-request for any structured-output call. This single flag moved structured-JSON output from 8/10 to 10/10 and cut latency from ~6–10s to under 1s.
- **Give thinking-capable models a real token budget.** ≥1024 floor, ≥4096 for reasoning/debug work. Under-budgeted calls return empty, which reads as a model failure and is not one. `max_tokens` defaults per routing alias belong in `config.yaml`.

**Measured swap latency** (§13) — this is what the `model_loading` SSE event has to cover: `chat-default` on GPU0 **cold 12.47s / warm 0.67s**; `dispatcher` **cold 3.48s / warm 0.03–0.18s**; `utility` on CPU **cold 20.54s / warm ~9.1–9.4s**. The rev-2 guess of "3–10s typical" was optimistic for a cold big-model load; `first_token_timeout_s` must exceed 12.5s (config default 30s is fine) and the UI must show the loading state rather than appearing hung.

**The live llama-swap config is fixed and verified (2026-07-23).** `/home/john/llm-stack/serving/llama-swap/config.yaml` now carries the shape above; the previous hand-written revision had four defects, each now a swapgen acceptance criterion:

1. **No `groups:` block** — every model fell into llama-swap's implicit default group, which swaps, so the CPU residents were evicted the moment a big model was requested. **Verified empirically on llama-swap v237** before the fix: requesting `chat-default` killed the running classifier. After the fix, `classifier` (CPU) + `chat-default` (GPU0) + `dispatcher` (GPU1) run simultaneously, and a big-slot swap to `coder` replaces only the big model.
2. **`--reasoning off` missing from `classifier`** — now present and verified returning non-empty `content` with `reasoning_content: None`.
3. **`dispatcher` pinned to GPU0** while GPU1 sat idle — now on GPU1 (`CUDA_VISIBLE_DEVICES=1`), 1303MB.
4. **CPU residents still initialized CUDA contexts** (~150–256MB per card each) despite `--device none -ngl 0`; `CUDA_VISIBLE_DEVICES=""` on those entries reclaims it. GPU0 now measures **21908 MiB / 24576 with `chat-default` at 32k ctx — the ~2.6GB headroom §11 predicted, restored exactly.**

`chat-default` also moved from 16384 to the plan's 32768 ctx target.

**Context-depth caveat (open, accepted):** `chat-default` recall is correct at every checkpoint from 2k to 32k, but **throughput degrades 52.9% by 32k** — a real fail of the ≤30% bar. This is a speed problem, not an accuracy one. Mitigation is compaction (§4.15) triggering earlier rather than a smaller context window; the trigger percentage is a config key and should be tuned against this curve, not guessed.

Old tier aliases (`coding-light/medium/heavy`, `reasoning-*`, `vision-*`) remain as **routing labels in config that point at this roster** — the router/Settings vocabulary survives, the model count drops from ~12 to ~10. Users can still add models per class in Settings.

**Default model / always-loaded (owner §4):** `chat-default` has `ttl: 0` and the backend re-warms it whenever the GPU0 slot has been idle on another model for N minutes (config, default ~10) — a ~20-line deterministic policy, so a fresh chat almost always gets an instant first token (warm 0.67s vs cold 12.47s). Because `reasoner` is now the *same blob* in thinking mode, the two highest-traffic routes never swap at all.

**Per-chat model switching (owner §5) — already solved by the architecture, no new machinery:** each chat row stores `model_override` (null = router decides). The composer's model picker writes it; every turn resolves `override ?? router(intent)` and passes that name in the OpenAI request's `model` field; **llama-swap does the actual load/swap transparently**. The only UX work: an SSE `model_loading` status event so the UI shows "loading <model>…" during a GPU0 swap (**measured: up to 12.5s cold**, not the 3–10s earlier guessed), and the per-message model label. Mid-chat switches are just the next turn's field value.

**GPU delegation (spec §14):** at backend startup, run `nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv` → GPU inventory endpoint → Settings UI lets the user assign each model to **a GPU or CPU**. A deterministic Python module (`gpu/swapgen.py`) renders `llama-swap.yaml` from `config.yaml` model entries + GPU assignments. **Placement is expressed as `CUDA_VISIBLE_DEVICES` in the entry's `env:`, never as `--tensor-split`** — Phase 0 measured the degenerate-split idiom at ~3x slower (§8), so swapgen must not emit it. Multi-GPU spanning stays available as an explicit opt-in for a model that genuinely does not fit one card; nothing in the locked roster needs it.

```yaml
# generated — do not hand-edit
healthCheckTimeout: 120
macros:
  llama: /home/john/llm-stack/engine/llama.cpp/build/bin/llama-server --host 0.0.0.0 --port ${PORT} --jinja
models:
  classifier:                       # CPU — --reasoning off is REQUIRED (see §4.3)
    cmd: ${llama} -m .../Qwen3-1.7B-Q8_0.gguf --device none -ngl 0 -c 4096 --reasoning off --temp 0
    ttl: 0
  utility:                          # CPU
    cmd: ${llama} -m .../qwen3-8b.gguf --device none -ngl 0 -c 8192
    ttl: 0
  embed:                            # CPU
    cmd: ${llama} -m .../nomic-embed-text-v2-moe.Q4_K_M.gguf --device none -ngl 0 --embedding -c 2048
    ttl: 0
  dispatcher:                       # GPU1 (3070) — resident, off the big slot
    cmd: ${llama} -m .../Hammer2.1-1.5b-Q4_K_M.gguf -ngl 999 -c 4096 --temp 0
    env: ["CUDA_VISIBLE_DEVICES=1"]
    ttl: 0
  chat-default:                     # GPU0 (3090) solo — no tensor-split
    cmd: ${llama} -m .../Qwen3.6-35B-A3B-UD-Q4_K_M.gguf -ngl 999 -c 32768
    env: ["CUDA_VISIBLE_DEVICES=0"]
    ttl: 0
  coder:
    cmd: ${llama} -m .../Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf -ngl 999 -c 16384
    env: ["CUDA_VISIBLE_DEVICES=0"]
groups:
  resident: { swap: false, exclusive: false, members: [classifier, utility, embed, dispatcher] }
  gpu0-main: { swap: true, members: [chat-default, coder, reasoner-alt, vision, coder-small] }
```

The `groups:` block is not optional decoration — **without it every model shares llama-swap's implicit default group and the residents cannot coexist with the big model.** Golden-file tests for swapgen must assert its presence.

Changing assignments → regenerate file → llama-swap config reload. **This is programmatic config writing, not AI-generated** (Key Rule 1 / Future §8 principle). The live config is `/home/john/llm-stack/serving/llama-swap/config.yaml`; its three known defects are listed above and are swapgen's acceptance criteria. [UNCERTAIN — llama-swap reload endpoint name, verify against the installed version in Phase 2]

**Model classes (spec §2):** general, coding, tool-call, reasoning, vision — all are just entries in `config.yaml` with a `class:` tag and a `gpu:` assignment; vision models add `--mmproj`. Benchmarks: `llama-bench` wrapped by a script in Phase 1 of the testing suite (spec Future §4 does the full suite; Critical only needs per-model tok/s sanity numbers shown in the debug panel).

**Client:** one thin `app/llm_client.py` speaking OpenAI chat-completions to llama-swap (`model` field selects the model; llama-swap handles load/swap). No scheduler code in our app at all — the entire old ModelScheduler problem is deleted. [FACT — this is llama-swap's core function]

Its `chat()` signature carries **`thinking: bool | None`** and **`max_tokens: int | None`** from Phase 1, even though nothing sets them until Phase 2. This is deliberate: `reasoner` is `chat-default`'s own blob with thinking enabled *at the request layer*, and every structured-output call needs reasoning suppressed per-request — so a Phase-1 client without those parameters would force a frozen-file reopening the moment Phase 2 starts. `None` means "use the server's `--reasoning` default"; the alias's `thinking` / `max_tokens` config values are what the router passes through.

### 4.2 Web application (spec §4)

- **Design target: mirror claude.ai web 1:1** (owner directive) — collapsible left sidebar (new chat, Chats, Projects, recents), centered chat column, right-side artifact/context panel, model picker in the composer, per-message model label, plus a Settings area and the Debug view (our additions). Build a static HTML/CSS mock of the claude.ai layout *first* and get it approved before any logic — UI parity was where the last build quietly diverged (Bug 3). Theme via one `theme.css` of custom properties (Future §3 themes = swap that file).
- Every view is a TS module under `web/src/views/` exporting `mount(el, state)` / `unmount()`. A ~200-line `web/src/router.ts` (hash-based) and a ~150-line `web/src/store.ts` (pub/sub state) — hand-written, no framework.
- SSE client with auto-reconnect and a hard rule learned from Bug 2: **every stream must terminate with `done` or `error`; UI shows "connection lost" if neither arrives.**

**Frozen chat contract (single source of truth — `docs/FEATURES.md` and `docs/PHASE_PROMPTS.md` both restate this, neither may diverge from it).** Adding an event or endpoint here is an owner decision, per `.cursor/rules/002-boundaries.mdc`.

| | Contract |
|---|---|
| Chat REST | `POST /api/chats` · `GET /api/chats` · `GET /api/chats/{id}/messages` · `POST /api/chats/{id}/messages` (SSE) · `POST /api/chats/{id}/model` · `POST /api/chats/{id}/attachments` · `DELETE /api/chats/{id}` |
| SSE events | `token {text}` · `model_loading {model}` · `tool_start {name, args_preview}` · `tool_result {name, content_preview, is_error}` · `title {chat_id, title}` · **`done {message_id, model, usage, route, citations, context}`** · **`error {kind, detail}`** |
| Terminal rule | exactly one `done` **or** one `error`, always, enforced in a `finally` block |
| Other SSE streams | non-chat relays (opencode session events `oc_event`, debug tap `span`/`log`) carry their own payload names but obey the same terminal rule |
| Span stages | flat snake_case, no dots: `route`, `llm_request`, `llm_stream`, `sse_emit`, `swap_wait`, `db`, `tool`, `dispatcher`, `search`, `fetch`, `rag_ingest`, `rag_retrieve`, `memory_inject`, `memory_review`, `project_context`, `attachment_extract`, `compaction`, `title`, `summary`, `config_load`, `db_migrate`, `gpu_inventory`, `swapgen`, `exec`, `browser`, `opencode_session`, `bench` |

Route decision, citations, and token usage ride the `done` payload rather than getting their own events — one terminal payload is easier to keep honest than five optional mid-stream ones. Artifacts are detected **client-side** from the finished message text (`web/src/artifacts/detect.ts`), so there is no `artifact` SSE event. Thinking tokens, when a thinking-enabled alias is routed, arrive inside `token` and are tagged in the `done` payload — the Debug view reads them from the span, not the stream.

### 4.3 Smart router (spec §5)

Three layers, strictly ordered; every decision emitted to the debug panel with source + latency:

1. **Manual override** — per-chat model picker always wins (spec: "user can manually select").
2. **Deterministic rules** — attachments force intents (image→vision, code file→coding); config keyword rules (word-boundary, 2+ words). Cheap, transparent, no model.
3. **Classifier** — Qwen3-1.7B-Q8_0, CPU-resident, **measured 91.76% on the taxonomy below** (`docs/phase0-measurements.md` §13). Three things are load-bearing and none of them are optional:
   - **`--reasoning off` at the server level.** Without it the model spends its whole completion budget inside `<think>` and returns empty `content`. A `/no_think` prompt suffix is *not* an acceptable substitute — it worked on this checkpoint and silently failed on two others.
   - **A few-shot prompt targeting the observed confusions**, not generic examples. The two examples that closed the last 4 points were live-data-without-a-tool-name (`stock price`, `weather`) and file-search-vs-code-writing (`grep`, `find files`).
   - **`response_format: json_schema` (GBNF-enforced)**, making malformed JSON structurally impossible — so the old build's 4.4k-token defensive prompt collapses to a ~600-token prompt + few-shots. [FACT — llama.cpp supports schema-constrained sampling]

   **Frozen output schema (re-frozen 2026-07-23 to match what was actually measured):**
   ```json
   {"class": "chat|chit_chat|code_task|tool_call_needed|reasoning_task|vision_task",
    "confidence": 0.0}
   ```
   Per-class accuracy: `chat` 100%, `reasoning_task` 100%, `vision_task` 100%, `chit_chat` 90%, `code_task` 88.2%, `tool_call_needed` 78.9%. The classifier **never names models and no longer emits `effort`** — effort and `needs_tools` are set by the deterministic rules layer, which is cheaper and testable without a model. `config.yaml`'s `routing:` table maps class → alias (`code_task → coder`, `reasoning_task → reasoner`, `vision_task → vision`, everything else → `chat-default`).

   *Why not a bigger classifier:* asked and answered. Qwen3-4B on the identical fixed prompt costs **~93.7s per single-word classification** vs the 1.7B's 0.283s — that specific gguf never honors reasoning-suppression flags. The fixed 1.7B clears the bar at a fraction of the cost; the gap was never model capacity, it was a scoring bug plus a missing flag.
   - Timeout 2s → default chat model. Confidence < threshold → default chat model, flagged in debug panel.
   - **Scoring rule for the eval harness (this cost 45 accuracy points once):** `chat` is a substring of `chit_chat`. Any scorer over this label set must match longest-label-first with word boundaries, or exactly — never plain `if label in text`.
4. **Optional upgrade path** [INFERENCE, Phase 6+]: a ModernBERT-style fine-tuned classifier head (vLLM Semantic Router's approach) if the generative classifier's accuracy plateaus; the router interface doesn't change.

### 4.4 opencode integration (spec §6) — division of labor, with corrections to the owner's proposal

Owner's proposal and where I push back:

1. **"In-chat coding artifacts (simple python script, debugging a file) go through opencode on Ubuntu."** — **Pushback: don't.** A simple script or single-file debug is one completion by the chat/coding model + a sandbox run; routing it through opencode adds a full agent loop (session create → agent plans → tool calls → file writes) for a task with no repo, multiplying latency and failure points for zero gain. The dividing line that works: **no workspace → chat model + artifact sandbox; real directory/repo → opencode session scoped to it.** "Debug this file" sits on the line: if the file was pasted/attached, it's chat-side; if it lives in a project/repo path on the box, delegate to opencode. [INFERENCE — this is also how Claude.ai itself splits artifacts vs. Claude Code]
2. **"App can use opencode's tool calling to search files etc (not sure)."** — **Correct to: no.** opencode's API is session-based (create session → prompt → agent acts); it is not a tool-RPC you call for one `grep`. [FACT — OpenAPI surface is sessions/messages/events] Our own `file_ops` tool (~100 lines, project-scoped) does file search directly — faster, deterministic, debuggable (Key Rule 1: if plain programming does the task, no AI in the loop).
3. **VS Code integration is indeed separate:** opencode's IDE extension talks to its own opencode instance; nothing for our backend to do beyond docs + a "open this project in VS Code + opencode" workflow doc.

Design:
- `opencode serve` as a systemd unit on the Ubuntu box (primary — spec: generation happens on Ubuntu); optionally also user-launched on the Windows host for host-side repos. Both registered in Settings by URL. No auth (owner: open LAN).
- opencode's `opencode.json` on both machines points its provider at llama-swap's `/v1` (custom OpenAI-compatible provider) — same local models. Written by our config generator, not by hand and not by AI (Future §8 rule).
- **Docs deliverable (owner §9):** `docs/opencode.md` includes switching opencode's provider between local llama-swap and **opencode zen** (its hosted gateway, the old "OpenCode Go") — an `opencode.json` provider/model edit + API key; document both directions.
- Web app surface: a "Code" area that (a) lists sessions via the OpenAPI API, (b) creates a session against a chosen directory, (c) streams session events into a viewer, (d) "Open in VS Code" deep-link. The router can *suggest* delegation; the user confirms — agent loops never nest silently.
- [UNCERTAIN]: opencode's event-stream endpoint shape and API stability across versions — pin the version, smoke-test in Phase 4 before building UI on it.

**Why opencode over Qwen Code (Alibaba's Claude-Code clone) — owner Q:** opencode stays the primary harness. It's the better fit for *this* app because (a) its `@opencode-ai/sdk` is a **type-safe TS client generated from the server's OpenAPI spec** — a clean match for our tsc-only frontend; (b) it's **provider-agnostic by design** (any OpenAI-compatible `/v1`), where Qwen Code is optimized for Qwen models first; and (c) one opencode server multiplexes many clients/sessions, while `qwen serve` binds **one workspace per process** (a port each) — awkward for a multi-project app. The one real caveat: an LLM vendor tunes its own harness for its own model first, and our `coder` **is** Qwen3-Coder-30B. So in **Phase 4, A/B the same Qwen3-Coder-30B running in opencode vs in Qwen Code** on 3 real tasks; if Qwen-Code-in-harness is materially better, add it as an *optional second harness* (both are OpenAI-compatible clients of llama-swap → one entry in the same provider registry as Future §5). Don't switch the plan on it now. [FACT — opencode SDK from OpenAPI; Qwen Code Qwen-optimized + one-workspace-per-daemon]

### 4.5 Projects (spec §7)

Mirror Claude.ai: project grid → project workspace (instructions, sources/files, project chats, project memory). Filesystem layout stays (`instructions.md`, `docs/`); ingestion → chunker → embed → Qdrant + FTS5, incremental on file mtime. Fixes old Bug 3 by design: app opens to a plain new chat; Projects is a nav item, not a gate.

### 4.6 Artifacts + sandboxed execution (spec §8)

Two tiers, both toggleable:

- **Tier 1 (client, Phase 3):** artifact panel rendering markdown/HTML/SVG/JS in a sandboxed `iframe` (`sandbox="allow-scripts"`, no same-origin), Python via **Pyodide** in a web worker. Zero server risk, covers Claude.ai-artifact parity. [FACT — this is Claude.ai's and Open WebUI's model]
- **Tier 2 (server, Phase 4):** `POST /api/exec` runs code in a **short-lived Docker container** on the box (`--network none`, mem/cpu/pids limits, read-only rootfs + tmpfs workdir, 30s timeout). Used by the `bash`/`run_code` tool and artifacts needing real deps. Images: `sandbox-python`, `sandbox-node`.

### 4.7 Tool calls (spec §9)

- Primary path: llama.cpp native tool calling (`--jinja` + model chat template) through the OpenAI `tools` API; orchestrator runs the accumulate-deltas → dispatch → append-result loop (max N iterations, N in config). The old spec's delta-merge logic was correct — reuse the *pattern*.
- **Dispatcher assist (spec §9.1) — the model changed, the architecture did not.** For models tagged `tool_call: weak` in config, the orchestrator routes the *call-emission step* to the resident `dispatcher` (**Hammer2.1-1.5b**, GPU1, 0.07–0.21s/call): query + tool schemas → one JSON call → orchestrator executes → result to the main model. Toggleable per model; the debug panel marks dispatcher-assisted turns and shows, per step, **who decided vs. who emitted**. The dispatcher is never the planner — anything that branches on a result stays with the main model.
- **Measured dispatcher accuracy, and what it means for the design** [FACT — §13]: **79.0% call_f1** on a realistic 6-tool registry, **63.75%** on a deliberately hostile 13-tool registry with three confusable name-trios (`web_search`/`_news`/`_images`, `fetch_url`/`_raw`, `file_read`/`_lines`/`read_file_metadata`), 98.8% parse rate. After few-shot disambiguation examples, the residual errors are **argument-fidelity only** (dropping "the"/"a" while copying query text into an argument), not wrong-tool selection — a much narrower problem. Design consequence: keep the tool registry's *names* semantically distinct, and treat the assist path as a per-model option, not the primary. The primary tool path remains the main model's own native tool calling, which is what `chat-default` is good at.
- **Two negative results worth not re-running.** (a) **Tool-retrieval pre-filtering** — embed the tools and the query, hand the dispatcher only the top-k — is a standard pattern and measured *worse* here (60.87% at k=5, 59.26% at k=8, vs 63.75% unfiltered): the retrieval step itself misses the right tool 5–9% of the time and every miss is unrecoverable, while the few-shot prompt already handles most real disambiguation. Possibly worth revisiting at a 50+ tool registry; untested there. (b) **Cactus Needle 26M is dropped** — ~0.9–1.1s/call on its reference JAX server against a 50ms bar, and the production C++/ONNX runtime that would fix that hardcodes ARM NEON with no x86_64 path, so it cannot be built on this box at all.
- **The finetune option is proven and shelved, not abandoned.** A full 250-example finetune of **FunctionGemma-270M** reached **88.3% call_f1 on a genuinely fresh, non-overlapping holdout** (an earlier 100% was memorization of a same-pool split — the honest number is the 88.3%), converted cleanly to GGUF, and runs at 5–6x less VRAM than Hammer. It still lost the head-to-head under registry pressure (36.5% vs 63.75%) and has higher per-call latency (0.29–0.34s vs 0.07–0.21s) despite ~2x the raw tok/s — fixed per-request overhead, not throughput. **Plan unchanged:** once the tool registry stabilizes at the end of Phase 3, re-run this comparison against the real schemas. The training script, the fresh-holdout generator, and the GGUF conversion (including the `vocab_size` 262144→262146 fix the upstream checkpoint needs) all already exist under `scripts/needle_training/`.
- Tools are one module each under `tools/`, self-describing (`name, schema, execute()`, `enabled` flag) — registry auto-discovers; toggling a tool off = config flag (Key Rule 6).
- Initial set: `web_search`, `fetch_url`, `file_ops` (project-scoped), `run_code` (Tier 2 sandbox), `browser` (BrowserOS MCP), `memory_save/search`.

### 4.8 RAG + memory (spec §10) — reference confirmed: **hermes-agent**

How hermes actually does it [FACT — hermes docs]: fact-based "holographic memory" in **SQLite + FTS5** (not a vector DB!), memories injected into the user message inside a tagged `<memory-context>` block, pluggable memory providers, and a **background self-improvement review** that after a turn may quietly save a memory or update a skill. Our adaptation:

- **RAG (documents):** per-project ingestion → heading-aware chunking (~512 tokens, 20% overlap; AST chunking for code via tree-sitter later) → embeddings (CPU-resident `embed`, nomic-embed-text-v2-moe, 11.7–34ms/call, recall@5 100% [FACT — §13]) → **Qdrant (vectors) + SQLite FTS5 (lexical)** → **hybrid retrieval** (vector + BM25, reciprocal-rank fusion) → top-k with source citations in the UI.
- **Memory (hermes-style facts):** discrete fact rows in SQLite (FTS5 + optional embedding), three scopes — user preferences/custom prompts (always injected, spec §17), project memories, global memories. Injected as a tagged context block, hermes-fashion. All visible/editable in Settings → Memory; nothing invisible.
- **Self-improvement loop (the part the owner flagged as "super cool"):** after a turn completes, a background job on the utility model reviews the transcript and may *propose* a memory write or an update to a skill/instruction file — proposals land in a review queue in the UI, auto-accept optional per scope. Phased: manual `memory_save` tool in Phase 3; background reviewer in Phase 5; skill *creation* (hermes' full loop) belongs with Future §7 custom skills. [INFERENCE — hermes writes silently; queue-first is safer for a system you're also debugging]
- Chat history embedded per-message-batch → "search my past chats" (spec §13).

### 4.9 Attachments (spec §11)

Upload endpoint → type sniff → extractor registry: text/code (direct), pdf (pymupdf), docx/xlsx/pptx (python-docx etc. or markitdown), images (→ vision model path), audio [Future]. Extracted text goes to context if small, to RAG-on-the-fly if large. Each extractor is one module (Key Rule 6).

### 4.10 Testing suite (spec §12)

- **Unit/integration:** pytest against the FastAPI app with a fake llama-swap (canned OpenAI responses) — router decisions, tool loop, SSE framing, extractors, swapgen output.
- **Contract tests:** golden SSE transcripts (a turn's full event sequence) diffed on change.
- **Router eval:** keep the old repo's one good idea — a labeled prompt→expected-route CSV + `eval_router.py` scoring script, run on classifier prompt/model changes.
- **E2E smoke:** Playwright, ~10 flows (send message, switch model, upload file, artifact render, debug panel populates), run against a fake-LLM backend so CI needs no GPU.
- **Live hardware check:** `scripts/preflight.py` — nvidia-smi present, llama-swap up, each configured model loads and answers 1 token, embeddings endpoint alive. Run on the box, not CI.
- Gate: no feature merges without its tests; CI = lint (ruff) + `tsc --noEmit` typecheck + pytest + Playwright-vs-fake.

### 4.11 Vector DB (spec §13) — **Qdrant**, behind the `VectorStore` interface (§3.1/§4.8). The Phase-0 100k-vector benchmark retired sqlite-vec as the primary: KNN p95 105ms vs the ~50ms bar, brute-force-scan-bound rather than query-shape-bound. `SqliteVecStore` remains in-tree as the fallback implementation and as the second target of the `VectorStore` conformance suite — the interface is what makes this a config swap rather than a rewrite, which is the whole reason it was specified up front.

### 4.12 BrowserOS (spec §15) — placement and role, with corrections to the owner's proposal

1. **Placement: host machine (Windows), not Ubuntu.** BrowserOS is a GUI Chromium fork — on the headless GPU box it would need a virtual display and you'd never see what the agent is doing. Host is where you already browse and stay logged in; that's the whole value (authenticated sessions, watching the agent). [INFERENCE from FACT that it's a desktop browser]
2. **"Browser agent so the app can do deep research" — pushback: browser-driving is the *wrong primary* for deep research.** Perplexity-style deep research is search-API fan-out + parallel HTTP fetch + synthesis — dozens of sources a minute, headless, robust. A browser agent reads one page at a time through a GUI. So: deep research (Future §1) runs on `search` + `fetch_url`; the **BrowserOS toolset is the escalation path** for what fetch can't do — JS-heavy pages, logged-in content (Gmail, dashboards), interactive tasks, and MCP-connector-style automation. Both exposed as tools; the model (or user toggle) picks.
3. Backend ships a generic **MCP client** (`tools/browser.py`) connecting to BrowserOS's built-in MCP server (31+ tools: navigate, click, scrape, screenshot) over LAN; exposed to capable models as the `browser` toolset, off by default, per-chat toggle (browser actions are consequential). BrowserOS can also independently point its own in-browser agent at llama-swap `/v1`.
4. [UNCERTAIN — MCP transport BrowserOS exposes (SSE vs streamable-HTTP vs stdio-only) and whether its MCP server accepts non-localhost connections; if localhost-only, a tiny relay on the host or an SSH tunnel bridges it. Verify in Phase 5 before UI work. Note: spec says "BrowserClaw" once — assuming BrowserOS per the URL given.]

### 4.13 Search (spec §16)

`search/` provider chain: **DDG primary** (`ddgs` lib, no key) → on rate-limit/empty → **Tavily** (key in `.env`). Old build's DDG-resilience doc showed DDG throttling is real — the fallback is automatic per-query, with the provider used shown in citations + debug panel. (Spec's "taily" read as Tavily.)

### 4.14 Settings + custom prompts / preferences (spec §17) — the control surface

**One coherent Settings area, not a file-vs-menu split (owner directive).** The old build's "edit a JSON file *and* use a menu" confusion is designed out by a strict rule: **`config.yaml` holds checked-in *defaults* only; the Settings UI is the authoritative surface for every user change and writes a `settings.local.yaml` overlay** (loader deep-merges overlay over defaults, then env-substitutes secrets from `.env`). You never *have* to hand-edit a file — anything editable in the file is editable in the UI, and the UI wins. Hand-editing `config.yaml` stays possible for power users, but it sets defaults, not live state.

Design target: mirror **Claude Code's settings + Odysseus's model "cookbook"** — a left-nav Settings page (its own route, `#/settings`) with tabs, each a small TS module (`web/src/views/settings/<tab>.ts`), all backed by `GET/PATCH /api/settings` with in-process hot-reload (no restart):

| Tab | Controls |
|---|---|
| **Models** | add/remove/enable a model per class; set alias → GGUF path → **device assignment (GPU index / CPU)** — placement writes `CUDA_VISIBLE_DEVICES`, never a tensor-split (§4.1) and context length; per-model quant; A/B notes. Writing here regenerates `llama-swap.yaml` via swapgen (§4.1) and reloads. Mirrors the roster table. |
| **Providers / endpoints** | **API keys** for remote providers (Anthropic, opencode zen, Kimi, Tavily) written to `.env` through the settings writer, never to `config.yaml`; **per-box endpoints** — the `model → endpoint` map so a model can live on this box or (future) a second box; llama-swap base URLs. |
| **Routing** | keyword rules, classifier on/off + confidence threshold, per-class default alias. |
| **Tools** | per-tool enable toggles (search, fetch, file_ops, run_code, browser, memory), BrowserOS MCP URL, search provider order + keys. |
| **Memory / prompts** | custom system prompts (per model class, per project), user preferences (always-injected), memory tiers view/edit, review-queue auto-accept per scope. |
| **Debug** | the master toggle for the Debug window + what it captures (store full prompts, GPU poll interval, trace retention) — see §4.16. |
| **Appearance** | theme (`theme.css` token set), show-thinking default. |

Prompt templates and preferences (spec §17) live under Memory/prompts; project memories are the per-project scope (§4.8). Secrets are the one thing the UI writes to `.env` (redacted on read-back); everything else is the `settings.local.yaml` overlay. This is the surface that must "account for different models and configurations" (multi-box, BrowserOS, remote providers) — each is just a tab-driven overlay edit, no code change.

### 4.15 Chat summaries, auto-title, compaction (spec §18)

Two models, split by who waits on the result [FACT — §12, §13]:

- **Titles → `dispatcher` (Hammer2.1-1.5b, GPU1).** 8/8 on the title rubric at **0.042s/call**, versus CPU `utility` at 1/8 and **43.5s** — the utility model hits the same thinking-budget trap as the reasoners and mostly returns nothing at a title-sized budget. A ~760x latency difference on something the sidebar shows immediately settles it.
- **Rolling summaries + compaction → `utility` (Qwen3-8B, CPU).** 17.6–21.8s per 100-token summary is fine for a job nobody awaits; this is the role CPU residency was chosen for.

Title post-processing is deterministic and lives in code, not in the rubric: `clean_title()` **truncates** overlong output and never penalizes a short title. The measured 8/8 depends on this — an earlier 4/8 was the rubric's fault, not the model's, for demanding an exact 5–8 word range with no recovery path. (Open, cosmetic: truncation is a naive word-cut, so ~3/8 titles end mid-phrase; clause-boundary truncation is a nice-to-have.) All background tasks; failures never block chat.

### 4.16 Debug window (spec §19 — critical, built FIRST not last)

**A separate window, not an embedded panel (owner directive).** Debug is its own route — **`#/debug`, opened in a standalone browser window/tab** (`web/src/views/debug.ts`), toggled on in Settings → Debug (§4.14). You run the app in one window and watch everything happen in the other, live. This keeps the chat UI clean (claude.ai parity) while giving a full instrument panel when you want it.

- **Backend (built first, Phase 1):** every turn gets a `trace_id`; every stage (route, rag, llm request/response, tool dispatch, swap wait, SSE emit) writes a span row to SQLite — timestamps, model, full prompt/response (toggle), token counts, latency, GPU snapshot. This is infrastructure every later feature must call (rule `003`/observability), so retrofitting is designed out.
- **Token count + latency come from llama.cpp, not our estimates (owner directive):** the `llm_client` reads llama.cpp's response `usage` (prompt/completion/total tokens) and `timings` (prompt_ms, predicted_ms, tokens-per-second) and stamps them on the `llm.request` span. The Debug window shows real prompt-eval vs generation tok/s and real token counts per turn — never a client-side guess.
- **What the window shows** (`/api/debug/stream` SSE + trace REST): a per-turn **waterfall** of spans; the **route decision + why** (override / rule / classifier, with the classifier's raw JSON and confidence); **exactly what each model was sent and returned** (raw prompt + completion, incl. reasoning/thinking tokens); **every tool call** — name, arguments, result, who emitted it (main model vs `dispatcher`); **token counts + latency per stage** (from llama.cpp); **llama-swap state** (loaded/loading model, proxied from its API); and an **nvidia-smi poll** (per-GPU VRAM + util). Filter by chat or trace_id.
- Failures never block chat: trace-write errors are caught and logged; the window degrades gracefully (e.g. "no GPU telemetry" if nvidia-smi is absent).

---

## 5. Phases

Each phase ends with: features **wired end-to-end** (no "adapter exists but not injected"), tests green, docs page written, demo checklist run on real hardware. No phase starts on top of an unintegrated one.

**Phase 0 — Ground truth (box + inference). ✅ COMPLETE 2026-07-23.** All model testing/benchmarking happened here (`docs/BENCHMARK_PLAN.md` → `docs/phase0-measurements.md`, condensed in `docs/PHASE0_FINDINGS_SUMMARY.md`). Delivered: the full roster benchmarked for VRAM/tok-s/quality at real context, the **placement decision (Config B: solo-GPU0 + CPU residents)** made from measured concurrent load rather than isolated synthetic numbers, swap latencies measured against a live llama-swap config, sqlite-vec benchmarked at 100k vectors and **rejected**, the classifier taken from a broken 45.88% to a passing **91.76%**, and a dispatcher chosen on a fair head-to-head after the originally-specified model proved unbuildable. A Phase-0.5 pass re-ran the concurrency, quality, and context tests after finding that the first pass's numbers came from a shape (tensor-split) incompatible with the config being chosen.

The durable output is not the numbers — it's `.cursor/rules/010-benchmark-eval-methodology.mdc`. Eight methodology bugs produced eight wrong published numbers before root-causing (a substring-matching scorer that cost 45 accuracy points; a `/no_think` convention that worked on one checkpoint and silently failed on others; token budgets that measured truncation and were read as quality; a rubric that punished a model for a violation the shipping code fixes anyway; a plausible mitigation that measured *worse*; two background evals racing for one port). **Read that rule before writing or trusting any eval in this repo.**

Carried into Phase 1 rather than closed: the live llama-swap config's three defects (§4.1), multi-user `--parallel N` throughput (untested), sustained-load thermal/throttle behavior (all benches were short bursts), dispatcher argument-fidelity (article-dropping when copying query text into arguments), and a stale `llama-server.service` that auto-respawns a leftover server holding ~10GB of VRAM — stopped, not disabled.

**Phase 1 — Skeleton with eyes. ← NEXT.** FastAPI app: config load/validate, `/health`, `llm_client`, SQLite schema, SSE chat endpoint against a fixed model, minimal chat UI (send/stream/history), **debug trace store + Debug panel**, error-path contract (`done`/`error` always). Testing harness + CI from day one. Exit: chat with any manually-picked model, every turn fully traced.

Phase-0 additions to Phase 1's scope: the llama-swap config is already fixed and verified (§4.1), so Phase 1 develops against a correct serving layer; **still open — disable the stale `llama-server.service`** (it is `enabled` and will respawn a VRAM-holding test server at boot; needs sudo, rule `008-remote-box`). `first_token_timeout_s` must exceed the **measured 12.47s cold load**, and the `model_loading` SSE event is Phase-1 work, not a Phase-2 polish item, because a 12-second silent wait is the difference between "loading" and "broken".

**Phase 2 — Routing + models control.** GPU inventory, swapgen (generated llama-swap config + reload), Settings UI (models, GPU assignment, toggles), router layers 1–3 with grammar-constrained classifier, router eval harness, auto-title/summaries. Exit: correct model auto-selected ≥90% on eval set; GPU reassignment without restart.

The ≥90% gate is **already met in the lab at 91.76%** — Phase 2's job is to reproduce it in the app, which means porting the exact prompt (few-shots included), the `--reasoning off` flag, and a scorer that matches labels longest-first with word boundaries. Re-deriving any of the three from scratch re-opens a solved problem. Note the margin is 1.76 points: `tool_call_needed` at 78.9% is the weakest class and the most likely regression source.

**Phase 3 — Substance.** Tools framework + web_search/fetch/file_ops, dispatcher assist, attachments pipeline, Projects (grid/workspace), RAG hybrid retrieval + citations, memory tiers, Tier-1 artifacts (iframe/Pyodide), chat compaction. Exit: Claude.ai-parity daily-driver for chat work.

**Operator pre-step for Phase 3 (nothing RAG-shaped starts before it):** Qdrant is a data-plane service this plan adopted on measurement but never installed. Bring it up on the box as a Docker container on `:6333` with a persistent volume, create the `chunks` and `messages` collections at the embed model's dimension, and record the version + resolved `dim` in `docs/phase0-measurements.md`. A `dim` that disagrees with the live `embed` model corrupts a collection silently, so config validates it at startup rather than trusting the value.

**Phase 4 — Code.** Docker exec sandbox + `run_code` tool, Tier-2 artifacts, opencode serve on box (+ optional host), Code area UI (sessions, delegation flow), opencode config generator. Exit: replaces Cursor for small/medium tasks.

**Phase 5 — Reach + hardening.** BrowserOS MCP tool, memory self-improvement reviewer, Tavily fallback polish, `llama-bench` panel, preflight script, sandbox audit (no backend auth — owner: trusted LAN, fully open; the *sandbox* still gets locked down because tool-executed code is not the owner), full docs (per-feature `docs/<feature>.md`: what/why/how-to-extend — Key Rule 7).

**Future (unordered, post-5):** **remote model providers (owner v2: opencode zen/"Go", Anthropic, Kimi — each is one entry in a provider registry with base URL + key in `.env`; llama-swap and remotes are all OpenAI-compatible endpoints to `llm_client`, Anthropic gets a thin adapter, so no LiteLLM needed even then)**, deep research pipeline, open-design, themes, benchmark suite, Obsidian, Google integrations, custom skills (+ hermes-style skill creation loop), one-click MCP add (programmatic `opencode.json`/our-config writer — already designed via generators).

---

## 6. Build discipline (Cursor + Claude Code)

- **Rules files** (rewrite, don't reuse old ones): `001-stack` (TypeScript+tsc only, no frameworks/bundlers, no Ollama, no LiteLLM — affirmatively phrased), `002-modularity` (module-per-feature, `enabled` flags, file size cap ~300 lines), `003-config` (all model/provider names from config; generated files never hand-edited), `004-observability` (every new pipeline stage must write a debug span + emit terminal SSE event), `005-integration` (a feature PR must include wiring + test + docs page — "built but not injected" is a rejected PR), `006-git` (worktrees, FILE SCOPE, no `git add .` — carry over, it worked).
- **Per-feature workflow:** design note (½ page: interfaces, config keys, debug spans, toggle) → approved → build in worktree → tests → wire → demo checklist → docs. The old repo died in the gap between "built" and "wired"; rule 005 exists to close it.
- **AI-generation guardrail (Key Rule 1):** generators (swapgen, opencode config, MCP registration), extractors, and SQL schema are deterministic hand-written code; agents implement against written interfaces, never invent config formats.

---

## 7. Resolved decisions (owner answers, 2026-07-20) + remaining unknowns

Resolved:
1. **Hardware + placement (Phase-0 measured, locked):** single box — RTX 3090 (GPU0) + RTX 3070 (GPU1) + Ryzen 9 + 64GB. **Config B:** big models solo-pinned to GPU0 via `CUDA_VISIBLE_DEVICES` (no tensor-split), `dispatcher` resident on GPU1, `classifier`/`utility`/`embed` CPU-resident. PC2 stays future; no cross-box RPC (§4.1).
2. **Memory reference:** hermes-agent, including the self-improvement idea (§4.8).
3. **Host:** Windows now, maybe Linux later — BrowserOS + optional opencode live there; backend stays on Ubuntu.
4. **Per-task model config:** user-configurable in Settings, same pattern as the current app's tier table (§4.1 roster keeps the aliases).
5. **Auth:** none — fully open on trusted LAN.
6. **Frontend:** TypeScript (tsc-only, no bundler). **UI:** claude.ai 1:1 mirror + Settings.
7. **Remote providers** (opencode zen, Anthropic, Kimi): v2/Future.
8. **Vector store (2026-07-23):** Qdrant behind `VectorStore`; sqlite-vec failed its Phase-0 gate (§3.1, §4.11).
9. **Dispatcher (2026-07-23):** `dispatcher` = Hammer2.1-1.5b; the alias `needle` is retired from the routing vocabulary (§4.7).
10. **Classifier schema (2026-07-23):** re-frozen to the measured 6-category taxonomy without `effort` (§4.3).
11. **Reasoner (2026-07-23):** `chat-default`'s own weights in thinking mode; DeepSeek-R1-Distill-32B kept as config-switchable `reasoner-alt` (§4.1).

Closed by Phase 0:
- ~~Quant + ctx fits on 24GB~~ — measured for every roster model at real context (§4.1).
- ~~sqlite-vec at 100k chunks~~ — measured, failed, Qdrant adopted.
- ~~llama.cpp device-flag spelling~~ — placement is `CUDA_VISIBLE_DEVICES` in `env:`, `--device none -ngl 0` for CPU.

Remaining unknowns (verification tasks, not blockers):
- llama-swap reload endpoint name (Phase 2, verify against the installed version).
- opencode API/event-stream stability across versions (pin it — Phase 4).
- BrowserOS MCP transport + non-localhost reachability from the Ubuntu backend (Phase 5).
- **Multi-user concurrency** (`--parallel N`): no test covers 2–3 simultaneous users on one `chat-default` server. Single-user is the stated target, so this is a limit to know, not a gap to close now.
- **Sustained-load thermals:** every benchmark was a short burst; a 10–15 minute continuous run would catch clock-throttling. Measure before trusting the tok/s numbers for long generations.
