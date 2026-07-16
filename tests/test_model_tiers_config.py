"""Tests for tier aliases in the Ollama model catalog."""

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

_EXPECTED_TIER_TAGS = {
    "local/coding-light": "qwen2.5-coder:7b-16k",
    "local/coding-medium": "qwen3-coder:30b-16k",
    "local/coding-heavy": "qwen3-coder:30b-24k",
    "local/reasoning-medium": "deepseek-r1:8b-32k",
    "local/reasoning-heavy": "deepseek-r1:32b-16k",
    "local/vision-light": "gemma4:12b-16k",
    "local/vision-medium": "gemma4:26b-16k",
    "local/vision-heavy": "gemma4:31b-12k",
    "local/tool-calling-medium": "qwen3:8b-32k",
}


def test_tier_aliases_present_with_resolved_ollama_tags() -> None:
    for alias in _TIER_ALIASES:
        assert alias in DEFAULT_OLLAMA_MODEL_NAMES
        assert DEFAULT_OLLAMA_MODEL_NAMES[alias] == _EXPECTED_TIER_TAGS[alias]
        assert DEFAULT_OLLAMA_MODEL_NAMES[alias].strip() != ""


def test_tier_aliases_are_not_models_config_fields() -> None:
    field_names = set(ModelsConfig.model_fields.keys())
    for alias in _TIER_ALIASES:
        assert alias not in field_names


def test_models_config_reasoning_intent_defaults() -> None:
    models = ModelsConfig()
    assert models.reasoning_medium == "local/reasoning-medium"
    assert models.reasoning_heavy == "local/reasoning-heavy"
