"""Tests for app/litellm_resolver.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.litellm_resolver import (
    LitellmAliasError,
    reset_litellm_resolver_cache,
    resolve_litellm_params,
)


def _write_litellm_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "model_list:",
                "  - model_name: local/qwen3-8b",
                "    litellm_params:",
                "      model: ollama/qwen3:8b",
                "      api_base: http://localhost:11434",
                "  - model_name: remote/deepseek-v4-pro",
                "    litellm_params:",
                "      model: openai/deepseek-v4-pro",
                "      api_base: https://opencode.ai/zen/go/v1",
                "      api_key: os.environ/OPENCODE_API_KEY",
                "      extra_headers:",
                "        User-Agent: prompter-x/1.0",
                "",
            ]
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clear_resolver_cache() -> None:
    reset_litellm_resolver_cache()
    yield
    reset_litellm_resolver_cache()


def test_resolve_local_alias(tmp_path: Path) -> None:
    config_path = tmp_path / "litellm_config.yaml"
    _write_litellm_config(config_path)
    settings = Settings(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "data",
        litellm_config_path=str(config_path),
    )

    params = resolve_litellm_params(settings, "local/qwen3-8b")

    assert params["model"] == "ollama/qwen3:8b"
    assert params["api_base"] == "http://localhost:11434"


def test_resolve_remote_alias_uses_env_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated = tmp_path / "isolated_settings.json"
    isolated.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SETTINGS_JSON_PATH", str(isolated))
    monkeypatch.setenv("OPENCODE_API_KEY", "test-opencode-key")
    config_path = tmp_path / "litellm_config.yaml"
    _write_litellm_config(config_path)
    settings = Settings(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "data",
        litellm_config_path=str(config_path),
    )

    params = resolve_litellm_params(settings, "remote/deepseek-v4-pro")

    assert params["model"] == "openai/deepseek-v4-pro"
    assert params["api_base"] == "https://opencode.ai/zen/go/v1"
    assert params["api_key"] == "test-opencode-key"
    assert params["extra_headers"] == {"User-Agent": "prompter-x/1.0"}


def test_missing_alias_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "litellm_config.yaml"
    _write_litellm_config(config_path)
    settings = Settings(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "data",
        litellm_config_path=str(config_path),
    )

    with pytest.raises(LitellmAliasError, match="missing-model"):
        resolve_litellm_params(settings, "remote/missing-model")
