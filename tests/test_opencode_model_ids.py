"""Guardrails: app aliases vs OpenCode Go provider model IDs in litellm_config.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.config import Settings, get_settings
from app.litellm_resolver import reset_litellm_resolver_cache, resolve_litellm_params

# OpenCode Go chat/completions model IDs (https://opencode.ai/docs/go/)
OPENCODE_CHAT_COMPLETIONS_IDS = frozenset(
    {
        "glm-5.1",
        "glm-5",
        "kimi-k2.5",
        "kimi-k2.6",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "mimo-v2.5",
        "mimo-v2.5-pro",
    }
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LITELLM_CONFIG = REPO_ROOT / "litellm_config.yaml"
SETTINGS_JSON = REPO_ROOT / "settings.json"


def _load_litellm_entries() -> list[dict]:
    with LITELLM_CONFIG.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return [e for e in data.get("model_list", []) if isinstance(e, dict)]


def test_repo_remote_models_use_valid_opencode_ids() -> None:
    """Every openai/* remote entry must use a documented OpenCode chat/completions ID."""
    for entry in _load_litellm_entries():
        params = entry.get("litellm_params") or {}
        model = params.get("model", "")
        if not isinstance(model, str) or not model.startswith("openai/"):
            continue
        provider_id = model.removeprefix("openai/")
        assert provider_id in OPENCODE_CHAT_COMPLETIONS_IDS, (
            f"Unknown OpenCode model ID {provider_id!r} for alias {entry.get('model_name')!r}"
        )


def test_repo_kimi_alias_resolves_to_kimi_k2_6() -> None:
    reset_litellm_resolver_cache()
    settings = Settings(
        projects_dir=REPO_ROOT / "projects",
        data_dir=REPO_ROOT / "data",
        litellm_config_path=str(LITELLM_CONFIG),
    )
    params = resolve_litellm_params(settings, "remote/kimi-k2-6")
    assert params["model"] == "openai/kimi-k2.6"


def test_settings_model_aliases_exist_in_litellm_config() -> None:
    """Every alias in settings.models must have a litellm_config.yaml model_name entry."""
    reset_litellm_resolver_cache()
    settings = get_settings()
    configured = {e["model_name"] for e in _load_litellm_entries() if "model_name" in e}
    for intent, alias in settings.models.items():
        assert alias in configured, (
            f"settings.models.{intent} = {alias!r} missing from litellm_config.yaml"
        )
