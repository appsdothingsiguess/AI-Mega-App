"""Pydantic models for API requests and responses."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import (
    AssistantSettings,
    DebugSettings,
    EmbeddingSettings,
    HealthSettings,
    LoggingSettings,
    ModelsConfig,
    OllamaSettings,
    QdrantSettings,
    RagSettings,
    RouterSettings,
    VisionSettings,
)


class ProjectCreate(BaseModel):
    name: str
    system_prompt: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class ProjectSummary(BaseModel):
    id: str
    name: str
    created_at: datetime
    file_count: int
    thread_count: int


class ProjectDetail(ProjectSummary):
    system_prompt: str
    config: dict[str, Any] = Field(default_factory=dict)
    docs_path: str
    instructions_path: str


class ThreadCreate(BaseModel):
    title: str | None = None


class ThreadRename(BaseModel):
    title: str


class ThreadSummary(BaseModel):
    id: str
    title: str | None = None
    created_at: datetime
    message_count: int


class MessageCreate(BaseModel):
    content: str


class ChatStreamRequest(MessageCreate):
    model_override: str | None = None
    enabled_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_enabled_tools(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw_enabled = data.get("enabled_tools")
        if isinstance(raw_enabled, dict):
            data = dict(data)
            data["enabled_tools"] = [k for k, v in raw_enabled.items() if v]
        return data


class MessageRecord(BaseModel):
    role: str
    content: str
    created_at: datetime
    model: str | None = None


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
    retrieved_chunks: list[dict[str, Any]] = Field(default_factory=list)


class OllamaModelsResponse(BaseModel):
    reachable: bool
    models: list[str] = Field(default_factory=list)


class ToolsListResponse(BaseModel):
    tools: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    ok: bool
    mode: str
    base_url: str
    model: str
    model_loaded: bool
    message: str
    available_models: list[str] = Field(default_factory=list)


# --- Sources ---

class DocFileInfo(BaseModel):
    name: str
    size: int
    mtime: float
    enabled: bool
    ingested: bool


class SourcesState(BaseModel):
    files: list[DocFileInfo] = Field(default_factory=list)
    default_new_enabled: bool = True


class SourcesUpdate(BaseModel):
    enabled: list[str]
    default_new_enabled: bool = True


# --- Instructions ---

class InstructionsResponse(BaseModel):
    content: str


class InstructionsUpdate(BaseModel):
    content: str


# --- Sync ---

class SyncResponse(BaseModel):
    chunk_count: int


# --- Settings (Phase 1 nested schema) ---

class SearchProviders(BaseModel):
    web_search: str = "duckduckgo"
    deep_research: str = "tavily"


class SearchSettingsPublic(BaseModel):
    providers: SearchProviders = Field(default_factory=SearchProviders)
    tavily_api_key: str = ""
    tavily_api_key_set: bool = False


class OpenCodeGoSettingsPublic(BaseModel):
    base_url: str = "https://opencode.ai"
    api_key: str = ""
    api_key_set: bool = False
    enabled: bool = True


class SettingsSnapshot(BaseModel):
    models: ModelsConfig
    ollama_model_names: dict[str, str]
    assistant: AssistantSettings = Field(default_factory=AssistantSettings)
    vision: VisionSettings
    router: RouterSettings
    embedding: EmbeddingSettings
    search: SearchSettingsPublic
    ollama: OllamaSettings
    opencode_go: OpenCodeGoSettingsPublic
    qdrant: QdrantSettings
    rag: RagSettings
    health: HealthSettings
    logging: LoggingSettings
    debug: DebugSettings


class SettingsUpdate(BaseModel):
    """Partial nested update payload for PUT /settings."""

    model_config = ConfigDict(extra="forbid")

    models: dict[str, str] | None = None
    ollama_model_names: dict[str, str] | None = None
    assistant: dict[str, Any] | None = None
    vision: dict[str, Any] | None = None
    router: dict[str, Any] | None = None
    embedding: dict[str, Any] | None = None
    search: dict[str, Any] | None = None
    ollama: dict[str, Any] | None = None
    opencode_go: dict[str, Any] | None = None
    qdrant: dict[str, Any] | None = None
    rag: dict[str, Any] | None = None
    health: dict[str, Any] | None = None
    logging: dict[str, Any] | None = None
    debug: dict[str, Any] | None = None


# --- LM Studio models & server ---

class LmModelInfo(BaseModel):
    key: str
    display_name: str
    type: str
    loaded: bool
    vision: bool
    params_string: str | None = None


class LmModelsResponse(BaseModel):
    models: list[LmModelInfo] = Field(default_factory=list)
    selected_model: str
    mode: str


class LmModelLoadRequest(BaseModel):
    model: str
    context_length: int | None = Field(default=None, ge=512, le=1_000_000)


class LmModelLoadResponse(BaseModel):
    ok: bool
    model: str
    status: str
    instance_id: str | None = None
    load_time_seconds: float | None = None
    message: str = ""


class LmServerStatus(BaseModel):
    config_found: bool
    config_path: str | None = None
    port: int = 1234
    network_interface: str = "127.0.0.1"
    serve_on_local_network: bool = False
    access_urls: list[str] = Field(default_factory=list)
    restart_required_note: str | None = None


class LmServerUpdate(BaseModel):
    serve_on_local_network: bool
