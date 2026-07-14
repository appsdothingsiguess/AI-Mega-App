"""Tests for app/litellm_sync.py."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.litellm_sync import sync_litellm_config


def _write_config(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "model_list": [
                    {
                        "model_name": "remote/deepseek-v4-pro",
                        "litellm_params": {
                            "model": "openai/deepseek-v4-pro",
                            "api_base": "https://opencode.ai/zen/go/v1",
                        },
                    },
                    {
                        "model_name": "local/qwen3-8b",
                        "litellm_params": {
                            "model": "ollama_chat/qwen3:8b-32k",
                            "api_base": "http://localhost:11434",
                        },
                    },
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_sync_adds_new_alias(tmp_path: Path) -> None:
    config_path = tmp_path / "litellm_config.yaml"
    _write_config(config_path)

    sync_litellm_config(
        {"local/coding-heavy": "qwen3-coder:30b-16k"},
        "http://localhost:11434",
        config_path,
    )

    data = _load(config_path)
    entries = {e["model_name"]: e for e in data["model_list"]}
    assert "local/coding-heavy" in entries
    assert entries["local/coding-heavy"]["litellm_params"]["model"] == "ollama_chat/qwen3-coder:30b-16k"
    assert entries["local/coding-heavy"]["litellm_params"]["api_base"] == "http://localhost:11434"


def test_sync_updates_existing_alias_in_place(tmp_path: Path) -> None:
    config_path = tmp_path / "litellm_config.yaml"
    _write_config(config_path)

    sync_litellm_config(
        {"local/qwen3-8b": "qwen3:8b-64k"},
        "http://192.168.0.5:11434",
        config_path,
    )

    data = _load(config_path)
    entries = {e["model_name"]: e for e in data["model_list"]}
    assert len(data["model_list"]) == 2
    assert entries["local/qwen3-8b"]["litellm_params"]["model"] == "ollama_chat/qwen3:8b-64k"
    assert entries["local/qwen3-8b"]["litellm_params"]["api_base"] == "http://192.168.0.5:11434"


def test_sync_leaves_remote_and_unrelated_entries_untouched(tmp_path: Path) -> None:
    config_path = tmp_path / "litellm_config.yaml"
    _write_config(config_path)

    sync_litellm_config(
        {"local/coding-heavy": "qwen3-coder:30b-16k"},
        "http://localhost:11434",
        config_path,
    )

    data = _load(config_path)
    entries = {e["model_name"]: e for e in data["model_list"]}
    assert entries["remote/deepseek-v4-pro"]["litellm_params"]["model"] == "openai/deepseek-v4-pro"
    assert entries["remote/deepseek-v4-pro"]["litellm_params"]["api_base"] == "https://opencode.ai/zen/go/v1"


def test_sync_skips_empty_tag_aliases(tmp_path: Path) -> None:
    config_path = tmp_path / "litellm_config.yaml"
    _write_config(config_path)

    sync_litellm_config(
        {"local/coding-light": "", "local/coding-medium": "   "},
        "http://localhost:11434",
        config_path,
    )

    data = _load(config_path)
    names = {e["model_name"] for e in data["model_list"]}
    assert "local/coding-light" not in names
    assert "local/coding-medium" not in names
    assert len(data["model_list"]) == 2


def test_sync_creates_missing_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "nested" / "litellm_config.yaml"

    sync_litellm_config(
        {"local/coding-heavy": "qwen3-coder:30b-16k"},
        "http://localhost:11434",
        config_path,
    )

    data = _load(config_path)
    assert data["model_list"][0]["model_name"] == "local/coding-heavy"
