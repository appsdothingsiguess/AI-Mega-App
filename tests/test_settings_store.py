"""Tests for app/settings_store.py Phase 1 settings.json persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.config import get_settings
from app.settings_store import (
    SettingsValidationError,
    init_settings_store,
    load_settings,
    read_settings,
    update_settings,
    validate_settings,
    write_settings,
)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Each test uses its own settings.json path and a clean settings cache."""
    settings_file = tmp_path / "settings.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SETTINGS_JSON_PATH", str(settings_file))
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("QDRANT_URL", raising=False)
    get_settings.cache_clear()
    yield settings_file
    get_settings.cache_clear()


def test_read_creates_defaults_when_missing(_isolated_settings: Path) -> None:
    assert not _isolated_settings.exists()
    data = read_settings()  # persists defaults on first read
    assert data["models"]["general_chat"] == "local/qwen3-8b"
    assert data["router"]["rules_enabled"] is True
    assert len(data["router"]["rules"]) == 7
    assert _isolated_settings.exists()


def test_load_settings_round_trip(_isolated_settings: Path) -> None:
    init_settings_store()
    loaded = load_settings()
    assert loaded["embedding"]["max_tokens"] == 1500
    assert loaded["rag"]["chunk_size"] == 512
    assert loaded["search"]["providers"]["web_search"] == "duckduckgo"


def test_write_and_read_settings(_isolated_settings: Path) -> None:
    init_settings_store()
    current = load_settings()
    current["logging"]["level"] = "DEBUG"
    write_settings(current)
    reloaded = load_settings()
    assert reloaded["logging"]["level"] == "DEBUG"


def test_partial_update_merges_nested(_isolated_settings: Path) -> None:
    init_settings_store()
    public = update_settings({"ollama": {"base_url": "http://ollama:11434"}})
    assert public["ollama"]["base_url"] == "http://ollama:11434"
    assert public["ollama"]["scheduler_enabled"] is True
    assert load_settings()["qdrant"]["url"] == "http://localhost:6333"


def test_partial_update_models_intent(_isolated_settings: Path) -> None:
    init_settings_store()
    public = update_settings({"models": {"bash": "local/deepseek-r1-8b"}})
    assert public["models"]["bash"] == "local/deepseek-r1-8b"
    assert public["models"]["pdf_gen"] == "local/qwen3-8b"


def test_validation_rejects_invalid_chunk_size(_isolated_settings: Path) -> None:
    init_settings_store()
    with pytest.raises(SettingsValidationError, match="rag"):
        update_settings({"rag": {"chunk_size": "not-a-number"}})


def test_validation_rejects_invalid_routing_rule(_isolated_settings: Path) -> None:
    init_settings_store()
    with pytest.raises(SettingsValidationError, match="router.rules"):
        update_settings(
            {
                "router": {
                    "rules": [{"patterns": "bad", "intent": "web_search", "tools": []}],
                }
            }
        )


def test_public_settings_strip_secrets(_isolated_settings: Path) -> None:
    init_settings_store()
    write_settings(
        {
            **load_settings(),
            "search": {"tavily_api_key": "should-not-persist"},
            "opencode_go": {"api_key": "also-secret"},
        }
    )
    on_disk = json.loads(_isolated_settings.read_text(encoding="utf-8"))
    assert on_disk["search"]["tavily_api_key"] == ""
    assert on_disk["opencode_go"]["api_key"] == ""

    public = read_settings()
    assert public["search"]["tavily_api_key"] == ""
    assert public["search"]["tavily_api_key_set"] is False
    assert public["opencode_go"]["api_key"] == ""
    assert public["opencode_go"]["api_key_set"] is False


def test_public_settings_secret_flags_from_env(
    _isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_settings_store()
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("OPENCODE_API_KEY", "oc-test")
    public = read_settings()
    assert public["search"]["tavily_api_key_set"] is True
    assert public["opencode_go"]["api_key_set"] is True
    assert public["search"]["tavily_api_key"] == ""


def test_update_settings_persists_secrets_to_env(
    _isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OPENCODE_API_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE_PATH", str(env_path))
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    init_settings_store()
    public = update_settings(
        {
            "search": {"tavily_api_key": "tvly-from-ui"},
            "opencode_go": {"api_key": "oc-from-ui"},
        }
    )
    assert public["search"]["tavily_api_key_set"] is True
    assert public["opencode_go"]["api_key_set"] is True
    assert public["search"]["tavily_api_key"] == ""

    on_disk = json.loads(_isolated_settings.read_text(encoding="utf-8"))
    assert on_disk["search"]["tavily_api_key"] == ""
    assert on_disk["opencode_go"]["api_key"] == ""

    env_text = env_path.read_text(encoding="utf-8")
    assert "TAVILY_API_KEY=tvly-from-ui" in env_text
    assert "OPENCODE_API_KEY=oc-from-ui" in env_text
    assert os.environ["TAVILY_API_KEY"] == "tvly-from-ui"
    assert os.environ["OPENCODE_API_KEY"] == "oc-from-ui"


def test_init_settings_store_clears_cache(_isolated_settings: Path) -> None:
    init_settings_store()
    update_settings({"debug": {"router_decisions": True}})
    get_settings()
    info_before = get_settings.cache_info()
    assert info_before.currsize >= 1
    init_settings_store()
    assert get_settings.cache_info().currsize == 0


def test_update_empty_returns_current(_isolated_settings: Path) -> None:
    init_settings_store()
    first = read_settings()
    second = update_settings({})
    assert second == first


def test_validate_settings_normalizes_router_rules(_isolated_settings: Path) -> None:
    init_settings_store()
    base = load_settings()
    base["router"]["rules"] = base["router"]["rules"][:1]
    normalized = validate_settings(base)
    assert len(normalized["router"]["rules"]) == 1
    assert normalized["router"]["rules"][0]["intent"] == "web_search"


def test_qdrant_url_env_overrides_settings_json(
    _isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import Settings

    _isolated_settings.write_text(
        json.dumps({"qdrant": {"url": "http://localhost:6333"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("QDRANT_URL", "http://192.168.0.240:6333")
    get_settings.cache_clear()
    settings = Settings()
    assert settings.qdrant.url == "http://192.168.0.240:6333"


def test_load_settings_merges_new_tier_aliases_into_old_catalog(
    _isolated_settings: Path,
) -> None:
    """settings.json written before tiers existed must still expose placeholders."""
    from app.config import DEFAULT_OLLAMA_MODEL_NAMES

    old_catalog = {
        "local/qwen3-8b": "qwen3:8b-32k",
        "local/qwen2.5-coder-7b": "qwen2.5-coder:7b-32k",
        "local/qwen3-coder-30b": "qwen3-coder:30b-16k",
        "local/deepseek-r1-32b": "deepseek-r1:32b-16k",
        "local/gemma4-12b": "gemma4:12b-32k",
        "local/deepseek-r1-8b": "deepseek-r1:8b-32k",
    }
    _isolated_settings.write_text(
        json.dumps({"ollama_model_names": old_catalog}),
        encoding="utf-8",
    )
    get_settings.cache_clear()
    loaded = load_settings()
    names = loaded["ollama_model_names"]
    for alias, tag in DEFAULT_OLLAMA_MODEL_NAMES.items():
        assert alias in names
        if alias in old_catalog:
            assert names[alias] == old_catalog[alias]
        else:
            assert names[alias] == tag
    assert names["local/coding-light"] == ""
