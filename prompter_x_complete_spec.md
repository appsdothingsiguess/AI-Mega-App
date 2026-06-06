# Prompter X — Complete Build Specification

**Status:** Implementation Ready (all amendments applied)
**Date:** June 6, 2026
**Owner:** Joey
**Hardware:** AMD Ryzen 9 5950X (16C/32T) · 8GB VRAM RTX 3070 · WSL2/Docker
**Purpose:** This is the single source of truth for generating Cursor 3.0 agent prompts. Every design decision, protocol, code reference, and constraint lives here.

---

## 1. Project Definition

A personal AI platform that replaces Claude.ai (web) and adds deep research capabilities (Perplexity-style). Built by extending the existing **Prompter** codebase (FastAPI + React/Vite + filesystem projects). Implemented via Cursor 3 parallel agents guided by strict rules and skills.

### Core Principles

1. **Modularity over monolith** — every major component is swappable via a defined Protocol interface.
2. **No hardcoded model names, provider names, or tool names** anywhere in application logic — always resolved from Settings at runtime.
3. **Local-first or remote-first is a config decision**, not an architectural one.
4. **No over-engineering** — build what's needed, not what's theoretically elegant.
5. **Agent-built, human-architected** — Cursor agents do implementation; humans define contracts and boundaries.
6. **All Protocol methods that perform I/O must be async** — FastAPI is async-native.
7. **Streaming is the primary response path** — non-streaming is the exception.

### Swap Test

Every component must satisfy the swap test: can it be replaced without touching anything outside its own adapter file and the Settings config? If no, it is not modular enough.

---

## 2. Existing Codebase (What Exists Today)

### File Structure

```
app/
  __init__.py
  __main__.py
  chat_service.py          # DELETE — replaced by ChatOrchestrator
  clipboard_paste.py
  config.py                # OVERHAUL — new Settings class
  lmstudio_client.py       # DELETE — replaced by LiteLLM
  lmstudio_server_config.py # DELETE
  main.py                  # OVERHAUL — new routes, SSE endpoints
  message_parts.py
  paste_input.py
  project_manager.py       # KEEP — do not touch
  rag.py                   # PARTIAL KEEP — keep chunk_text(), delete _score_chunks()
  schemas.py               # EXTEND — add new schemas
  settings_store.py        # OVERHAUL — new settings schema
  terminal_input.py
  utils.py

web/
  index.html
  package.json
  src/
    api/client.ts          # OVERHAUL — SSE client
    App.tsx                 # EXTEND
    components/
      ChatView.tsx          # OVERHAUL — SSE streaming display
      InstructionsPanel.tsx  # KEEP
      MessageBubble.tsx      # OVERHAUL — streaming + tool events
      ProjectSidebar.tsx     # KEEP
      SettingsModal.tsx      # OVERHAUL — new settings schema
      SourcesPanel.tsx       # KEEP
      StatusBar.tsx          # EXTEND — model loading indicator

projects/                   # KEEP — filesystem-based project storage
tests/                      # REWRITE — all tests against new orchestrator
scripts/
```

### What's Kept Untouched

- `project_manager.py` — Project CRUD, threads, filesystem structure. Do not modify.
- `projects/` directory structure — filesystem-based, works as-is.
- `InstructionsPanel.tsx`, `ProjectSidebar.tsx`, `SourcesPanel.tsx` — UI components that don't touch the chat pipeline.
- `web/index.html`, `web/vite.config.ts`, `web/tsconfig.json` — build config.

### What's Deleted

| File | Reason |
|------|--------|
| `chat_service.py` | Replaced by `ChatOrchestrator` |
| `lmstudio_client.py` | Replaced by LiteLLM proxy |
| `lmstudio_server_config.py` | Replaced by LiteLLM config |
| `rag.py _score_chunks()` | Replaced by `VectorStore.search()` |
| All `test_lmstudio_*.py` | Dead code |
| All `test_chat_service.py` | Rewritten for ChatOrchestrator |

### Current Config (Being Replaced)

The existing `config.py` uses `pydantic_settings.BaseSettings` loading from `.env`. It has `lmstudio_mode`, `lmstudio_base_url`, `debug_prompts`, etc. This entire class is replaced by the new `Settings` class (Section 10).

### Current API Surface (Being Overhauled)

The existing FastAPI app in `main.py` has:
- `POST /api/projects/{id}/threads/{tid}/messages` → non-streaming JSON response
- Standard CRUD for projects, threads, docs, instructions
- LM Studio model management endpoints
- Settings GET/PUT

The new API replaces the chat endpoint with SSE streaming and removes all LM Studio endpoints.

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────┐
│                FRONTEND (Web UI)                 │
│   React/Vite (extend existing, not rewrite)      │
│   - Chat interface with SSE streaming            │
│   - Artifact rendering (code, markdown, PDF)     │
│   - Project manager (existing, extend)           │
│   - Model/tool selector UI                       │
└──────────────────┬──────────────────────────────┘
                   │ SSE (text/event-stream)
┌──────────────────▼──────────────────────────────┐
│            FASTAPI BACKEND                       │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  ChatOrchestrator (replaces ChatService)  │   │
│  │  1. Vision detection (pre-router)         │   │
│  │  2. Hybrid Router                         │   │
│  │  3. Model Resolver                        │   │
│  │  4. RAG retrieval                         │   │
│  │  5. Tool execution loop (true streaming)  │   │
│  │  6. Streaming response                    │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                 │
│  ┌──────────────▼───────────────────────────┐   │
│  │  ModelScheduler (VRAM management)         │   │
│  │  Serializes local model swaps via lock    │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                 │
│  ┌──────────────▼───────────────────────────┐   │
│  │  LiteLLM Proxy Layer                      │   │
│  │  (alias → actual endpoint/protocol)       │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                 │
│  ┌──────────────▼───────────────────────────┐   │
│  │  Service Layer (Protocol-backed, async)   │   │
│  │  - EmbeddingService Protocol              │   │
│  │  - SearchService Protocol                 │   │
│  │  - VectorStore Protocol                   │   │
│  │  - VisionService Protocol                 │   │
│  │  - PDFGenerator Protocol (Phase 2)        │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  MCP Tool Server (thin wrappers)          │   │
│  │  web_search | bash | file_ops             │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  Config Validation (startup)              │   │
│  │  Health Check Endpoint (/health)          │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
                   │
       ┌───────────┴────────────┐
       │                        │
┌──────▼──────┐        ┌───────▼──────────┐
│  LOCAL      │        │  REMOTE          │
│  OLLAMA     │        │  (any provider   │
│  (Docker)   │        │   via LiteLLM)   │
└─────────────┘        └──────────────────┘
```

---

## 4. Model Layer

### 4.1 Local Models (Ollama Docker — 8GB VRAM RTX 3070)

**VRAM Budget:**

| Role | Default | VRAM | Residency |
|------|---------|------|-----------|
| Router/Classifier | qwen2.5:1.5b | ~1.0 GB | `keep_alive=-1` (permanent) |
| Embedding | nomic-embed-text | ~0.5 GB | `keep_alive=-1` (permanent) |
| Main LLM slot (one at a time) | qwen3:8b / deepseek-r1:8b / qwen2.5-coder:7b / Qwen2.5-VL-3B | 3.3–5.2 GB | Managed by ModelScheduler |
| **Total ceiling** | | **~6.7 GB** (1.3 GB headroom) |

Vision model (`Qwen2.5-VL-3B`, 3.3GB) occupies the main LLM slot — cannot run simultaneously with qwen3:8b or deepseek-r1:8b.

**Critical constraint:** Only one large model loaded at a time. Router and embedder stay resident on GPU via `keep_alive=-1`.

**CPU offload rejected** — Classifier: 1.6s/req CPU vs ~7ms GPU (230x slower). Embedder: 371ms/chunk CPU. Both stay on GPU.

**Embedding model:** `nomic-embed-text` — hard limit of 2048 tokens (throws on overflow). Hard cap all chunks at 1500 tokens. Fallback: `qwen3-embedding:0.6b`.

**Ollama confirmed over LM Studio.** Delete `LMStudioClient` and `lmstudio_server_config.py`.

### 4.2 Remote Models (OpenCode Go)

OpenCode Go exposes two protocols — LiteLLM handles both:

| Protocol | Endpoint | Auth Header |
|----------|----------|-------------|
| OpenAI-compatible | `/zen/go/v1/chat/completions` | `Authorization: Bearer <key>` |
| Anthropic-compatible | `/zen/go/v1/messages` | `x-api-key: <key>` |

**Required on all requests:** `User-Agent: prompter-x/1.0` (prevents Cloudflare 403).

**Tool calling verified:** DeepSeek V4 Pro ✓, Kimi K2.6 ✓, Qwen3.7 Max ✓, MiniMax M2.5 ✓.

OpenCode Go is the current remote provider. Swapping to a different provider = `litellm_config.yaml` edit only.

### 4.3 LiteLLM Proxy Layer

Unified proxy between FastAPI and all model endpoints. Application code calls LiteLLM aliases only — never a provider directly.

```yaml
# litellm_config.yaml
model_list:
  # Local models (Ollama)
  - model_name: local/qwen3-8b
    litellm_params:
      model: ollama/qwen3:8b
      api_base: http://localhost:11434

  - model_name: local/qwen2.5-coder-7b
    litellm_params:
      model: ollama/qwen2.5-coder:7b
      api_base: http://localhost:11434

  - model_name: local/qwen2.5-vl-3b
    litellm_params:
      model: ollama/qwen2.5-vl-3b
      api_base: http://localhost:11434

  - model_name: local/deepseek-r1-8b
    litellm_params:
      model: ollama/deepseek-r1:8b
      api_base: http://localhost:11434

  # Remote models (OpenCode Go — OpenAI-compatible)
  - model_name: remote/deepseek-v4-pro
    litellm_params:
      model: openai/deepseek-v4-pro
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_API_KEY
      extra_headers:
        User-Agent: prompter-x/1.0

  - model_name: remote/kimi-k2-6
    litellm_params:
      model: openai/kimi-k2-6
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_API_KEY
      extra_headers:
        User-Agent: prompter-x/1.0
```

### 4.4 ModelScheduler (VRAM Management)

Singleton FastAPI dependency. All local model dispatch goes through it. Prevents OOM by serializing swaps behind an asyncio lock.

```python
class ModelScheduler:
    """Manages Ollama VRAM allocation. All local model calls go through this."""

    def __init__(self, ollama_url: str):
        self._url = ollama_url
        self._lock = asyncio.Lock()
        self._loaded_main: str | None = None
        self._resident: set[str] = set()  # populated from settings at init

    async def ensure_loaded(self, model: str) -> None:
        """Ensure model is loaded, evicting current main if needed."""
        if model in self._resident or model == self._loaded_main:
            return
        async with self._lock:
            if model == self._loaded_main:
                return
            if self._loaded_main and self._loaded_main not in self._resident:
                await self._unload(self._loaded_main)
            await self._warmup(model)
            self._loaded_main = model

    async def _unload(self, model: str) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(f"{self._url}/api/generate",
                              json={"model": model, "keep_alive": 0})

    async def _warmup(self, model: str, retries: int = 3, backoff: float = 1.0) -> None:
        """Load model with retry + exponential backoff."""
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        f"{self._url}/api/generate",
                        json={"model": model, "prompt": "", "keep_alive": -1},
                    )
                    resp.raise_for_status()
                    return
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                if attempt < retries - 1:
                    logger.warning(f"Ollama warmup attempt {attempt+1} failed: {e}. Retrying in {backoff}s")
                    await asyncio.sleep(backoff)
                    backoff *= 2
                else:
                    raise
```

### 4.5 Alias-to-Ollama Name Mapping

LiteLLM aliases (e.g., `local/qwen3-8b`) must map to Ollama model names (e.g., `qwen3:8b`) for the ModelScheduler. This mapping lives in `settings.json`:

```json
"ollama_model_names": {
    "local/qwen3-8b": "qwen3:8b",
    "local/qwen2.5-coder-7b": "qwen2.5-coder:7b",
    "local/qwen2.5-vl-3b": "qwen2.5-vl-3b",
    "local/deepseek-r1-8b": "deepseek-r1:8b"
}
```

```python
def _alias_to_ollama_name(self, alias: str) -> str:
    return self.settings.ollama_model_names[alias]
```

Config validation ensures every `local/*` alias in `settings.models` has a corresponding entry.

---

## 5. Request Routing Architecture

### 5.1 Hybrid Router

```
Incoming request
      │
      ▼
[Vision Check] ──image attached──► intent="vision", skip router
      │
   no image
      │
      ▼
[Keyword Rules] ──match──► { intent, tools[] }
      │                            │
   no match                        │
      │                            ▼
[Classifier Protocol] ──► { intent, tools[] }
                                   │
                                   ▼
                        [Model Resolver]
                        reads settings.models[intent]
                                   │
                                   ▼
                        [LiteLLM] → model + tools
```

**Vision detection** happens in the ChatOrchestrator before the router. Image attachment → intent forced to `vision`, classifier skipped.

**Layer 1 — Keyword Rules:** Config-driven, stored in `settings.json`. Uses word-boundary matching (`\b`). All patterns must be 2+ words to reduce false positives. Single-word ambiguous terms are handled by the classifier.

```python
import re

def matches_rule(message: str, patterns: list[str]) -> bool:
    normalized = message.lower().strip()
    for pattern in patterns:
        if re.search(r'\b' + re.escape(pattern) + r'\b', normalized):
            return True
    return False
```

```json
"routing_rules": [
  { "patterns": ["what's the weather", "weather forecast", "weather today", "weather in"], "intent": "web_search", "tools": ["web_search"] },
  { "patterns": ["make a pdf", "create a pdf", "generate a pdf", "export to pdf"], "intent": "pdf_gen", "tools": ["pdf_gen"] },
  { "patterns": ["deep research", "research report", "research and summarize"], "intent": "deep_research", "tools": ["web_search"] },
  { "patterns": ["run this command", "execute this", "run in terminal", "shell command", "run this script"], "intent": "bash", "tools": ["bash"] },
  { "patterns": ["list files", "find the file", "browse files", "find in my files"], "intent": "file_ops", "tools": ["file_ops"] },
  { "patterns": ["debug this", "fix this error", "fix this bug", "refactor this", "add a feature to"], "intent": "coding_advanced", "tools": [] },
  { "patterns": ["write a script", "make a function", "parse this csv", "analyze this data"], "intent": "coding_basic", "tools": [] }
]
```

**Layer 2 — Classifier Protocol:** Any classifier implementing the async interface. Current: `qwen2.5:1.5b` via Ollama.

```python
@runtime_checkable
class Classifier(Protocol):
    async def classify(self, message: str) -> ClassifierOutput: ...
```

**Classifier system prompt** lives in `settings.json` at `router.classifier_prompt`. Model identity preamble lives in the adapter only.

```python
class QwenClassifierAdapter:
    IDENTITY = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant.\n\n"

    async def classify(self, message: str) -> ClassifierOutput:
        prompt = self.IDENTITY + self.settings.router.classifier_prompt
        # ... call Ollama via httpx
```

**Classifier prompt template** (stored in `settings.json`):

```
You are an intent classifier. Output ONLY valid JSON: {"intent":"<intent>","model":"<model>","tools":[...]}.

Intents: coding_basic, coding_advanced, vision, general_chat, web_search, deep_research, bash, pdf_gen, file_ops

Intent boundaries — apply before defaulting to general_chat:
- web_search: ANY "look up"/"find out"/"search online"; current facts (weather, sports scores, showtimes, restaurants, prices); who is CEO/PM; news/headlines/guidelines. NOT vision (weather is web_search). NOT general_chat when internet lookup is needed.
- deep_research: literature review, compile findings, summarize academic papers/studies, research brief/synthesis, analyze trends across sources. NOT general_chat. NOT web_search for single quick facts.
- file_ops: find/locate/open/read/list/copy/move/delete LOCAL files/folders/repo/downloads — including "find the invoice PDF in downloads". Search codebase/project for text/function/TODO. NOT vision (disk search ≠ image OCR). NOT coding.
- vision: user attached "this image/photo/screenshot/chart/diagram" for classify/identify/describe/OCR/read labels. NOT weather. NOT finding PDFs on disk.
- pdf_gen: create/export/convert TO PDF — markdown, Word, images, presentation, repo docs. "Make a PDF research report" = pdf_gen (PDF is the deliverable). NOT deep_research when PDF output is requested.
- general_chat: explain concepts, draft emails, advice, summarize/explain content FROM a named file (README.md, config.yaml) — understanding text, not filesystem ops. NOT file_ops when goal is explain/summarize meaning.

Coding overrides boundaries: write/implement/create/add/scaffold NEW code = coding_basic. Fix/debug/review/add tests to THIS class/file/code = coding_advanced. NOT general_chat. NOT web_search unless user explicitly says look up/search online.

Rules:
- bash: Run/execute/test/start a terminal command or script (git, npm, pip, pytest, docker, python main.py, "run my script", "test on my pc"). Not writing code.
- file_ops: filesystem operations on local paths — see boundaries above.
- pdf_gen: output format is PDF; includes converting images into a single PDF.
- deep_research: multi-source analytical reports — see boundaries above.
- web_search: live/current internet facts — see boundaries above.
- vision: visual inspection of attached images — see boundaries above.
- coding_basic: write/create/implement new self-contained code, scripts, functions, schemas, boilerplate, isolated endpoints/hooks, regex, migrations, or small utilities.
- coding_advanced: fix/debug/review/refactor/optimize existing code, errors, stack traces, failing tests/CI, merge conflicts, PRs, codebase/project context, regressions, or performance bugs.
- general_chat: everything else — see boundaries above.

TOOLS — exact values only. Valid tools: web_search, bash, pdf_gen, file_ops, vision. Never use intent/model names as tools.
Tools map: coding_basic [] | coding_advanced [] | general_chat [] | web_search ["web_search"] | deep_research ["web_search"] | bash ["bash"] | pdf_gen ["pdf_gen"] | file_ops ["file_ops"] | vision ["vision"]

Models (copy exactly): coding_basic local/qwen2.5-coder-7b | coding_advanced remote/deepseek-v4-pro | general_chat remote/deepseek-v4-pro | vision local/qwen2.5-vl-3b | web_search/deep_research remote/kimi-k2-6 | bash/pdf_gen/file_ops local/qwen3-8b

Examples:
User: Look up the CEO of OpenAI -> {"intent":"web_search","model":"remote/kimi-k2-6","tools":["web_search"]}
User: Look up how to fix this Python error online -> {"intent":"web_search","model":"remote/kimi-k2-6","tools":["web_search"]}
User: What's the weather like today? -> {"intent":"web_search","model":"remote/kimi-k2-6","tools":["web_search"]}
User: Find the score of last night's Lakers game -> {"intent":"web_search","model":"remote/kimi-k2-6","tools":["web_search"]}
User: Summarize recent papers on protein folding -> {"intent":"deep_research","model":"remote/kimi-k2-6","tools":["web_search"]}
User: Literature review on federated learning privacy -> {"intent":"deep_research","model":"remote/kimi-k2-6","tools":["web_search"]}
User: Make me a PDF research report on AI safety -> {"intent":"pdf_gen","model":"local/qwen3-8b","tools":["pdf_gen"]}
User: Convert markdown README to PDF -> {"intent":"pdf_gen","model":"local/qwen3-8b","tools":["pdf_gen"]}
User: Convert these images into a single PDF -> {"intent":"pdf_gen","model":"local/qwen3-8b","tools":["pdf_gen"]}
User: Find the invoice PDF in my downloads -> {"intent":"file_ops","model":"local/qwen3-8b","tools":["file_ops"]}
User: Open package.json and show dependencies -> {"intent":"file_ops","model":"local/qwen3-8b","tools":["file_ops"]}
User: Search my codebase for TODO comments -> {"intent":"file_ops","model":"local/qwen3-8b","tools":["file_ops"]}
User: Open README.md and summarize it -> {"intent":"general_chat","model":"remote/deepseek-v4-pro","tools":[]}
User: Classify the document type from this image -> {"intent":"vision","model":"local/qwen2.5-vl-3b","tools":["vision"]}
User: Read the labels in this chart image -> {"intent":"vision","model":"local/qwen2.5-vl-3b","tools":["vision"]}
User: Run git pull origin main -> {"intent":"bash","model":"local/qwen3-8b","tools":["bash"]}
User: Add caching layer with Redis -> {"intent":"coding_basic","model":"local/qwen2.5-coder-7b","tools":[]}
User: Implement OAuth login in my app -> {"intent":"coding_basic","model":"local/qwen2.5-coder-7b","tools":[]}
User: Add unit tests for this class -> {"intent":"coding_advanced","model":"remote/deepseek-v4-pro","tools":[]}
User: Debug this Go error: IndexError -> {"intent":"coding_advanced","model":"remote/deepseek-v4-pro","tools":[]}
User: Write a regex to match email addresses -> {"intent":"coding_basic","model":"local/qwen2.5-coder-7b","tools":[]}
User: Fix this bug in my Python code -> {"intent":"coding_advanced","model":"remote/deepseek-v4-pro","tools":[]}

Classify:
```

**Inference params:** `temperature=0.0, top_k=20, top_p=0.8, repetition_penalty≤1.05, max_tokens=96`

**Classifier timeout fallback:** 3 seconds → fall back to `general_chat`.

```python
async def _route_with_fallback(self, message: str) -> RouteResult:
    try:
        return await asyncio.wait_for(self.router.route(message), timeout=3.0)
    except (asyncio.TimeoutError, ConnectionError) as e:
        logger.warning(f"Router failed ({e}), falling back to general_chat")
        return RouteResult(intent="general_chat", tools=[], confidence=0.0, source=RouteSource.CLASSIFIER)
```

**Layer 3 — Model Resolver:** Single lookup, no logic.

```python
def resolve_model(intent: str, settings: Settings) -> str:
    return settings.models.get(intent, settings.models["general_chat"])
```

### 5.2 Intent Labels (Stable Contract — Never Rename)

```
general_chat | web_search | deep_research |
coding_basic | coding_advanced | bash | pdf_gen | file_ops | vision
```

**coding_basic vs coding_advanced heuristic:**
- `coding_basic`: greenfield, self-contained ("write a script to...", "make a function that...")
- `coding_advanced`: references existing code, errors, files ("fix this bug", "debug", "refactor")

### 5.3 Default Model Assignments

```json
"models": {
  "general_chat":    "remote/deepseek-v4-pro",
  "web_search":      "remote/kimi-k2-6",
  "deep_research":   "remote/kimi-k2-6",
  "coding_basic":    "local/qwen2.5-coder-7b",
  "coding_advanced": "remote/deepseek-v4-pro",
  "bash":            "local/qwen3-8b",
  "pdf_gen":         "local/qwen3-8b",
  "file_ops":        "local/qwen3-8b",
  "vision":          "local/qwen2.5-vl-3b"
}
```

Remote-first for chat, advanced coding, research. Local for cheap mechanical tasks.

---

## 6. Shared Data Types

```python
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

@dataclass
class SearchResult:
    """Shared result type for SearchService and VectorStore."""
    text: str                          # snippet or chunk content
    source: str                        # URL or file path
    title: str = ""                    # page title or doc name
    score: float = 0.0                 # relevance score (0-1 normalized)
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata is opaque — only the originating adapter writes to it

@dataclass
class ClassifierOutput:
    intent: str        # intent label only — no model names
    tools: list[str]
    confidence: float

class RouteSource(StrEnum):
    KEYWORD = "keyword"
    CLASSIFIER = "classifier"
    VISION_OVERRIDE = "vision_override"

@dataclass
class RouteResult:
    intent: str
    tools: list[str]
    confidence: float
    source: RouteSource

@dataclass
class ToolCallDelta:
    """Accumulates streaming tool_call deltas."""
    id: str = ""
    name: str = ""
    arguments: str = ""

    def to_openai_format(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }
```

---

## 7. Service Protocols

Every swappable service has an async Protocol. Application code calls the Protocol — never the adapter directly.

### EmbeddingService

```python
@runtime_checkable
class EmbeddingService(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts. Raises ValueError if any text exceeds max_tokens().
        Callers MUST pre-check length. Adapter MUST validate and reject, never silently truncate."""
        ...
    def max_tokens(self) -> int: ...  # sync — returns a constant
```

Current adapter: `nomic-embed-text` via Ollama (GPU). Max tokens: 2048 (hard limit). Input cap: 1500 tokens.

### SearchService

```python
@runtime_checkable
class SearchService(Protocol):
    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]: ...
```

Current adapter: `DuckDuckGoSearchService` (web_search intent). Phase 2 adds `TavilySearchService` (deep_research intent).

**Phase 2 extension plan:** Add `async def deep_search(self, query: str, ...) -> DeepSearchResult` as a second method. Existing adapters won't break — they just won't implement `deep_search`. Use `hasattr()` or a separate `DeepSearchService` Protocol at that time.

### VectorStore

```python
@runtime_checkable
class VectorStore(Protocol):
    async def upsert(self, ids: list[str], embeddings: list[list[float]],
                     texts: list[str], metadatas: list[dict]) -> None: ...
    async def search(self, query_embedding: list[float], top_k: int,
                     filter: dict | None = None) -> list[SearchResult]: ...
    async def delete(self, ids: list[str]) -> None: ...
    async def count(self, filter: dict | None = None) -> int: ...
    async def close(self) -> None: ...
```

Current adapter: `QdrantAdapter` (Docker/WSL, `localhost:6333`).

### PDFGenerator (Phase 2)

```python
@runtime_checkable
class PDFGenerator(Protocol):
    async def generate(self, content: str, format: str) -> bytes: ...
```

### VisionService

```python
@runtime_checkable
class VisionService(Protocol):
    async def analyze(self, image_bytes: bytes, prompt: str) -> str: ...
    async def analyze_multi(self, images: list[bytes], prompt: str) -> str: ...
```

**Returns `str` (buffered), not streaming.** Acceptable for single-user (avg 1.54s). The frontend shows a loading indicator via `model_loading` SSE event during model swap. Vision inference returns as a single chunk event.

**Local-only** — OpenCode Go confirmed text-only as of June 2026. Current adapter: `Qwen2.5-VL-3B` via Ollama.

**Benchmarked capabilities:**
- OCR including handwritten text and dense two-column layouts: GO
- UI screenshot element extraction: GO
- Simple charts (bar, pie, KPI cards): GO
- Dense PDFs including financial tables and invoices: GO (3/3, avg 0.93, avg 1.54s)
- Multi-series line charts with many data points: MARGINAL
- GLM-OCR: REJECTED (timeout, 0.67 avg)

### Classifier Protocol

```python
@runtime_checkable
class Classifier(Protocol):
    async def classify(self, message: str) -> ClassifierOutput: ...
```

Current adapter: `qwen2.5:1.5b` via Ollama (GPU).

---

## 8. Streaming Contract

### SSE Event Protocol (Backend → Frontend)

```
Endpoint: POST /api/chat/{project_id}/{thread_id}
Response Headers: Content-Type: text/event-stream

Event types:
  data: {"type": "chunk", "content": "partial text"}
  data: {"type": "tool_call", "name": "web_search", "input": {...}}
  data: {"type": "tool_result", "name": "web_search", "output": {...}}
  data: {"type": "sources", "chunks": [...]}
  data: {"type": "model_loading", "model": "qwen2.5-vl-3b", "estimated_seconds": 34}
  data: {"type": "done", "usage": {"prompt_tokens": N, "completion_tokens": N}}
  data: {"type": "error", "message": "..."}
```

### LiteLLM Streaming Pattern

```python
async def _stream_completion(
    self, model_alias: str, messages: list[dict], tools: list[str]
) -> AsyncIterator[str]:
    response = await litellm.acompletion(
        model=model_alias,
        messages=messages,
        stream=True,
    )
    async for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content
```

---

## 9. ChatOrchestrator

Replaces `ChatService` entirely. The old `ChatService` is deleted, not modified. All old tests are rewritten.

```python
class ChatOrchestrator:
    """Central request handler. Coordinates routing, retrieval, tool execution, and streaming."""

    def __init__(
        self,
        router: HybridRouter,
        vector_store: VectorStore,
        embedding_service: EmbeddingService,
        vision_service: VisionService | None,
        model_scheduler: ModelScheduler | None,
        settings: Settings,
        projects: ProjectManager,
    ): ...

    async def handle_message(
        self,
        project_id: str,
        thread_id: str,
        user_content: str | UserTurn,
    ) -> AsyncIterator[str]:
        """Returns a streaming async iterator of SSE events."""
        turn = self._parse_turn(user_content)

        # 1. Vision check (before routing)
        if turn.has_images():
            intent = "vision"
            tools = []
        else:
            # 2. Route with timeout fallback
            route = await self._route_with_fallback(turn.text_for_retrieval())
            intent = route.intent
            tools = route.tools

        # 3. Resolve model alias
        model_alias = self.router.resolve_model(intent)

        # 4. Ensure local model loaded (if local)
        if model_alias.startswith("local/") and self.model_scheduler:
            await self.model_scheduler.ensure_loaded(
                self._alias_to_ollama_name(model_alias)
            )

        # 5. Vision path (separate from normal flow)
        if intent == "vision" and self.vision_service:
            images = [img.read_bytes() for img in turn.images()]
            if len(images) == 1:
                result = await self.vision_service.analyze(images[0], turn.text_for_retrieval())
            else:
                result = await self.vision_service.analyze_multi(images, turn.text_for_retrieval())
            yield json.dumps({"type": "chunk", "content": result})
            yield json.dumps({"type": "done", "usage": {}})
            return

        # 6. Retrieve context (if project has docs)
        retrieved = await self._retrieve(project_id, turn)
        if retrieved:
            yield json.dumps({"type": "sources", "chunks": retrieved})

        # 7. Build messages
        history = self.projects.get_thread_messages(project_id, thread_id)
        messages = self._build_messages(turn, retrieved, history, intent)

        # 8. Stream via LiteLLM (with true streaming tool execution loop)
        async for event in self._execute_with_tools(model_alias, messages, tools):
            yield event
```

### Tool Execution Loop (True Streaming)

MCP tools are dispatched by the ChatOrchestrator — LiteLLM does not discover MCP tools.

```
Flow:
1. Router determines intent + tools[] (e.g., ["web_search"])
2. ChatOrchestrator passes tool schemas to LiteLLM as OpenAI function definitions
3. LiteLLM streams response; text chunks go to frontend immediately
4. Tool call deltas are accumulated incrementally from the stream
5. When stream ends with tool calls: orchestrator dispatches to MCP tool / FastAPI service
6. Tool result appended to messages as tool_result
7. Loop continues — model gets results, may call more tools or respond
8. Max 5 iterations prevents runaway loops
```

```python
async def _execute_with_tools(
    self, model: str, messages: list[dict], tools: list[str],
    max_iterations: int = 5,
) -> AsyncIterator[str]:
    tool_schemas = [self._get_tool_schema(t) for t in tools] if tools else None
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        response = await litellm.acompletion(
            model=model, messages=messages,
            tools=tool_schemas, stream=True,
        )

        # Stream text chunks to frontend AS THEY ARRIVE
        # Accumulate tool_call deltas incrementally
        text_buffer = ""
        tool_calls: list[ToolCallDelta] = []

        async for chunk in response:
            delta = chunk.choices[0].delta

            if delta.content:
                text_buffer += delta.content
                yield json.dumps({"type": "chunk", "content": delta.content})

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    _merge_tool_call_delta(tool_calls, tc_delta)

        if not tool_calls:
            break

        assistant_msg = {
            "role": "assistant",
            "content": text_buffer or None,
            "tool_calls": [tc.to_openai_format() for tc in tool_calls],
        }
        messages.append(assistant_msg)

        for tc in tool_calls:
            yield json.dumps({"type": "tool_call", "name": tc.name, "input": tc.arguments})
            result = await self._dispatch_tool(tc.name, tc.arguments)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            yield json.dumps({"type": "tool_result", "name": tc.name, "output": result})

    if iteration >= max_iterations:
        yield json.dumps({"type": "error", "message": f"Tool loop hit {max_iterations} iteration limit"})

    yield json.dumps({"type": "done", "usage": {}})


def _merge_tool_call_delta(accumulated: list[ToolCallDelta], delta) -> None:
    """Merge streaming tool_call deltas by index."""
    idx = delta.index
    while len(accumulated) <= idx:
        accumulated.append(ToolCallDelta())
    if delta.id:
        accumulated[idx].id = delta.id
    if delta.function and delta.function.name:
        accumulated[idx].name = delta.function.name
    if delta.function and delta.function.arguments:
        accumulated[idx].arguments += delta.function.arguments
```

---

## 10. Settings Schema

Three config files: `settings.json` (structured), `.env` (secrets only), `litellm_config.yaml` (model routing).

### Complete Settings Fields

| Category | Key | Default |
|----------|-----|---------|
| **Models** | `models.general_chat` | `remote/deepseek-v4-pro` |
| | `models.web_search` | `remote/kimi-k2-6` |
| | `models.deep_research` | `remote/kimi-k2-6` |
| | `models.coding_basic` | `local/qwen2.5-coder-7b` |
| | `models.coding_advanced` | `remote/deepseek-v4-pro` |
| | `models.bash` | `local/qwen3-8b` |
| | `models.pdf_gen` | `local/qwen3-8b` |
| | `models.file_ops` | `local/qwen3-8b` |
| | `models.vision` | `local/qwen2.5-vl-3b` |
| **Ollama Model Names** | `ollama_model_names` | `{"local/qwen3-8b": "qwen3:8b", ...}` (see Section 4.5) |
| **Vision** | `vision.local_model` | `ollama/qwen2.5-vl-3b` |
| | `vision.remote_model` | `""` (unset — local only) |
| **Router** | `router.classifier` | `ollama/qwen2.5:1.5b` |
| | `router.classifier_prompt` | (full classifier template — Section 5.1) |
| | `router.rules_enabled` | `true` |
| **Embedding** | `embedding.model` | `ollama/nomic-embed-text` |
| | `embedding.max_tokens` | `1500` |
| **Search** | `search.providers.web_search` | `duckduckgo` |
| | `search.providers.deep_research` | `tavily` |
| | `search.tavily_api_key` | `""` |
| **Ollama** | `ollama.base_url` | `http://localhost:11434` |
| | `ollama.keep_alive` | `-1` |
| | `ollama.scheduler_enabled` | `true` |
| **OpenCode Go** | `opencode_go.base_url` | `https://opencode.ai` |
| | `opencode_go.api_key` | `""` |
| | `opencode_go.enabled` | `true` |
| **Qdrant** | `qdrant.url` | `http://localhost:6333` |
| **RAG** | `rag.chunk_size` | `512` |
| | `rag.chunk_overlap_ratio` | `0.2` |
| | `rag.top_k` | `5` |
| **Health** | `health.classifier_timeout_s` | `3.0` |
| | `health.ollama_fallback_to_remote` | `true` |
| **Logging** | `logging.level` | `INFO` |
| | `logging.file_enabled` | `true` |
| | `logging.subsystems` | all enabled |
| **Debug** | `debug.router_decisions` | `false` |

**Implementation:** `settings.json` for structured config, `.env` for secrets. Backend: `app/config.py` Settings class + `/settings` GET/PUT. Frontend: `SettingsModal.tsx` with tabbed sections.

---

## 11. RAG Pipeline

### Qdrant (Docker in WSL)

Docker server mode, `localhost:6333`. Embedded rejected — disk-bound on Windows storage.

**Benchmarks:**
- 50K vectors: 16.17s population (~3,092 vec/sec)
- Reconnect + collection access: ~0.02s
- Filtered search at 50K: 58ms → 2.57ms after payload index on `source`
- RAM for 50K chunks (768 dims): ~440MB

**Required:** Create payload index on `source` and filter fields before production use.

### Chunking

**Code (Python/TypeScript):** AST-based via `tree-sitter`. Chunk at function/class boundaries. Hard cap: 1500 tokens.

**Documents:** Sliding window, 20% overlap (`chunk_overlap_ratio = 0.2`), 512-token target. Respect heading boundaries. AST chunks do NOT use overlap — overlap config applies to document chunks only.

```python
overlap_tokens = int(settings.rag.chunk_size * settings.rag.chunk_overlap_ratio)
chunks = chunk_text(text, chunk_size=settings.rag.chunk_size, chunk_overlap=overlap_tokens)
```

### Component Migration Map

| Existing | Action |
|----------|--------|
| `lmstudio_client.py` | **Delete** — replaced by LiteLLM |
| `lmstudio_server_config.py` | **Delete** |
| `rag.py _score_chunks()` | **Delete** — replaced by `VectorStore.search()` |
| `rag.py chunk_text()` | **Keep** — add 1500-token guard + AST path for code |
| `ChatService` | **Delete** — replaced by `ChatOrchestrator` |
| Project management, threads | **Do not touch** |
| `SettingsModal.tsx` + `/settings` | **Overhaul** |

---

## 12. Search Provider Strategy

Provider selected by intent label at runtime from settings.

```json
"search": {
  "providers": {
    "web_search":    "duckduckgo",
    "deep_research": "tavily"
  },
  "tavily_api_key": "",
  "brave_api_key":  ""
}
```

Cascade-by-failure rejected — adds latency on every query. Intent-routing only.

---

## 13. MCP Tool Server

Tools are thin wrappers. Business logic lives in FastAPI services (which implement Protocols), not in MCP tool implementations. The ChatOrchestrator dispatches tool calls.

**Phase 1 tools:** `web_search`, `file_ops`, `bash`

**Deferred:** Agent swarm / multi-agent MCP coordination (v2). `pdf_gen` (Phase 2).

---

## 14. Frontend

**Extend existing React/Vite — do not rewrite.** Frontend is built last.

**New Phase 1 components:**
- SSE streaming message display (replaces current request/response chat)
- Model selector dropdown (shows per-intent defaults, allows per-conversation override)
- Tool activation toggles per conversation
- Artifact renderer (code blocks, markdown, PDF preview)
- Source citation display for RAG responses
- Model loading indicator (triggered by `model_loading` SSE event)

---

## 15. Logging & Debug

Replace single `DEBUG_PROMPTS` flag with structured named loggers.

| Logger | Captures |
|--------|----------|
| `prompter.router` | Every decision: input, rule/classifier output, resolved model + tools |
| `prompter.rag` | Retrieval: query, top-k results, scores, sources |
| `prompter.llm` | Every LLM call: model alias, token counts, latency, provider |
| `prompter.search` | Search calls: provider, query, result count, latency |
| `prompter.embedding` | Input length, latency, truncation warnings |
| `prompter.mcp` | Tool invocations: name, inputs, outputs, errors |
| `prompter.scheduler` | Model load/unload events, VRAM transitions, lock contention |

Log destinations: console (stderr, always in dev) + rolling file `logs/prompter.log` (10MB max, 3 rotations). Debug UI panel deferred to Phase 2.

---

## 16. Health Checks & Graceful Degradation

### Health Endpoint

```
GET /health → 200 if healthy, 503 with details if not

Response:
{
  "status": "degraded",   // "healthy" | "degraded" | "down"
  "services": {
    "ollama": {"status": "up", "loaded_models": ["qwen2.5:1.5b", "nomic-embed-text"]},
    "qdrant": {"status": "up", "collections": 3},
    "litellm": {"status": "up"},
    "remote_provider": {"status": "down", "error": "403 Forbidden"}
  }
}
```

### Degradation Rules

- **Ollama down** → remote-only mode. Local-intent models fall back to remote if `health.ollama_fallback_to_remote = true`.
- **Qdrant down** → skip retrieval, proceed without RAG. Log warning. Don't crash.
- **Remote provider down** → local-only for intents with local alternatives. Error for intents with no local fallback.
- **Classifier timeout (>3s)** → fall back to `general_chat`.

---

## 17. Config Validation

Runs at startup in FastAPI lifespan. Checks consistency across all three config files.

```python
async def validate_config(settings: Settings) -> tuple[list[str], list[str]]:
    errors = []    # Hard failures — refuse to start
    warnings = []  # Degraded — log and continue

    # Every intent's model alias must exist in litellm_config.yaml
    litellm_models = _parse_litellm_config(settings.litellm_config_path)
    for intent, alias in settings.models.items():
        if alias not in litellm_models:
            errors.append(
                f"settings.models.{intent} = '{alias}' but no matching "
                f"model_name in litellm_config.yaml"
            )

    # Every local/* alias must have an ollama_model_names entry
    for alias in settings.models.values():
        if alias.startswith("local/") and alias not in settings.ollama_model_names:
            errors.append(
                f"Alias '{alias}' has no entry in settings.ollama_model_names"
            )

    # Ollama reachability (if any local models configured)
    local_aliases = [a for a in settings.models.values() if a.startswith("local/")]
    if local_aliases:
        try:
            resp = await httpx.get(f"{settings.ollama.base_url}/api/tags", timeout=3)
            resp.raise_for_status()
        except Exception as e:
            errors.append(f"Ollama unreachable at {settings.ollama.base_url}: {e}")

    # Qdrant reachability — WARNING only (app degrades gracefully)
    try:
        resp = await httpx.get(f"{settings.qdrant.url}/collections", timeout=3)
        resp.raise_for_status()
    except Exception as e:
        warnings.append(
            f"Qdrant unreachable at {settings.qdrant.url}: {e}. "
            f"RAG retrieval will be skipped until Qdrant is available."
        )

    return errors, warnings
```

---

## 18. Build Phases

### Phase 1: Core Platform

**Definition of done:** Send a message → routes to correct model → streamed response → web search toggleable.

**Tasks (in dependency order):**

```
1. LiteLLM proxy wiring
   - litellm_config.yaml with Ollama + OpenCode Go aliases
   - ModelScheduler implementation (with retry on warmup)
   - Config validation (startup check, Qdrant as warning not error)
   - ollama_model_names mapping in settings
   Dependency: none

2a. EmbeddingService Protocol + nomic adapter (with hard reject on overflow)
    Dependency: Task 1 (needs Ollama via LiteLLM)

2b. VectorStore Protocol + Qdrant adapter
    Dependency: Task 2a (needs embeddings to test)

2c. SearchService Protocol + DDG adapter
    Dependency: none (DDG calls DuckDuckGo API, not LiteLLM)

3. Hybrid Router
   - Keyword rules layer (2+ word patterns, word-boundary matching)
   - Classifier adapter (existing trained qwen2.5:1.5b)
   - Model Resolver
   - RouteSource StrEnum
   Dependency: Task 1

4. ChatOrchestrator
   - Replaces ChatService (delete, don't modify)
   - True streaming tool execution loop (text streams immediately, tool deltas accumulated incrementally, max 5 iterations)
   - Integrates router, vector store, vision, streaming
   - Rewrite all ChatService tests
   Dependency: Tasks 1, 2a, 2b, 3

5. Streaming endpoint
   - SSE streaming from ChatOrchestrator to frontend
   - Health check endpoint
   Dependency: Task 4

6. web_search MCP tool
   Dependency: Task 2c

7. Settings overhaul (parallel bounded agent task)
   Dependency: none — runs in parallel from start

8. Frontend: SSE display + model selector + artifact rendering (last)
   Dependency: Tasks 4, 5, 7
```

**Parallel lanes from day one:**
- **Lane A:** Task 1 → 2a → 2b → 4 → 5 (critical path)
- **Lane B:** Task 2c → 6 (search, independent)
- **Lane C:** Task 3 (router, needs Task 1 for classifier)
- **Lane D:** Task 7 (settings, fully independent)

**Stopping condition:** Tasks 1–5 end-to-end before 6–8.

### Phase 2: Research Features

- Tavily adapter for deep_research (extends SearchService with `deep_search` method)
- Multi-step deep research pipeline (search → synthesize → cite)
- Source filtering UI
- Per-project document ingestion with AST chunking
- PDFGenerator Protocol + adapter
- PDF generation tool
- Debug UI panel (`/debug/last-turn` endpoint)

### Phase 3: Terminal Agent (Deferred)

**Do not start until Phase 1 is stable and Phase 2 is in active use.**

---

## 19. Cursor 3 Build Strategy

### Rule File Structure

```
.cursor/rules/
  001-base.mdc            # Stack, always-apply, <30 lines
  002-python-api.mdc      # FastAPI conventions, glob: app/api/**/*.py
                           # MUST include: "All chat endpoints stream via SSE.
                           #   ChatOrchestrator.handle_message() returns AsyncIterator[str]."
  003-interfaces.mdc      # Protocols must not be modified by agents
                           # MUST include: "All Protocol methods that perform I/O MUST be async."
  004-frontend.mdc        # React/Vite, glob: frontend/src/**/*
  005-security.mdc        # No .env, no hardcoded keys, always-apply
  006-no-scope-creep.mdc  # Agent boundaries, always-apply
                           # MUST include: "ChatService is DELETED. ChatOrchestrator replaces it."
  007-no-hardcoding.mdc   # Model/provider names in config only, always-apply
                           # MUST include: "Classifier system prompts live in settings.json.
                           #   Model identity preambles live in the adapter only."
```

### Key Cursor 3 Constraints

**Rules drift in long sessions.** Mitigation: `alwaysApply` rules ≤3 files ≤400 lines total, glob-based component rules, `.cursorignore` for legacy folders, clean git state before every session, one branch per task.

**Parallel agent rule:** One agent per bounded unit. No shared file surfaces. Validate diffs before merge. Shared config files (`pyproject.toml`, `settings.json`) — designate one owner.

**Negative phrasing penalty:** Rewrite prohibitions as affirmative instructions with rationale. Instead of "Don't hardcode model names," use "All model names must come from settings.json to enable zero-code provider swaps."

**Session continuity:** Cursor has no persistent session memory. Prefix new sessions with a continuity packet referencing this document and the current task plan.

**Architecture-as-Artifact pattern:** This document IS the architecture artifact. Every Cursor rule, skill, and agent contract references it rather than duplicating it.

**Three-tier boundary system for agents:**
- ✅ **Always:** Run tests, follow naming conventions, use Protocols for I/O
- ⚠️ **Ask first:** Schema changes, new dependencies, new config fields
- 🚫 **Never:** Edit `project_manager.py`, modify Protocol interfaces, hardcode model names, touch `.env`, commit secrets

### .cursorignore

```
projects/
.git/
node_modules/
__pycache__/
*.pyc
logs/
.venv/
```

---

## 20. Out of Scope

- Agent swarms / multi-agent coordination (v2)
- Terminal coding agent / OpenCode CLI (Phase 3)
- Multi-user support
- Mobile app
- Vector store migration tooling
- Brave Search (deferred)
- CPU offload for classifier/embedder (benchmarked and rejected)

---

## 21. Resolved Decisions & Test Results

| Question | Resolution |
|----------|-----------|
| nomic-embed-text context window | Hard 2048-token limit, throws on overflow. Chunk cap: 1500 tokens. |
| Qdrant: embedded vs Docker | Docker in WSL. 3,092 vec/sec, 0.02s reconnect, 2.57ms filtered search at 50K. |
| OpenCode Go tool calling | All 4 tested models pass: DeepSeek V4 Pro, Kimi K2.6, Qwen3.7 Max, MiniMax M2.5. |
| Ollama vs LM Studio | Ollama. Native embeddings, programmatic model management, headless, LiteLLM native. |
| Search provider strategy | Intent-routing: DDG → web_search, Tavily → deep_research. Cascade rejected. |
| OpenCode Go auth | Two shapes: Bearer (OpenAI-compatible), x-api-key (Anthropic-compatible). User-Agent required. |
| Remote vs local default | Remote-first for chat and coding_advanced. Local for mechanical tasks. All config-driven. |
| Vision model | Qwen2.5-VL-3B local-only. GO on OCR, UI, charts, dense PDFs (0.93 avg, 1.54s avg). GLM-OCR rejected. |
| coding_basic model | qwen2.5-coder:7b — purpose-built for code generation, better than deepseek-r1:8b for greenfield. |
| CPU offload: classifier | REJECTED. 1.6s/req CPU vs ~7ms GPU (230x slower). |
| CPU offload: embedder | REJECTED for query path. 371ms/chunk CPU. Tolerable for batch but not per-query. |
| VRAM management | ModelScheduler with asyncio lock. Classifier + embedder permanent GPU. Main slot managed. |
| Streaming | SSE required. ChatOrchestrator returns AsyncIterator[str]. True streaming in tool loop. |
| Config architecture | Three files: settings.json, .env, litellm_config.yaml. Startup validation. |
| ChatService refactor | Full rewrite into ChatOrchestrator. Delete and replace. |
| Keyword matching | Word-boundary regex. 2+ word patterns only to reduce false positives. |
| Classifier prompt location | Shared template in settings.json. Identity preamble in adapter only. |
| chunk_overlap type | Stored as ratio (0.2), converted to integer tokens at call site. Applies to document chunks only, not AST. |
| Qdrant startup validation | Warning only — does not block boot. Runtime degradation handles Qdrant outage. |
| Tool loop iteration limit | Max 5 iterations. Prevents runaway loops from misbehaving models. |
| RouteResult.source | StrEnum (RouteSource), not magic strings. |
| Embedding overflow behavior | Adapter raises ValueError. Never silently truncates. |
| DDG adapter dependency | Independent of LiteLLM. Can be built and tested without any model infrastructure. |
| ModelScheduler warmup | Retry with exponential backoff (3 attempts). |
| VisionService streaming | Returns str (buffered). Acceptable for single-user at 1.54s avg. |
