"""Tests for app/tools/web_search.py and orchestrator dispatch."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.chat_orchestrator import ChatOrchestrator
from app.tools import web_search
from app.types import SearchResult


def test_tool_schema_openai_format() -> None:
    schema = web_search.TOOL_SCHEMA
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "web_search"
    assert fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "query" in params["properties"]
    assert params["properties"]["query"]["type"] == "string"
    assert "max_results" in params["properties"]
    assert params["properties"]["max_results"]["default"] == 5
    assert params["required"] == ["query"]


@pytest.mark.asyncio
async def test_execute_returns_json_results() -> None:
    mock_service = AsyncMock()
    mock_service.search = AsyncMock(
        return_value=[
            SearchResult(
                text="Official site",
                source="https://python.org",
                title="Python",
            ),
            SearchResult(
                text="Documentation",
                source="https://docs.python.org",
                title="Docs",
            ),
        ]
    )

    raw = await web_search.execute("python", 5, mock_service)
    parsed = json.loads(raw)

    assert len(parsed) == 2
    assert parsed[0] == {
        "text": "Official site",
        "source": "https://python.org",
        "title": "Python",
    }
    assert parsed[1]["title"] == "Docs"
    mock_service.search.assert_awaited_once_with("python", max_results=5)


@pytest.mark.asyncio
async def test_execute_empty_results() -> None:
    mock_service = AsyncMock()
    mock_service.search = AsyncMock(return_value=[])

    raw = await web_search.execute("obscure query", 3, mock_service)
    assert json.loads(raw) == []
    mock_service.search.assert_awaited_once_with("obscure query", max_results=3)


@pytest.mark.asyncio
async def test_execute_propagates_search_errors() -> None:
    mock_service = AsyncMock()
    mock_service.search = AsyncMock(side_effect=RuntimeError("provider down"))

    with pytest.raises(RuntimeError, match="provider down"):
        await web_search.execute("fail", 5, mock_service)


@pytest.fixture
def orchestrator_with_search() -> tuple[ChatOrchestrator, AsyncMock]:
    search_service = AsyncMock()
    search_service.search = AsyncMock(return_value=[])

    orchestrator = ChatOrchestrator(
        router=MagicMock(),
        vector_store=AsyncMock(),
        embedding_service=AsyncMock(),
        vision_service=None,
        model_scheduler=None,
        settings=MagicMock(),
        projects=MagicMock(),
        search_service=search_service,
    )
    return orchestrator, search_service


@pytest.mark.asyncio
async def test_get_tool_schema_web_search(
    orchestrator_with_search: tuple[ChatOrchestrator, AsyncMock],
) -> None:
    orchestrator, _ = orchestrator_with_search
    schema = orchestrator._get_tool_schema("web_search")
    assert schema == web_search.TOOL_SCHEMA


@pytest.mark.asyncio
async def test_dispatch_web_search_success(
    orchestrator_with_search: tuple[ChatOrchestrator, AsyncMock],
) -> None:
    orchestrator, search_service = orchestrator_with_search
    search_service.search = AsyncMock(
        return_value=[
            SearchResult(
                text="Result body",
                source="https://example.com",
                title="Example",
            )
        ]
    )

    raw = await orchestrator._dispatch_tool(
        "web_search", json.dumps({"query": "test query", "max_results": 2})
    )
    parsed = json.loads(raw)

    assert parsed == [
        {"text": "Result body", "source": "https://example.com", "title": "Example"}
    ]
    search_service.search.assert_awaited_once_with("test query", max_results=2)


@pytest.mark.asyncio
async def test_dispatch_web_search_default_max_results(
    orchestrator_with_search: tuple[ChatOrchestrator, AsyncMock],
) -> None:
    orchestrator, search_service = orchestrator_with_search

    await orchestrator._dispatch_tool("web_search", json.dumps({"query": "news"}))

    search_service.search.assert_awaited_once_with("news", max_results=5)


@pytest.mark.asyncio
async def test_dispatch_web_search_missing_query(
    orchestrator_with_search: tuple[ChatOrchestrator, AsyncMock],
) -> None:
    orchestrator, search_service = orchestrator_with_search

    raw = await orchestrator._dispatch_tool("web_search", json.dumps({}))
    parsed = json.loads(raw)

    assert "error" in parsed
    search_service.search.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_web_search_invalid_json(
    orchestrator_with_search: tuple[ChatOrchestrator, AsyncMock],
) -> None:
    orchestrator, search_service = orchestrator_with_search

    raw = await orchestrator._dispatch_tool("web_search", "{not json")
    parsed = json.loads(raw)

    assert "error" in parsed
    search_service.search.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_web_search_no_service() -> None:
    orchestrator = ChatOrchestrator(
        router=MagicMock(),
        vector_store=AsyncMock(),
        embedding_service=AsyncMock(),
        vision_service=None,
        model_scheduler=None,
        settings=MagicMock(),
        projects=MagicMock(),
    )

    raw = await orchestrator._dispatch_tool(
        "web_search", json.dumps({"query": "test"})
    )
    parsed = json.loads(raw)

    assert "error" in parsed


@pytest.mark.asyncio
async def test_dispatch_unknown_tool(
    orchestrator_with_search: tuple[ChatOrchestrator, AsyncMock],
) -> None:
    orchestrator, _ = orchestrator_with_search

    raw = await orchestrator._dispatch_tool("unknown_tool", "{}")
    parsed = json.loads(raw)

    assert parsed["status"] == "not_implemented"
    assert parsed["tool"] == "unknown_tool"
