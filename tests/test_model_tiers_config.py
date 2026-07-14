"""Tests for placeholder tier aliases in the Ollama model catalog."""

from __future__ import annotations

from app.config import DEFAULT_OLLAMA_MODEL_NAMES, ModelsConfig

_TIER_ALIASES = (
    "local/coding-light",
    "local/coding-medium",
    "local/coding-heavy",
    "local/reasoning-medium",
    "local/reasoning-heavy",
    "local/vision-light",
    "local/vision-medium",
    "local/vision-heavy",
    "local/tool-calling-medium",
)


def test_tier_aliases_present_with_empty_placeholder_values() -> None:
    for alias in _TIER_ALIASES:
        assert alias in DEFAULT_OLLAMA_MODEL_NAMES
        assert DEFAULT_OLLAMA_MODEL_NAMES[alias] == ""


def test_tier_aliases_are_not_models_config_fields() -> None:
    field_names = set(ModelsConfig.model_fields.keys())
    for alias in _TIER_ALIASES:
        assert alias not in field_names
