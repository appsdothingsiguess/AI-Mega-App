"""Application configuration from settings.json, environment, and .env secrets."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CLASSIFIER_PROMPT = """You are an intent classifier. Output ONLY one JSON object with exactly four keys: {"intent":"<intent>","model":"<model>","tools":[...],"confidence":<0.0-1.0>} — no duplicate keys, no prose. Never answer the user's question; only classify.

"confidence" is YOUR certainty that "intent" is correct: 0.9-1.0 for unambiguous matches, 0.6-0.8 when boundaries are debatable, below 0.5 only when genuinely unsure. Always include a numeric confidence — never omit it or default to 0.

Intents: coding_basic, coding_advanced, vision, general_chat, web_search, deep_research, bash, pdf_gen, file_ops, reasoning_medium, reasoning_heavy

Intent boundaries:
- bash: ANY shell execution request — message starts with/centers on Run/Execute/Start plus a CLI (git, npm, pnpm, pip, poetry, pytest, docker, curl, python, black, make, ls, rg). "Execute pytest -q", "Execute npm ci && npm test", "Execute poetry install and then poetry run pytest", "Execute pip install -e .", "Run git checkout -b…", "Run pnpm install then pnpm lint", "Execute make migrate…", "Execute ls -la…", "Execute rg -n …" = bash, NOT coding_*, NOT file_ops. Tools=["bash"] only.
- coding_basic: write/implement/create NEW code/scaffolding/regex/boilerplate/migrations/new endpoints/Dockerfiles/hooks/helpers/JSON Schema. Tools=[] ALWAYS. "Implement OAuth login" = coding_basic. Interview advice ≠ coding. Opening/searching files = file_ops.
- coding_advanced: fix/debug/review/refactor/optimize EXISTING code/errors/CI/build failures/panics/exceptions/PRs/stack traces/unit tests for an existing class / race conditions / slow SQL on an existing query / tangled hooks. Tools=[] ALWAYS — empty array, period. Never add bash, web_search, file_ops, pdf_gen, or the four-tool suite — diagnosing a pytest/CI/Docker/flake8 failure still tools=[].
- web_search: look up / search online / live or local facts (weather, scores, prices, who is CEO/PM right now, showtimes, restaurants, address/hours, market cap). NOT general_chat.
- deep_research: lit review / compile findings / papers / research brief across sources. Tools=["web_search"] ONLY. NOT product compare advice (general_chat). NOT analyzing incentives/pricing/auctions/org redesign (those are reasoning_heavy). NOT migration plans (reasoning_*).
- reasoning_medium: puzzles/riddles (river-crossing, truth-teller/liar, switches-and-bulbs — stay medium even with "carefully"/"with justification"), "think through"/"work through"/"solve:"/"step through", fair-division/chores, ambulance triage, everyday multi-constraint planning/scheduling (study schedule, weekend itinerary, conflicting meetings), queue-depth diagnosis step-through. Tools MUST be exactly ["web_search","bash","pdf_gen","file_ops"] — copying deep_research's ["web_search"] alone is WRONG. NOT general_chat. NOT reasoning_heavy unless multi-system production root-cause/incident forensics or an org-wide irreversible migration under audit.
- reasoning_heavy: deep multi-system root-cause / hard multi-constraint migration/ops/capacity/SLA/staffing plans / phased decommission with dual-write / analyzing second-order business effects / game-theoretic auction design / competing incentives in org redesign / canary-vs-ticket production anomalies / consistency-vs-availability deep tradeoff. Tools MUST be exactly ["web_search","bash","pdf_gen","file_ops"] — never ["web_search"] alone. NOT papers/lit-review (deep_research).
- file_ops: find/locate/open/read/list/copy/move/delete LOCAL files/folders; search/grep codebase/tree for a symbol/class/function/TODO/usage; show a yaml service block — even when the target is source code. "Open package.json and show dependencies" / "Search the project for enabled_tools usages" = file_ops. NOT coding_*. NOT Run/Execute shell (bash). NOT vision. NOT general_chat.
- vision: user refers to THIS image/photo/screenshot/chart/spreadsheet-screenshot attached for OCR/classify/describe/compare. "Extract table cells visible in this spreadsheet screenshot" = vision. Disk PDF/find ≠ vision.
- pdf_gen: create/export/convert TO PDF (checklists, cover letters, reports, combining images into a PDF). Converting images→PDF = pdf_gen not vision.
- general_chat: explain a concept/term, draft email, advice, brainstorm ("how should I prioritize backlog"), discuss tradeoffs/pros-cons in the abstract, compare tools for a side project, summarize meaning of a named file. NOT live/current/local facts (web_search). NOT file_ops. NOT puzzles/scheduling (reasoning_medium). NOT coding.

Models — always copy the current value shown for the matched intent, verbatim, do not invent or reuse another intent's value:
coding_basic {{MODEL:coding_basic}} | coding_advanced {{MODEL:coding_advanced}} | general_chat {{MODEL:general_chat}} | vision {{MODEL:vision}} | web_search {{MODEL:web_search}} | deep_research {{MODEL:deep_research}} | bash {{MODEL:bash}} | pdf_gen {{MODEL:pdf_gen}} | file_ops {{MODEL:file_ops}} | reasoning_medium {{MODEL:reasoning_medium}} | reasoning_heavy {{MODEL:reasoning_heavy}}

Examples (model values below are illustrative placeholders — always use the current values from the Models line above, not these):
User: Look up the CEO of OpenAI -> {"intent":"web_search","model":"{{MODEL:web_search}}","tools":["web_search"],"confidence":0.95}
User: Who is the Prime Minister of Canada right now? -> {"intent":"web_search","model":"{{MODEL:web_search}}","tools":["web_search"],"confidence":0.95}
User: Find restaurants open late in downtown Denver -> {"intent":"web_search","model":"{{MODEL:web_search}}","tools":["web_search"],"confidence":0.95}
User: What movies are playing near me this weekend? -> {"intent":"web_search","model":"{{MODEL:web_search}}","tools":["web_search"],"confidence":0.95}
User: Search for today's top tech headlines -> {"intent":"web_search","model":"{{MODEL:web_search}}","tools":["web_search"],"confidence":0.95}
User: Find the address and hours for the nearest DMV -> {"intent":"web_search","model":"{{MODEL:web_search}}","tools":["web_search"],"confidence":0.95}
User: Compare SQLite and Postgres for a side project -> {"intent":"general_chat","model":"{{MODEL:general_chat}}","tools":[],"confidence":0.95}
User: What does HTTP 429 mean? -> {"intent":"general_chat","model":"{{MODEL:general_chat}}","tools":[],"confidence":0.95}
User: How should I prioritize backlog items in a solo project? -> {"intent":"general_chat","model":"{{MODEL:general_chat}}","tools":[],"confidence":0.95}
User: Summarize recent papers on protein folding -> {"intent":"deep_research","model":"{{MODEL:deep_research}}","tools":["web_search"],"confidence":0.95}
User: Literature review on federated learning privacy -> {"intent":"deep_research","model":"{{MODEL:deep_research}}","tools":["web_search"],"confidence":0.95}
User: Compile findings on RAG benchmarks across sources -> {"intent":"deep_research","model":"{{MODEL:deep_research}}","tools":["web_search"],"confidence":0.95}
User: Work through this probability word problem carefully -> {"intent":"reasoning_medium","model":"{{MODEL:reasoning_medium}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Work through this combinatorics problem with clear intermediate steps -> {"intent":"reasoning_medium","model":"{{MODEL:reasoning_medium}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Reason step by step: where should the ambulance go first? -> {"intent":"reasoning_medium","model":"{{MODEL:reasoning_medium}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Reason about fair division of chores among four roommates -> {"intent":"reasoning_medium","model":"{{MODEL:reasoning_medium}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Step through diagnosing why a queue depth grows then collapses daily -> {"intent":"reasoning_medium","model":"{{MODEL:reasoning_medium}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Think through tradeoffs for a 2-week migration -> {"intent":"reasoning_medium","model":"{{MODEL:reasoning_medium}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Deep plan: migrate encrypted PII stores under auditor live review -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Design a multi-constraint capacity plan for 10x holiday traffic -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Multi-constraint schedule: factory lines, SLA penalties, and staffing rules -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Plan a phased decommission of a monolith with dual-write invariants -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Analyze second-order effects of introducing usage-based pricing -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Deep analysis of competing incentives in this org redesign scenario -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Reason through game-theoretic auction design for limited GPU inventory -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Deep tradeoff analysis: consistency vs availability for sync-critical booking -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Do a root cause analysis of this multi-system failure -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Analyze why canary metrics look green while customer ops tickets spike -> {"intent":"reasoning_heavy","model":"{{MODEL:reasoning_heavy}}","tools":["web_search","bash","pdf_gen","file_ops"],"confidence":0.95}
User: Convert these images into a single PDF -> {"intent":"pdf_gen","model":"{{MODEL:pdf_gen}}","tools":["pdf_gen"],"confidence":0.95}
User: Make me a PDF research report on AI safety -> {"intent":"pdf_gen","model":"{{MODEL:pdf_gen}}","tools":["pdf_gen"],"confidence":0.95}
User: Find the invoice PDF in my downloads -> {"intent":"file_ops","model":"{{MODEL:file_ops}}","tools":["file_ops"],"confidence":0.95}
User: Open package.json and show dependencies -> {"intent":"file_ops","model":"{{MODEL:file_ops}}","tools":["file_ops"],"confidence":0.95}
User: Search the project for enabled_tools usages -> {"intent":"file_ops","model":"{{MODEL:file_ops}}","tools":["file_ops"],"confidence":0.95}
User: Search my codebase for TODO comments -> {"intent":"file_ops","model":"{{MODEL:file_ops}}","tools":["file_ops"],"confidence":0.95}
User: Copy settings.example.json to settings.json locally -> {"intent":"file_ops","model":"{{MODEL:file_ops}}","tools":["file_ops"],"confidence":0.95}
User: Open docker-compose.yml and show the Ollama service block -> {"intent":"file_ops","model":"{{MODEL:file_ops}}","tools":["file_ops"],"confidence":0.95}
User: Open README.md and summarize what it means -> {"intent":"general_chat","model":"{{MODEL:general_chat}}","tools":[],"confidence":0.95}
User: Search the codebase for the HybridRouter class definition -> {"intent":"file_ops","model":"{{MODEL:file_ops}}","tools":["file_ops"],"confidence":0.95}
User: Read the dependencies list in pyproject.toml -> {"intent":"file_ops","model":"{{MODEL:file_ops}}","tools":["file_ops"],"confidence":0.95}
User: Move log files older than 30 days out of logs/ -> {"intent":"file_ops","model":"{{MODEL:file_ops}}","tools":["file_ops"],"confidence":0.95}
User: Classify the document type from this image -> {"intent":"vision","model":"{{MODEL:vision}}","tools":["vision"],"confidence":0.95}
User: Extract table cells visible in this spreadsheet screenshot -> {"intent":"vision","model":"{{MODEL:vision}}","tools":["vision"],"confidence":0.95}
User: Describe the differences between these two screenshots -> {"intent":"vision","model":"{{MODEL:vision}}","tools":["vision"],"confidence":0.95}
User: Create a JSON Schema for a product catalog entry -> {"intent":"coding_basic","model":"{{MODEL:coding_basic}}","tools":[],"confidence":0.95}
User: Implement OAuth login scaffolding for a FastAPI app -> {"intent":"coding_basic","model":"{{MODEL:coding_basic}}","tools":[],"confidence":0.95}
User: Write a SQL migration to add a users.email_verified column -> {"intent":"coding_basic","model":"{{MODEL:coding_basic}}","tools":[],"confidence":0.95}
User: Write an async retry helper with exponential backoff -> {"intent":"coding_basic","model":"{{MODEL:coding_basic}}","tools":[],"confidence":0.95}
User: Fix this bug in my Python code: list index out of range -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: Fix the race condition in our session cache -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: Optimize this slow SQL query that joins four large tables -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: Add unit tests for this UserService class -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: Add regression tests for the router keyword fallthrough case -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: Refactor this tangled React useEffect into smaller hooks -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: Why is this pytest failing with AssertionError on streaming chunks? -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: Debug CI: flake8 fails only on Ubuntu runners for this file -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: Refactor error handling so adapters don't leak LiteLLM exceptions -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: My Docker build fails at the pip install step, help diagnose -> {"intent":"coding_advanced","model":"{{MODEL:coding_advanced}}","tools":[],"confidence":0.95}
User: Make a PDF checklist for the release process -> {"intent":"pdf_gen","model":"{{MODEL:pdf_gen}}","tools":["pdf_gen"],"confidence":0.95}
User: Run git pull origin main -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Run python main.py with the current venv -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Execute pytest -q in the repo root -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Execute npm ci && npm test in web/ -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Execute poetry install and then poetry run pytest -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Execute pip install -e . on my machine -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Run git checkout -b feat/tmp only if clean -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Run pnpm install then pnpm lint -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Execute make migrate if the Makefile target exists -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Execute ls -la and show the largest files in ./logs -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Execute rg -n ClassifierOutput app/ and print matches -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Execute curl -s http://localhost:8000/health -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}
User: Start the docker compose stack for this repo -> {"intent":"bash","model":"{{MODEL:bash}}","tools":["bash"],"confidence":0.95}

TOOLS map (exact; valid tools only: web_search, bash, pdf_gen, file_ops, vision):
coding_basic [] | coding_advanced [] | general_chat [] | web_search ["web_search"] | deep_research ["web_search"] | bash ["bash"] | pdf_gen ["pdf_gen"] | file_ops ["file_ops"] | vision ["vision"] | reasoning_medium ["web_search","bash","pdf_gen","file_ops"] | reasoning_heavy ["web_search","bash","pdf_gen","file_ops"]
CRITICAL: coding_advanced / coding_basic / general_chat → tools=[] ALWAYS (even CI/Docker/pytest diagnose). bash → ["bash"] only — Run/Execute + CLI never coding_* never file_ops. deep_research → ["web_search"] only (papers/sources — not scenario analysis). reasoning_medium AND reasoning_heavy → ALWAYS ["web_search","bash","pdf_gen","file_ops"].

INTENT CRITICAL: current/live/local fact → web_search. File locate/read/move/search-in-tree → file_ops. Fix/debug/refactor/optimize/CI/pytest-fail/unit-tests-for-existing → coding_advanced with tools=[]. New scaffolding → coding_basic with tools=[]. Capacity/SLA/decommission/second-order/auction/incentives → reasoning_heavy. Screenshot OCR → vision. Backlog advice → general_chat.

Classify:
"""

DEFAULT_ASSISTANT_PROMPT = """You are Prompter X, a personal AI assistant for the project "{project_name}".

## How to answer
- Ground answers in project instructions and retrieved document excerpts when they are relevant to the question.
- Reference source filenames when you use retrieved context.
- Be direct, accurate, and proportional to what was asked.

## When information is missing
- When tools are available for this turn, call them to fetch what you need before answering.
- Use retrieved project context for project-specific facts; use tools for live, external, or filesystem information.
- When neither context nor tools can help, state what is missing and what you can still offer.

## Response style
- Prefer clear prose. Use fenced code blocks with language tags when writing code.
- State assumptions explicitly when you must make them.
- Report tool results faithfully; do not invent citations, data, or command output.
"""

DEFAULT_OLLAMA_MODEL_NAMES: dict[str, str] = {
    "local/qwen3-8b": "qwen3:8b-32k",
    "local/qwen2.5-coder-7b": "qwen2.5-coder:7b-32k",
    "local/qwen3-coder-30b": "qwen3-coder:30b-16k",
    "local/deepseek-r1-32b": "deepseek-r1:32b-16k",
    "local/gemma4-12b": "gemma4:12b-32k",
    "local/deepseek-r1-8b": "deepseek-r1:8b-32k",
    "local/coding-light": "",
    "local/coding-medium": "",
    "local/coding-heavy": "",
    "local/reasoning-medium": "",
    "local/reasoning-heavy": "",
    "local/vision-light": "",
    "local/vision-medium": "",
    "local/vision-heavy": "",
    "local/tool-calling-medium": "",
}

INTENT_FIELDS = (
    "general_chat",
    "web_search",
    "deep_research",
    "coding_basic",
    "coding_advanced",
    "bash",
    "pdf_gen",
    "file_ops",
    "vision",
    "reasoning_medium",
    "reasoning_heavy",
)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = base.copy()
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ModelsConfig(BaseModel):
    general_chat: str = "local/qwen3-8b"
    web_search: str = "local/tool-calling-medium"
    deep_research: str = "local/reasoning-heavy"
    coding_basic: str = "local/coding-light"
    coding_advanced: str = "local/coding-heavy"
    bash: str = "local/tool-calling-medium"
    pdf_gen: str = "local/tool-calling-medium"
    file_ops: str = "local/tool-calling-medium"
    vision: str = "local/vision-medium"
    reasoning_medium: str = "local/reasoning-medium"
    reasoning_heavy: str = "local/reasoning-heavy"

    def items(self) -> list[tuple[str, str]]:
        return [(name, getattr(self, name)) for name in INTENT_FIELDS]

    def values(self) -> list[str]:
        return [getattr(self, name) for name in INTENT_FIELDS]

    def get(self, intent: str, default: str | None = None) -> str | None:
        return getattr(self, intent, default)


_MODEL_PLACEHOLDER_RE = re.compile(r"\{\{MODEL:([a-z_]+)\}\}")


def render_classifier_prompt(template: str, models: "ModelsConfig") -> str:
    """Substitute {{MODEL:<intent>}} placeholders with current settings.json values."""

    def _replace(match: re.Match[str]) -> str:
        intent = match.group(1)
        return models.get(intent) or ""

    return _MODEL_PLACEHOLDER_RE.sub(_replace, template)


class OllamaSettings(BaseModel):
    base_url: str = "http://192.168.0.240:11434"
    # Seconds of idle time before Ollama unloads the model (Ollama-side purge).
    # 300 = 5m; -1 = forever. Protects VRAM if the app exits uncleanly.
    keep_alive: int = 300
    scheduler_enabled: bool = True


class VisionSettings(BaseModel):
    local_model: str = "ollama/gemma4:12b-32k"
    remote_model: str = ""


class RoutingRule(BaseModel):
    patterns: list[str]
    intent: str
    tools: list[str] = Field(default_factory=list)


_REASONING_TOOLS = ["web_search", "bash", "pdf_gen", "file_ops"]

DEFAULT_ROUTING_RULES: list[RoutingRule] = [
    RoutingRule(
        patterns=[
            "what's the weather",
            "weather forecast",
            "weather today",
            "weather in",
        ],
        intent="web_search",
        tools=["web_search"],
    ),
    RoutingRule(
        patterns=["make a pdf", "create a pdf", "generate a pdf", "export to pdf"],
        intent="pdf_gen",
        tools=["pdf_gen"],
    ),
    RoutingRule(
        patterns=["deep research", "research report", "research and summarize"],
        intent="deep_research",
        tools=["web_search"],
    ),
    RoutingRule(
        patterns=[
            "step by step",
            "think through",
            "solve this puzzle",
            "plan how to",
        ],
        intent="reasoning_medium",
        tools=list(_REASONING_TOOLS),
    ),
    RoutingRule(
        patterns=[
            "deep reasoning",
            "root cause analysis",
            "complex multi-step plan",
        ],
        intent="reasoning_heavy",
        tools=list(_REASONING_TOOLS),
    ),
    RoutingRule(
        patterns=[
            "run this command",
            "execute this",
            "run in terminal",
            "shell command",
            "run this script",
        ],
        intent="bash",
        tools=["bash"],
    ),
    RoutingRule(
        patterns=["list files", "find the file", "browse files", "find in my files"],
        intent="file_ops",
        tools=["file_ops"],
    ),
    RoutingRule(
        patterns=[
            "debug this",
            "fix this error",
            "fix this bug",
            "refactor this",
            "add a feature to",
        ],
        intent="coding_advanced",
        tools=[],
    ),
    RoutingRule(
        patterns=[
            "write a script",
            "make a function",
            "parse this csv",
            "analyze this data",
        ],
        intent="coding_basic",
        tools=[],
    ),
]


def _default_routing_rules() -> list[RoutingRule]:
    return [rule.model_copy(deep=True) for rule in DEFAULT_ROUTING_RULES]


class RouterSettings(BaseModel):
    classifier: str = "ollama/qwen2.5:3b"
    classifier_prompt: str = DEFAULT_CLASSIFIER_PROMPT
    rules_enabled: bool = True
    rules: list[RoutingRule] = Field(default_factory=_default_routing_rules)


class AssistantSettings(BaseModel):
    system_prompt: str = DEFAULT_ASSISTANT_PROMPT


class EmbeddingSettings(BaseModel):
    model: str = "ollama/nomic-embed-text"
    max_tokens: int = 1500


class SearchProviders(BaseModel):
    web_search: str = "duckduckgo"
    deep_research: str = "tavily"


class SearchSettings(BaseModel):
    providers: SearchProviders = Field(default_factory=SearchProviders)
    tavily_api_key: str = ""


class OpenCodeGoSettings(BaseModel):
    base_url: str = "https://opencode.ai"
    api_key: str = ""
    enabled: bool = True


class QdrantSettings(BaseModel):
    url: str = "http://localhost:6333"


class RagSettings(BaseModel):
    chunk_size: int = 512
    chunk_overlap_ratio: float = 0.2
    top_k: int = 5


class HealthSettings(BaseModel):
    classifier_timeout_s: float = 30.0
    ollama_fallback_to_remote: bool = True


class LoggingSubsystems(BaseModel):
    router: bool = True
    scheduler: bool = True
    embedding: bool = True
    search: bool = True
    rag: bool = True
    orchestrator: bool = True
    llm: bool = True
    mcp: bool = True


class LoggingSettings(BaseModel):
    level: str = "INFO"
    file_enabled: bool = True
    subsystems: LoggingSubsystems = Field(default_factory=LoggingSubsystems)


class DebugSettings(BaseModel):
    router_decisions: bool = False
    sse_trace: bool = False


class Settings(BaseSettings):
    """Runtime settings loaded from settings.json, environment, and .env secrets."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    litellm_config_path: str = "litellm_config.yaml"
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    ollama_model_names: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_OLLAMA_MODEL_NAMES)
    )
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    router: RouterSettings = Field(default_factory=RouterSettings)
    assistant: AssistantSettings = Field(default_factory=AssistantSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    opencode_go: OpenCodeGoSettings = Field(default_factory=OpenCodeGoSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    rag: RagSettings = Field(default_factory=RagSettings)
    health: HealthSettings = Field(default_factory=HealthSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    debug: DebugSettings = Field(default_factory=DebugSettings)

    opencode_api_key: str = Field(default="", validation_alias="OPENCODE_API_KEY")
    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")
    qdrant_url: str = Field(default="", validation_alias="QDRANT_URL")

    projects_dir: Path = Field(
        default=Path("./projects"),
        validation_alias="PROJECTS_DIR",
    )
    data_dir: Path = Field(
        default=Path("./data"),
        validation_alias="DATA_DIR",
    )

    # Legacy flat RAG/debug fields used by existing modules and settings_store.
    debug_prompts: bool = Field(
        default=False,
        validation_alias="DEBUG_PROMPTS",
    )
    rag_top_k: int = Field(default=5, validation_alias="RAG_TOP_K")
    chunk_size: int = Field(default=800, validation_alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=120, validation_alias="CHUNK_OVERLAP")

    @model_validator(mode="before")
    @classmethod
    def merge_settings_json(cls, data: Any) -> Any:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return data

        json_path = Path(os.environ.get("SETTINGS_JSON_PATH", "settings.json"))
        if json_path.exists():
            try:
                file_data = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(file_data, dict):
                    data = _deep_merge(file_data, data)
            except (json.JSONDecodeError, OSError):
                pass
        # Existing settings.json may predate new catalog keys; nested file
        # dicts would otherwise omit placeholders like local/coding-light.
        names = data.get("ollama_model_names")
        if not isinstance(names, dict):
            names = {}
            data["ollama_model_names"] = names
        for alias, tag in DEFAULT_OLLAMA_MODEL_NAMES.items():
            names.setdefault(alias, tag)
        return data

    @model_validator(mode="after")
    def sync_secrets_and_legacy_fields(self) -> Self:
        if self.opencode_api_key:
            self.opencode_go.api_key = self.opencode_api_key
        if self.tavily_api_key:
            self.search.tavily_api_key = self.tavily_api_key
        if self.qdrant_url.strip():
            self.qdrant.url = self.qdrant_url.strip()
        return self

    @property
    def effective_projects_dir(self) -> Path:
        """Prefer ``PROJECTS_DIR``; fall back to legacy ``DATA_DIR/projects`` if needed."""
        projects = self.projects_dir
        if projects.exists() and any(projects.iterdir()):
            return projects
        legacy = self.data_dir / "projects"
        if legacy.exists() and any(legacy.iterdir()):
            return legacy
        return projects

    def alias_to_ollama_name(self, alias: str) -> str:
        """Map a LiteLLM local alias to its Ollama model name."""
        return self.ollama_model_names[alias]

    def auth_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
