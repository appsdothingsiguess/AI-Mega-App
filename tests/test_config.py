from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app import config as config_mod
from app.config import Config, ConfigError, get_config, load_config, reset_config_cache

MINIMAL_MODEL = {
    "name": "chat-default",
    "class": "general",
    "ctx": 4096,
    "gpu": 0,
    "tool_call": "native",
    "max_tokens": 1024,
}

MINIMAL_CONFIG: dict = {
    "llama_swap": {"base_url": "http://127.0.0.1:8080/v1"},
    "models": [MINIMAL_MODEL],
    "defaults": {
        "chat_model": "chat-default",
        "utility_model": "chat-default",
        "title_model": "chat-default",
    },
}


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_repo_config_round_trips_with_zero_validation_errors() -> None:
    """The checked-in config.yaml must load cleanly with no overlay present."""
    cfg = load_config(config_mod.CONFIG_PATH)
    assert isinstance(cfg, Config)
    assert cfg.defaults.chat_model == "chat-default"
    assert any(m.name == "chat-default" for m in cfg.models)


def test_valid_minimal_config_loads(tmp_path: Path) -> None:
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, MINIMAL_CONFIG)
    cfg = load_config(base_path)
    assert cfg.server.port == 8000  # default
    assert cfg.models[0].class_ == "general"


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path / "does-not-exist.yaml")
    assert "not found" in exc_info.value.reason


def test_unknown_key_rejected_with_path(tmp_path: Path) -> None:
    bad = dict(MINIMAL_CONFIG)
    bad["not_a_real_section"] = {"x": 1}
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, bad)
    with pytest.raises(ConfigError) as exc_info:
        load_config(base_path)
    assert "not_a_real_section" in exc_info.value.key_path


def test_missing_required_field_reports_key_path(tmp_path: Path) -> None:
    bad = {"models": [MINIMAL_MODEL], "defaults": MINIMAL_CONFIG["defaults"]}
    # llama_swap.base_url is required and missing entirely.
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, bad)
    with pytest.raises(ConfigError) as exc_info:
        load_config(base_path)
    assert "llama_swap" in exc_info.value.key_path


def test_overlay_merge_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_path = tmp_path / "config.yaml"
    overlay_path = tmp_path / "settings.local.yaml"
    write_yaml(base_path, MINIMAL_CONFIG)
    write_yaml(overlay_path, {"server": {"port": 9999}})
    monkeypatch.setattr(config_mod, "OVERLAY_PATH", overlay_path)

    cfg = load_config(base_path)
    assert cfg.server.port == 9999  # overlay wins
    assert cfg.models[0].name == "chat-default"  # base untouched otherwise


def test_no_overlay_file_uses_base_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, MINIMAL_CONFIG)
    monkeypatch.setattr(config_mod, "OVERLAY_PATH", tmp_path / "no-such-overlay.yaml")

    cfg = load_config(base_path)
    assert cfg.server.port == 8000


def test_get_config_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_config_cache()
    calls: list[Path] = []
    real_load = config_mod.load_config

    def spy(path: Path = config_mod.CONFIG_PATH) -> Config:
        calls.append(path)
        return real_load(path)

    monkeypatch.setattr(config_mod, "load_config", spy)
    first = get_config()
    second = get_config()
    assert first is second
    assert len(calls) == 1
    reset_config_cache()


def test_reset_config_cache_forces_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_config_cache()
    first = get_config()
    reset_config_cache()
    second = get_config()
    assert first is not second
    reset_config_cache()
