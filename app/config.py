"""Configuration system (docs/FEATURES.md A1). One flat module: pydantic
models for config.yaml, a loader that deep-merges an optional
settings.local.yaml overlay, and a cached accessor.

Model/provider names are config-only (rule 003) — this module is the single
place that reads them off disk; every other module receives them as
already-validated `Config` fields, never a hardcoded literal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
OVERLAY_PATH = REPO_ROOT / "settings.local.yaml"


class ConfigError(Exception):
    """Raised on any invalid config — always carries the offending key path
    so startup fails loudly and specifically, never a silent half-boot."""

    def __init__(self, key_path: str, reason: str) -> None:
        self.key_path = key_path
        self.reason = reason
        super().__init__(f"invalid config at '{key_path}': {reason}")


ModelClass = Literal[
    "general", "coding", "tool", "reasoning", "vision", "utility", "embed",
    "classifier", "dispatcher",
]
ToolCallMode = Literal["native", "weak", "none"]
GpuAssignment = Literal[0, 1, "cpu"]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class ServerConfig(_Strict):
    host: str = "0.0.0.0"
    port: int = 8000


class LlamaSwapConfig(_Strict):
    base_url: str
    timeout_s: float = 120


class DbConfig(_Strict):
    path: str = "data/app.db"


class ModelEntry(_Strict):
    name: str
    class_: ModelClass = Field(alias="class")
    ctx: int
    gpu: GpuAssignment
    tool_call: ToolCallMode
    thinking: bool = False
    reasoning_off: bool = False
    max_tokens: int
    enabled: bool = True

    model_config = {"extra": "forbid", "populate_by_name": True}


class DefaultsConfig(_Strict):
    chat_model: str
    utility_model: str
    title_model: str


class LlmConfig(_Strict):
    first_token_timeout_s: float = 30


class DebugConfig(_Strict):
    store_prompts: bool = True


class Config(_Strict):
    server: ServerConfig = ServerConfig()
    llama_swap: LlamaSwapConfig
    db: DbConfig = DbConfig()
    models: list[ModelEntry]
    defaults: DefaultsConfig
    llm: LlmConfig = LlmConfig()
    debug: DebugConfig = DebugConfig()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    return loaded or {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge `overlay` onto `base`, overlay values winning. Nested dicts merge
    recursively; any other type (including lists) is replaced wholesale."""
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _first_error_key_path(exc: ValidationError) -> tuple[str, str]:
    errors = exc.errors()
    first = errors[0]
    key_path = ".".join(str(part) for part in first["loc"]) or "<root>"
    return key_path, first["msg"]


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load config.yaml, deep-merge settings.local.yaml if present, and
    validate. Raises ConfigError(key_path, reason) with the exact offending
    key on any validation failure — never a half-booted app."""
    if not path.exists():
        raise ConfigError(str(path), "file not found")
    base = _read_yaml(path)
    overlay = _read_yaml(OVERLAY_PATH)
    merged = _deep_merge(base, overlay)
    try:
        return Config.model_validate(merged)
    except ValidationError as exc:
        key_path, reason = _first_error_key_path(exc)
        raise ConfigError(key_path, reason) from exc


_cached_config: Config | None = None


def get_config() -> Config:
    """Cached accessor over the default config.yaml + overlay. Use
    reset_config_cache() in tests (or after a Settings write) to force the
    next call to re-read from disk."""
    global _cached_config
    if _cached_config is None:
        _cached_config = load_config()
    return _cached_config


def reset_config_cache() -> None:
    """Test/hot-reload hook: clears the cached Config so the next
    get_config() call re-reads from disk."""
    global _cached_config
    _cached_config = None
