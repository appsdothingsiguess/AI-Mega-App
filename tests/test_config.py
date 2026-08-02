from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app import config as config_mod
from app.config import (
    Config,
    ConfigError,
    RoutingRule,
    get_config,
    load_config,
    reset_config_cache,
)

MINIMAL_MODEL = {
    "name": "chat-default",
    "class": "general",
    "ctx": 4096,
    "gpu": 0,
    "tool_call": "native",
    "max_tokens": 1024,
    "file": "/models/chat-default.gguf",
    "quant": "Q4_K_M",
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


# ---------------------------------------------------------------------------
# New fields on ModelEntry
# ---------------------------------------------------------------------------

def test_model_entry_new_fields_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_mod, "OVERLAY_PATH", tmp_path / "no-such-overlay.yaml")
    cfg_data = dict(MINIMAL_CONFIG)
    cfg_data["models"] = [
        {
            **MINIMAL_MODEL,
            "file": "/models/chat.gguf",
            "quant": "Q5_K_M",
            "mmproj": "/models/chat-mmproj.gguf",
            "resident": True,
            "ttl_s": 0,
            "extra_flags": ["--cache-type-k", "q8_0"],
        }
    ]
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, cfg_data)
    cfg = load_config(base_path)
    m = cfg.models[0]
    assert m.file == "/models/chat.gguf"
    assert m.quant == "Q5_K_M"
    assert m.mmproj == "/models/chat-mmproj.gguf"
    assert m.resident is True
    assert m.ttl_s == 0
    assert m.extra_flags == ["--cache-type-k", "q8_0"]


def test_model_entry_defaults_for_optional_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_mod, "OVERLAY_PATH", tmp_path / "no-such-overlay.yaml")
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, MINIMAL_CONFIG)
    cfg = load_config(base_path)
    m = cfg.models[0]
    assert m.mmproj is None
    assert m.resident is False
    assert m.ttl_s is None
    assert m.extra_flags == []


# ---------------------------------------------------------------------------
# gpu section
# ---------------------------------------------------------------------------

def test_repo_config_gpu_section(tmp_path: Path) -> None:
    cfg = load_config(config_mod.CONFIG_PATH)
    assert cfg.gpu.rewarm_default_after_min == 10
    assert cfg.gpu.enabled is True
    assert cfg.gpu.swap_yaml_path == (
        "/home/john/llm-stack/serving/llama-swap/config.yaml"
    )
    assert cfg.gpu.reload_on_change is True
    assert cfg.gpu.vram_guard is True


def test_gpu_defaults_when_absent(tmp_path: Path) -> None:
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, MINIMAL_CONFIG)
    cfg = load_config(base_path)
    assert cfg.gpu.rewarm_default_after_min == 10
    assert cfg.gpu.enabled is True
    assert cfg.gpu.reload_on_change is True
    assert cfg.gpu.vram_guard is True


# ---------------------------------------------------------------------------
# routing section
# ---------------------------------------------------------------------------

def test_repo_config_routing_intents(tmp_path: Path) -> None:
    cfg = load_config(config_mod.CONFIG_PATH)
    assert cfg.routing.intents.code_task == "coder"
    assert cfg.routing.intents.reasoning_task == "reasoner"
    assert cfg.routing.intents.vision_task == "vision"
    assert cfg.routing.intents.chat == "chat-default"
    assert cfg.routing.intents.chit_chat == "chat-default"
    assert cfg.routing.intents.tool_call_needed == "chat-default"


def test_repo_config_routing_classifier(tmp_path: Path) -> None:
    cfg = load_config(config_mod.CONFIG_PATH)
    assert cfg.routing.classifier.model == "classifier"
    assert cfg.routing.classifier.timeout_s == 2.0
    assert cfg.routing.classifier.confidence_threshold == 0.5
    assert cfg.routing.classifier.fallback_model == "chat-default"


def test_repo_config_routing_rules_and_attachments(tmp_path: Path) -> None:
    cfg = load_config(config_mod.CONFIG_PATH)
    assert len(cfg.routing.rules) >= 1
    assert cfg.routing.attachments["image"] == "vision_task"
    assert cfg.routing.attachments["code_file"] == "code_task"


def test_routing_defaults_when_absent(tmp_path: Path) -> None:
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, MINIMAL_CONFIG)
    cfg = load_config(base_path)
    assert cfg.routing.rules == []
    assert cfg.routing.attachments == {}
    assert cfg.routing.intents.code_task == "coder"
    assert cfg.routing.classifier.timeout_s == 2.0


def test_routing_rule_keyword_must_be_multi_word() -> None:
    with pytest.raises(Exception):
        RoutingRule(keywords=["singleword"], intent="code_task")


def test_routing_rule_keyword_multi_word_accepted() -> None:
    rule = RoutingRule(keywords=["write code", "stack trace"], intent="code_task")
    assert rule.keywords == ["write code", "stack trace"]


def test_routing_rule_single_word_rejected_via_config(tmp_path: Path) -> None:
    bad = dict(MINIMAL_CONFIG)
    bad["routing"] = {
        "rules": [{"keywords": ["badkeyword"], "intent": "code_task"}]
    }
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, bad)
    with pytest.raises(ConfigError):
        load_config(base_path)


# ---------------------------------------------------------------------------
# background section
# ---------------------------------------------------------------------------

def test_repo_config_background_section(tmp_path: Path) -> None:
    cfg = load_config(config_mod.CONFIG_PATH)
    assert cfg.background.title_model == "dispatcher"
    assert cfg.background.summary_model == "utility"
    assert cfg.background.summary_every_n_turns == 6


def test_background_defaults_when_absent(tmp_path: Path) -> None:
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, MINIMAL_CONFIG)
    cfg = load_config(base_path)
    assert cfg.background.title_model == "dispatcher"
    assert cfg.background.summary_model == "utility"
    assert cfg.background.summary_every_n_turns == 6


# ---------------------------------------------------------------------------
# backward-compat: MINIMAL_CONFIG (no routing/gpu/background) still loads
# ---------------------------------------------------------------------------

def test_minimal_config_backward_compat(tmp_path: Path) -> None:
    """Old configs that omit routing, gpu, and background keys must still
    validate cleanly — all three sections default-construct."""
    base_path = tmp_path / "config.yaml"
    write_yaml(base_path, MINIMAL_CONFIG)
    cfg = load_config(base_path)
    assert isinstance(cfg, Config)
    assert cfg.gpu.rewarm_default_after_min == 10
    assert cfg.routing.rules == []
    assert cfg.background.summary_every_n_turns == 6
