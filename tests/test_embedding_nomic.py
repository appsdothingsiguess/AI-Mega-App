"""Tests for app/adapters/embedding_nomic.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.embedding_nomic import NomicEmbeddingAdapter
from app.config import OllamaSettings, Settings


def _settings(**overrides: object) -> Settings:
    overrides.setdefault(
        "ollama",
        OllamaSettings(base_url="http://localhost:11434", keep_alive=300),
    )
    return Settings(projects_dir="./projects", data_dir="./data", **overrides)


def _mock_response(embeddings: list[list[float]]) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"embeddings": embeddings}
    return response


@pytest.mark.asyncio
async def test_embed_returns_vectors_and_posts_to_ollama() -> None:
    settings = _settings()
    adapter = NomicEmbeddingAdapter(settings)
    embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    post = AsyncMock(return_value=_mock_response(embeddings))

    with patch("app.adapters.embedding_nomic.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post = post
        client_cls.return_value.__aenter__.return_value = client
        result = await adapter.embed(["hello world", "second text"])

    assert result == embeddings
    post.assert_awaited_once()
    call = post.await_args
    assert call.args[0] == "http://localhost:11434/api/embed"
    assert call.kwargs["json"] == {
        "model": "nomic-embed-text",
        "input": ["hello world", "second text"],
        "keep_alive": 300,
    }


@pytest.mark.asyncio
async def test_embed_overflow_raises_without_http_call() -> None:
    settings = _settings()
    adapter = NomicEmbeddingAdapter(settings)
    overflow_text = " ".join(["word"] * 1576)

    with patch("app.adapters.embedding_nomic.httpx.AsyncClient") as client_cls:
        with pytest.raises(ValueError, match="exceeds embedding limit"):
            await adapter.embed([overflow_text])

    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_embed_empty_input_returns_empty_without_http_call() -> None:
    settings = _settings()
    adapter = NomicEmbeddingAdapter(settings)

    with patch("app.adapters.embedding_nomic.httpx.AsyncClient") as client_cls:
        result = await adapter.embed([])

    assert result == []
    client_cls.assert_not_called()


def test_max_tokens_returns_hard_limit() -> None:
    adapter = NomicEmbeddingAdapter(_settings())
    assert adapter.max_tokens() == 2048
