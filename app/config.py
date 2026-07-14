"""Application configuration from settings.json, environment, and .env secrets."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CLASSIFIER_PROMPT = """You are an intent classifier. Output ONLY valid JSON: {"intent":"<intent>","model":"<model>","tools":[...]}.

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
    web_search: str = "local/qwen3-8b"
    deep_research: str = "local/deepseek-r1-32b"
    coding_basic: str = "local/qwen2.5-coder-7b"
    coding_advanced: str = "local/qwen3-coder-30b"
    bash: str = "local/qwen3-8b"
    pdf_gen: str = "local/qwen3-8b"
    file_ops: str = "local/qwen3-8b"
    vision: str = "local/gemma4-12b"

    def items(self) -> list[tuple[str, str]]:
        return [(name, getattr(self, name)) for name in INTENT_FIELDS]

    def values(self) -> list[str]:
        return [getattr(self, name) for name in INTENT_FIELDS]

    def get(self, intent: str, default: str | None = None) -> str | None:
        return getattr(self, intent, default)


class OllamaSettings(BaseModel):
    base_url: str = "http://192.168.0.240:11434"
    keep_alive: int = -1
    scheduler_enabled: bool = True


class VisionSettings(BaseModel):
    local_model: str = "ollama/gemma4:12b-32k"
    remote_model: str = ""


class RoutingRule(BaseModel):
    patterns: list[str]
    intent: str
    tools: list[str] = Field(default_factory=list)


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
    classifier: str = "ollama/qwen2.5:1.5b-32k"
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
