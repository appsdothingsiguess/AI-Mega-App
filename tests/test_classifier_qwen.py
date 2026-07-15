"""Tests for app/adapters/classifier_qwen.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.classifier_qwen import QwenClassifierAdapter
from app.config import OllamaSettings, RouterSettings, Settings


def _settings() -> Settings:
    return Settings(
        projects_dir="./projects",
        data_dir="./data",
        router=RouterSettings(
            classifier="ollama/qwen2.5:1.5b",
            classifier_prompt="Classify this message.",
            rules=[],
        ),
        ollama=OllamaSettings(base_url="http://ollama.test", keep_alive=-1),
    )


def _mock_response(response_text: str) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"response": response_text}
    return response


@pytest.mark.asyncio
async def test_classify_posts_expected_ollama_payload() -> None:
    adapter = QwenClassifierAdapter(_settings())
    post = AsyncMock(
        return_value=_mock_response(
            json.dumps(
                {
                    "intent": "web_search",
                    "tools": ["web_search"],
                    "confidence": 0.91,
                }
            )
        )
    )

    with patch("app.adapters.classifier_qwen.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        await adapter.classify("Look up today's weather")

    post.assert_awaited_once()
    call = post.await_args
    assert call.args[0] == "http://ollama.test/api/generate"
    payload = call.kwargs["json"]
    assert payload["model"] == "qwen2.5:1.5b"
    assert payload["system"] == "Classify this message."
    assert payload["prompt"] == "Look up today's weather"
    assert payload["stream"] is False
    assert payload["keep_alive"] == -1
    assert payload["options"] == {
        "temperature": 0.0,
        "top_k": 20,
        "top_p": 0.8,
        "repeat_penalty": 1.05,
        "num_predict": 250,
        "num_ctx": 8192,
        "num_gpu": 999,
    }


@pytest.mark.asyncio
async def test_warmup_uses_same_gpu_options_as_classify() -> None:
    adapter = QwenClassifierAdapter(_settings())
    post = AsyncMock(return_value=_mock_response(""))

    with patch("app.adapters.classifier_qwen.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        await adapter.warmup()

    post.assert_awaited_once()
    payload = post.await_args.kwargs["json"]
    assert payload["model"] == "qwen2.5:1.5b"
    assert payload["prompt"] == ""
    assert payload["keep_alive"] == -1
    assert payload["options"] == {
        "temperature": 0.0,
        "top_k": 20,
        "top_p": 0.8,
        "repeat_penalty": 1.05,
        "num_predict": 250,
        "num_ctx": 8192,
        "num_gpu": 999,
    }


@pytest.mark.asyncio
async def test_classify_parses_successful_json_response() -> None:
    adapter = QwenClassifierAdapter(_settings())
    post = AsyncMock(
        return_value=_mock_response(
            json.dumps(
                {
                    "intent": "coding_basic",
                    "tools": [],
                    "confidence": "0.76",
                }
            )
        )
    )

    with patch("app.adapters.classifier_qwen.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        result = await adapter.classify("Write a helper function")

    assert result.intent == "coding_basic"
    assert result.tools == []
    assert result.confidence == 0.76


@pytest.mark.asyncio
async def test_classify_returns_fallback_on_parse_failure() -> None:
    adapter = QwenClassifierAdapter(_settings())
    post = AsyncMock(return_value=_mock_response("not json"))

    with patch("app.adapters.classifier_qwen.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        result = await adapter.classify("Hello")

    assert result.intent == "general_chat"
    assert result.tools == []
    assert result.confidence == 0.0


def test_extract_json_strips_markdown_fence() -> None:
    wrapped = '```json\n{"intent":"web_search","tools":["web_search"]}\n```'
    assert QwenClassifierAdapter._extract_json(wrapped) == (
        '{"intent":"web_search","tools":["web_search"]}'
    )


def test_extract_json_finds_object_in_prose() -> None:
    text = 'Here is the result: {"intent":"bash","tools":["bash"]} thanks'
    assert QwenClassifierAdapter._extract_json(text) == (
        '{"intent":"bash","tools":["bash"]}'
    )


@pytest.mark.asyncio
async def test_classify_parses_markdown_wrapped_json() -> None:
    adapter = QwenClassifierAdapter(_settings())
    post = AsyncMock(
        return_value=_mock_response(
            '```json\n'
            + json.dumps(
                {
                    "intent": "web_search",
                    "tools": ["web_search"],
                    "confidence": 0.5,
                }
            )
            + "\n```"
        )
    )

    with patch("app.adapters.classifier_qwen.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        result = await adapter.classify("What's the weather?")

    assert result.intent == "web_search"
    assert result.tools == ["web_search"]
