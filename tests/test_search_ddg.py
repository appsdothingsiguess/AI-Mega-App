"""Tests for app/adapters/search_ddg.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from duckduckgo_search.exceptions import RatelimitException

from app.adapters.search_ddg import DuckDuckGoSearchAdapter
from app.protocols import SearchService


def _mock_ddgs(*, atext_return=None, atext_side_effect=None) -> AsyncMock:
    mock_ddgs = AsyncMock()
    if atext_side_effect is not None:
        mock_ddgs.atext = AsyncMock(side_effect=atext_side_effect)
    else:
        mock_ddgs.atext = AsyncMock(return_value=atext_return or [])
    mock_ddgs.__aenter__ = AsyncMock(return_value=mock_ddgs)
    mock_ddgs.__aexit__ = AsyncMock(return_value=None)
    return mock_ddgs


def test_adapter_satisfies_search_service_protocol() -> None:
    adapter = DuckDuckGoSearchAdapter()
    assert isinstance(adapter, SearchService)


@pytest.mark.asyncio
async def test_search_returns_mapped_results() -> None:
    raw = [
        {"title": "Python", "href": "https://python.org", "body": "Official site"},
        {"title": "Docs", "href": "https://docs.python.org", "body": "Documentation"},
    ]
    mock_ddgs = _mock_ddgs(atext_return=raw)

    with patch("app.adapters.search_ddg.AsyncDDGS", return_value=mock_ddgs):
        adapter = DuckDuckGoSearchAdapter()
        results = await adapter.search("python", max_results=2)

    assert len(results) == 2
    assert results[0].title == "Python"
    assert results[0].source == "https://python.org"
    assert results[0].text == "Official site"
    assert results[0].score == 0.0
    assert results[1].title == "Docs"
    mock_ddgs.atext.assert_awaited_once_with("python", max_results=2)


@pytest.mark.asyncio
async def test_search_handles_ddg_errors() -> None:
    mock_ddgs = _mock_ddgs(atext_side_effect=RatelimitException("rate limited"))

    with patch("app.adapters.search_ddg.AsyncDDGS", return_value=mock_ddgs):
        adapter = DuckDuckGoSearchAdapter()
        results = await adapter.search("python")

    assert results == []


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    mock_ddgs = _mock_ddgs(atext_return=[])

    with patch("app.adapters.search_ddg.AsyncDDGS", return_value=mock_ddgs):
        adapter = DuckDuckGoSearchAdapter()
        results = await adapter.search("obscure query with no hits")

    assert results == []
