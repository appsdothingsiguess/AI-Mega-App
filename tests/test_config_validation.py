"""Tests for app/config_validation.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import DEFAULT_OLLAMA_MODEL_NAMES, ModelsConfig, Settings
from app.config_validation import validate_config


def _settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    **overrides: object,
) -> Settings:
    if monkeypatch is not None:
        isolated = tmp_path / "isolated_settings.json"
        isolated.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("SETTINGS_JSON_PATH", str(isolated))
    return Settings(
        projects_dir=tmp_path / "projects",
        data_dir=tmp_path / "data",
        litellm_config_path=str(tmp_path / "litellm_config.yaml"),
        **overrides,
    )


def _write_litellm_config(path: Path, model_names: list[str]) -> None:
    lines = ["model_list:"]
    for name in model_names:
        lines.append(f"  - model_name: {name}")
        lines.append("    litellm_params:")
        lines.append("      model: ollama/placeholder")
        lines.append("      api_base: http://localhost:11434")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_missing_litellm_alias_is_error(tmp_path: Path) -> None:
    litellm_path = tmp_path / "litellm_config.yaml"
    _write_litellm_config(litellm_path, ["remote/deepseek-v4-pro"])
    settings = _settings(
        tmp_path,
        models=ModelsConfig(coding_basic="local/missing-model"),
        ollama_model_names=dict(DEFAULT_OLLAMA_MODEL_NAMES),
    )

    with patch("app.config_validation.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        tags_response = MagicMock()
        tags_response.raise_for_status = MagicMock()
        collections_response = MagicMock()
        collections_response.raise_for_status = MagicMock()
        client.get = AsyncMock(side_effect=[tags_response, collections_response])
        client_cls.return_value.__aenter__.return_value = client

        errors, warnings = await validate_config(settings)

    assert any("settings.models.coding_basic" in error for error in errors)
    assert warnings == []


@pytest.mark.asyncio
async def test_missing_ollama_model_names_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    litellm_path = tmp_path / "litellm_config.yaml"
    _write_litellm_config(
        litellm_path,
        [
            "local/qwen3-8b",
            "remote/deepseek-v4-pro",
            "remote/kimi-k2-6",
            "local/qwen2.5-coder-7b",
            "local/qwen2.5-vl-3b",
        ],
    )
    settings = _settings(
        tmp_path,
        monkeypatch,
        ollama_model_names={
            "local/qwen2.5-coder-7b": "qwen2.5-coder:7b",
            "local/qwen2.5-vl-3b": "qwen2.5-vl-3b",
        },
    )

    with patch("app.config_validation.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        tags_response = MagicMock()
        tags_response.raise_for_status = MagicMock()
        collections_response = MagicMock()
        collections_response.raise_for_status = MagicMock()
        client.get = AsyncMock(side_effect=[tags_response, collections_response])
        client_cls.return_value.__aenter__.return_value = client

        errors, warnings = await validate_config(settings)

    assert any("local/qwen3-8b" in error for error in errors)
    assert any("ollama_model_names" in error for error in errors)


@pytest.mark.asyncio
async def test_unreachable_ollama_is_error_when_local_models_configured(
    tmp_path: Path,
) -> None:
    litellm_path = tmp_path / "litellm_config.yaml"
    _write_litellm_config(
        litellm_path,
        [
            "local/qwen3-8b",
            "local/qwen2.5-coder-7b",
            "local/qwen2.5-vl-3b",
            "remote/deepseek-v4-pro",
            "remote/kimi-k2-6",
        ],
    )
    settings = _settings(tmp_path)

    with patch("app.config_validation.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        client_cls.return_value.__aenter__.return_value = client

        errors, warnings = await validate_config(settings)

    assert any("Ollama unreachable" in error for error in errors)


@pytest.mark.asyncio
async def test_unreachable_qdrant_is_warning_only(tmp_path: Path) -> None:
    litellm_path = tmp_path / "litellm_config.yaml"
    _write_litellm_config(
        litellm_path,
        [
            "local/qwen3-8b",
            "local/qwen2.5-coder-7b",
            "local/qwen2.5-vl-3b",
            "remote/deepseek-v4-pro",
            "remote/kimi-k2-6",
        ],
    )
    settings = _settings(tmp_path)

    with patch("app.config_validation.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        tags_response = MagicMock()
        tags_response.raise_for_status = MagicMock()

        async def get_side_effect(url: str, **kwargs: object) -> MagicMock:
            if url.endswith("/api/tags"):
                return tags_response
            raise httpx.ConnectError("qdrant down")

        client.get = AsyncMock(side_effect=get_side_effect)
        client_cls.return_value.__aenter__.return_value = client

        errors, warnings = await validate_config(settings)

    assert errors == []
    assert len(warnings) == 1
    assert "Qdrant unreachable" in warnings[0]
