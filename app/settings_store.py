"""Read/write ``settings.json`` with Pydantic validation.

Secrets (``OPENCODE_API_KEY``, ``TAVILY_API_KEY``, etc.) live in ``.env`` only.
API responses strip secret fields and expose ``*_set`` boolean flags instead.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import (
    DEFAULT_OLLAMA_MODEL_NAMES,
    DEFAULT_ROUTING_RULES,
    RoutingRule,
    Settings,
    get_settings,
)
from app.env_secrets import secret_is_set, write_env_vars
from app.litellm_sync import sync_litellm_config

logger = logging.getLogger(__name__)

_SETTINGS_ENV = "SETTINGS_JSON_PATH"
_DEFAULT_FILE = Path("settings.json")

_SECRET_PATHS: tuple[tuple[str, ...], ...] = (
    ("search", "tavily_api_key"),
    ("opencode_go", "api_key"),
)

_ENV_SECRET_FLAGS: dict[tuple[str, ...], str] = {
    ("search", "tavily_api_key"): "TAVILY_API_KEY",
    ("opencode_go", "api_key"): "OPENCODE_API_KEY",
}


class SettingsValidationError(ValueError):
    """Raised when settings fail Pydantic validation."""


def _settings_path() -> Path:
    return Path(os.environ.get(_SETTINGS_ENV, _DEFAULT_FILE))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = base.copy()
    for key, value in override.items():
        if value is None:
            merged.pop(key, None)
            continue
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


_PERSISTED_TOP_LEVEL_KEYS = (
    "models",
    "ollama_model_names",
    "assistant",
    "vision",
    "router",
    "embedding",
    "search",
    "ollama",
    "opencode_go",
    "qdrant",
    "rag",
    "health",
    "logging",
    "debug",
)


def _ensure_ollama_catalog_aliases(data: dict[str, Any]) -> dict[str, Any]:
    """Guarantee DEFAULT_OLLAMA_MODEL_NAMES keys exist (e.g. new tier aliases).

    ``_deep_merge`` already merges nested dict keys, but ``Settings()`` /
    ``_default_settings_dict`` can be contaminated by an on-disk settings.json
    that predates catalog additions. Always fill missing aliases in-place.
    """
    names = data.get("ollama_model_names")
    if not isinstance(names, dict):
        names = {}
        data["ollama_model_names"] = names
    for alias, tag in DEFAULT_OLLAMA_MODEL_NAMES.items():
        names.setdefault(alias, tag)
    return data


def _default_settings_dict() -> dict[str, Any]:
    """Build the full Phase 1 default settings document."""
    settings = Settings()
    dumped = {
        key: settings.model_dump(mode="json")[key]
        for key in _PERSISTED_TOP_LEVEL_KEYS
    }
    return _ensure_ollama_catalog_aliases(dumped)


def _only_persisted_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {key: data[key] for key in _PERSISTED_TOP_LEVEL_KEYS if key in data}


def _strip_secrets_for_disk(data: dict[str, Any]) -> dict[str, Any]:
    """Ensure secret fields are never persisted in settings.json."""
    stripped = _deep_merge(data, {})
    search = stripped.setdefault("search", {})
    if isinstance(search, dict):
        search["tavily_api_key"] = ""
    opencode = stripped.setdefault("opencode_go", {})
    if isinstance(opencode, dict):
        opencode["api_key"] = ""
    return stripped


def validate_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Validate *data* and return a normalized settings dict ready for disk."""
    try:
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first.get("loc", ()))
        msg = first.get("msg", "invalid value")
        detail = f"{loc}: {msg}" if loc else msg
        raise SettingsValidationError(detail) from exc

    normalized = _only_persisted_fields(settings.model_dump(mode="json"))
    return _strip_secrets_for_disk(normalized)


def _secret_is_set(path: tuple[str, ...]) -> bool:
    env_key = _ENV_SECRET_FLAGS.get(path)
    if env_key and secret_is_set(env_key):
        return True
    return False


def _extract_secret_updates(updates: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Pull secret values from a settings update; return cleaned update + env writes."""
    cleaned = _deep_merge({}, updates)
    env_writes: dict[str, str] = {}

    search = cleaned.get("search")
    if isinstance(search, dict) and "tavily_api_key" in search:
        raw = search.pop("tavily_api_key")
        if isinstance(raw, str) and raw.strip():
            env_writes["TAVILY_API_KEY"] = raw.strip()

    opencode = cleaned.get("opencode_go")
    if isinstance(opencode, dict) and "api_key" in opencode:
        raw = opencode.pop("api_key")
        if isinstance(raw, str) and raw.strip():
            env_writes["OPENCODE_API_KEY"] = raw.strip()

    if isinstance(search, dict) and not search:
        cleaned.pop("search", None)
    if isinstance(opencode, dict) and not opencode:
        cleaned.pop("opencode_go", None)

    return cleaned, env_writes


def _refresh_settings_cache() -> None:
    clear = getattr(get_settings, "cache_clear", None)
    if clear is not None:
        clear()


def to_public_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Return settings safe for API responses (secrets stripped, flags added)."""
    public = _deep_merge(data, {})
    search = public.setdefault("search", {})
    if isinstance(search, dict):
        search["tavily_api_key"] = ""
        search["tavily_api_key_set"] = _secret_is_set(("search", "tavily_api_key"))
    opencode = public.setdefault("opencode_go", {})
    if isinstance(opencode, dict):
        opencode["api_key"] = ""
        opencode["api_key_set"] = _secret_is_set(("opencode_go", "api_key"))
    return public


def load_settings() -> dict[str, Any]:
    """Load settings from disk, falling back to defaults when missing."""
    path = _settings_path()
    if not path.exists():
        defaults = validate_settings(_default_settings_dict())
        write_settings(defaults)
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SettingsValidationError(f"Could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SettingsValidationError("settings.json must contain a JSON object")
    merged = _deep_merge(_default_settings_dict(), raw)
    return validate_settings(_ensure_ollama_catalog_aliases(merged))


def read_settings() -> dict[str, Any]:
    """Return public settings for GET /settings."""
    return to_public_settings(load_settings())


def write_settings(data: dict[str, Any]) -> None:
    """Validate and persist settings to disk."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = validate_settings(data)
    path.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _refresh_settings_cache()
    try:
        sync_litellm_config(
            normalized.get("ollama_model_names", {}),
            normalized.get("ollama", {}).get("base_url", ""),
            Settings.model_fields["litellm_config_path"].default,
            keep_alive=normalized.get("ollama", {}).get("keep_alive"),
        )
    except Exception:
        logger.exception("Failed to sync litellm_config.yaml after settings write")


def update_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge partial *updates*, validate, persist, and return public settings."""
    if not updates:
        return read_settings()
    cleaned, env_writes = _extract_secret_updates(updates)
    if env_writes:
        write_env_vars(env_writes)
        _refresh_settings_cache()
    if not cleaned:
        return read_settings()
    current = load_settings()
    merged = _deep_merge(current, cleaned)
    write_settings(merged)
    return read_settings()


def ensure_settings_file() -> None:
    """Create settings.json with defaults when it does not exist."""
    path = _settings_path()
    if path.exists():
        return
    write_settings(_default_settings_dict())


def init_settings_store() -> None:
    """Call at app startup to ensure settings.json exists and refresh cache."""
    ensure_settings_file()
    _refresh_settings_cache()


# Backward-compatible aliases used by legacy routes/tests.
def load_local_settings() -> dict[str, Any]:
    return load_settings()


def save_local_settings(updates: dict[str, Any]) -> None:
    update_settings(updates)


def default_settings_for_export() -> dict[str, Any]:
    """Expose defaults for generating the repository settings.json file."""
    return validate_settings(_default_settings_dict())


__all__ = [
    "DEFAULT_ROUTING_RULES",
    "RoutingRule",
    "SettingsValidationError",
    "default_settings_for_export",
    "ensure_settings_file",
    "init_settings_store",
    "load_local_settings",
    "load_settings",
    "read_settings",
    "save_local_settings",
    "to_public_settings",
    "update_settings",
    "validate_settings",
    "write_settings",
]
