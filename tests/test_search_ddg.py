"""Tests for app/adapters/search_ddg.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ddgs.exceptions import DDGSException, RatelimitException

from app.adapters.search_ddg import DuckDuckGoSearchAdapter
from app.protocols import SearchService


def _mock_ddgs(*, text_return=None, text_side_effect=None) -> MagicMock:
    mock_ddgs = MagicMock()
    if text_side_effect is not None:
        mock_ddgs.text.side_effect = text_side_effect
    else:
        mock_ddgs.text.return_value = text_return or []
    return mock_ddgs


def test_adapter_satisfies_search_service_protocol() -> None:
    with patch("app.adapters.search_ddg.DDGS"):
        adapter = DuckDuckGoSearchAdapter()
    assert isinstance(adapter, SearchService)


@pytest.mark.asyncio
async def test_search_returns_mapped_results() -> None:
    raw = [
        {"title": "Python", "href": "https://python.org", "body": "Official site"},
        {"title": "Docs", "href": "https://docs.python.org", "body": "Documentation"},
    ]
    mock_ddgs = _mock_ddgs(text_return=raw)

    with patch("app.adapters.search_ddg.DDGS", return_value=mock_ddgs):
        adapter = DuckDuckGoSearchAdapter()
        results = await adapter.search("python", max_results=2)

    assert len(results) == 2
    assert results[0].title == "Python"
    assert results[0].source == "https://python.org"
    assert results[0].text == "Official site"
    assert results[0].score == 0.0
    assert results[1].title == "Docs"
    mock_ddgs.text.assert_called_once_with("python", max_results=2)


@pytest.mark.asyncio
async def test_search_handles_ddg_errors() -> None:
    mock_ddgs = _mock_ddgs(text_side_effect=DDGSException("provider error"))

    with patch("app.adapters.search_ddg.DDGS", return_value=mock_ddgs):
        adapter = DuckDuckGoSearchAdapter()
        results = await adapter.search("python")

    assert results == []


@pytest.mark.asyncio
async def test_search_rate_limit_returns_structured_error() -> None:
    mock_ddgs = _mock_ddgs(text_side_effect=RatelimitException("202 Ratelimit"))

    with patch("app.adapters.search_ddg.DDGS", return_value=mock_ddgs):
        adapter = DuckDuckGoSearchAdapter()
        results = await adapter.search("python")

    assert results == {"error": "rate_limited", "provider": "duckduckgo"}


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    mock_ddgs = _mock_ddgs(text_return=[])

    with patch("app.adapters.search_ddg.DDGS", return_value=mock_ddgs):
        adapter = DuckDuckGoSearchAdapter()
        results = await adapter.search("obscure query with no hits")

    assert results == []
