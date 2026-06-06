"""Resolve application model aliases to LiteLLM completion kwargs."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import Settings

_ENV_PREFIX = "os.environ/"
_SETTINGS_SECRET_FALLBACKS: dict[str, tuple[str, ...]] = {
    "OPENCODE_API_KEY": ("opencode_api_key", "opencode_go.api_key"),
    "TAVILY_API_KEY": ("tavily_api_key", "search.tavily_api_key"),
}


class LitellmAliasError(LookupError):
    """Raised when an alias is missing from litellm_config.yaml."""


def _resolve_env_reference(value: str, settings: Settings) -> str:
    env_key = value[len(_ENV_PREFIX) :]
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return env_value

    for attr_path in _SETTINGS_SECRET_FALLBACKS.get(env_key, ()):
        current: Any = settings
        for part in attr_path.split("."):
            current = getattr(current, part, "")
        if isinstance(current, str) and current.strip():
            return current.strip()
    return ""


def _resolve_config_values(value: Any, settings: Settings) -> Any:
    if isinstance(value, str) and value.startswith(_ENV_PREFIX):
        return _resolve_env_reference(value, settings)
    if isinstance(value, dict):
        return {
            key: _resolve_config_values(nested, settings)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_resolve_config_values(item, settings) for item in value]
    return value


@lru_cache
def _load_alias_params(config_path: str) -> dict[str, dict[str, Any]]:
    path = Path(config_path)
    if not path.exists():
        return {}

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    aliases: dict[str, dict[str, Any]] = {}
    for entry in data.get("model_list", []):
        if not isinstance(entry, dict):
            continue
        alias = entry.get("model_name")
        params = entry.get("litellm_params")
        if isinstance(alias, str) and isinstance(params, dict):
            aliases[alias] = params
    return aliases


def resolve_litellm_params(settings: Settings, alias: str) -> dict[str, Any]:
    """Return kwargs for ``litellm.acompletion`` from an application alias."""
    alias_params = _load_alias_params(settings.litellm_config_path).get(alias)
    if alias_params is None:
        raise LitellmAliasError(
            f"No litellm_config.yaml entry for alias {alias!r}"
        )

    resolved = _resolve_config_values(alias_params, settings)
    if not isinstance(resolved, dict):
        raise LitellmAliasError(
            f"Invalid litellm_params for alias {alias!r}"
        )
    if "model" not in resolved:
        raise LitellmAliasError(
            f"litellm_params for alias {alias!r} is missing required 'model'"
        )
    return dict(resolved)


def reset_litellm_resolver_cache() -> None:
    """Clear cached litellm config (for tests)."""
    _load_alias_params.cache_clear()
