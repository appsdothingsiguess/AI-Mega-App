"""Tests for app/model_scheduler.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import OllamaSettings, Settings, get_settings
from app.model_scheduler import ModelScheduler, reset_model_scheduler, _strip_ollama_prefix


@pytest.fixture(autouse=True)
def _reset_scheduler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_JSON_PATH", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    get_settings.cache_clear()
    reset_model_scheduler()
    yield
    reset_model_scheduler()
    get_settings.cache_clear()


def _settings(**overrides: object) -> Settings:
    return Settings(
        projects_dir="./projects",
        data_dir="./data",
        ollama=OllamaSettings(base_url="http://localhost:11434"),
        **overrides,
    )


def _mock_response() -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    return response


@pytest.mark.asyncio
async def test_skip_when_resident_classifier() -> None:
    settings = _settings()
    scheduler = ModelScheduler(settings)
    resident_name = _strip_ollama_prefix(settings.router.classifier)

    with patch("app.model_scheduler.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client_cls.return_value.__aenter__.return_value = client
        await scheduler.ensure_loaded(f"ollama/{resident_name}")

    client.post.assert_not_called()
    assert scheduler._loaded_main is None


@pytest.mark.asyncio
async def test_warmup_resident_embedding_uses_embed_endpoint() -> None:
    settings = _settings()
    scheduler = ModelScheduler(settings)
    post = AsyncMock(return_value=_mock_response())

    with patch("app.model_scheduler.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        await scheduler.warmup_resident()

    post.assert_awaited_once()
    call = post.await_args
    assert call.args[0] == "http://localhost:11434/api/embed"
    assert call.kwargs["json"] == {
        "model": "nomic-embed-text",
        "input": "warmup",
        "keep_alive": 300,
    }


@pytest.mark.asyncio
async def test_skip_when_already_loaded_main() -> None:
    settings = _settings()
    scheduler = ModelScheduler(settings)
    scheduler._loaded_main = "qwen3:8b-32k"

    with patch("app.model_scheduler.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client_cls.return_value.__aenter__.return_value = client
        await scheduler.ensure_loaded("local/qwen3-8b")

    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_unload_and_warmup_on_swap() -> None:
    settings = _settings()
    scheduler = ModelScheduler(settings)
    scheduler._loaded_main = "qwen2.5-coder:7b-32k"
    responses = [_mock_response(), _mock_response()]
    post = AsyncMock(side_effect=responses)

    with patch("app.model_scheduler.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        await scheduler.ensure_loaded("local/qwen3-8b")

    assert post.await_count == 2
    unload_call = post.await_args_list[0]
    warmup_call = post.await_args_list[1]
    assert unload_call.args[0] == "http://localhost:11434/api/generate"
    assert unload_call.kwargs["json"] == {"model": "qwen2.5-coder:7b-32k", "keep_alive": 0}
    assert warmup_call.kwargs["json"] == {
        "model": "qwen3:8b-32k",
        "prompt": "",
        "keep_alive": 300,
    }
    assert scheduler._loaded_main == "qwen3:8b-32k"


@pytest.mark.asyncio
async def test_serialization_behind_lock() -> None:
    settings = _settings()
    scheduler = ModelScheduler(settings)
    release_after_first_warmup = asyncio.Event()
    responses = [_mock_response(), _mock_response(), _mock_response()]

    original_warmup = scheduler._warmup

    async def gated_warmup(model: str, retries: int = 3, backoff: float = 1.0) -> None:
        await original_warmup(model, retries=retries, backoff=backoff)
        if model == "qwen3:8b-32k":
            release_after_first_warmup.set()

    async def slow_warmup(model: str, retries: int = 3, backoff: float = 1.0) -> None:
        await gated_warmup(model, retries=retries, backoff=backoff)
        if model == "qwen2.5-coder:7b-32k":
            await release_after_first_warmup.wait()

    post = AsyncMock(side_effect=responses)
    with patch.object(scheduler, "_warmup", side_effect=slow_warmup):
        with patch("app.model_scheduler.httpx.AsyncClient") as client_cls:
            client = AsyncMock()
            client.post = post
            client_cls.return_value.__aenter__.return_value = client
            await asyncio.gather(
                scheduler.ensure_loaded("local/qwen3-8b"),
                scheduler.ensure_loaded("local/qwen2.5-coder-7b"),
            )

    assert scheduler._loaded_main == "qwen2.5-coder:7b-32k"


@pytest.mark.asyncio
async def test_warmup_retry_exponential_backoff() -> None:
    settings = _settings()
    scheduler = ModelScheduler(settings)
    success = _mock_response()
    post = AsyncMock(
        side_effect=[
            httpx.ConnectError("down"),
            httpx.ConnectError("still down"),
            success,
        ]
    )
    sleep = AsyncMock()

    with patch("app.model_scheduler.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        with patch("app.model_scheduler.asyncio.sleep", sleep):
            await scheduler._warmup("qwen3:8b")

    assert post.await_count == 3
    sleep.assert_any_await(1.0)
    sleep.assert_any_await(2.0)


@pytest.mark.asyncio
async def test_warmup_raises_after_retries_exhausted() -> None:
    settings = _settings()
    scheduler = ModelScheduler(settings)
    post = AsyncMock(side_effect=httpx.ConnectError("down"))
    sleep = AsyncMock()

    with patch("app.model_scheduler.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        with patch("app.model_scheduler.asyncio.sleep", sleep):
            with pytest.raises(httpx.ConnectError):
                await scheduler._warmup("qwen3:8b")

    assert post.await_count == 3


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("local/qwen3-8b", "qwen3:8b-32k"),
        ("local/qwen2.5-coder-7b", "qwen2.5-coder:7b-32k"),
        ("local/deepseek-r1-8b", "deepseek-r1:8b-32k"),
        ("local/deepseek-r1-32b", "deepseek-r1:32b-16k"),
    ],
)
def test_alias_to_ollama_name_mapping(alias: str, expected: str) -> None:
    settings = _settings()
    scheduler = ModelScheduler(settings)
    assert scheduler._alias_to_ollama_name(alias) == expected
