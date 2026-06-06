"""web_search MCP tool — thin wrapper around SearchService."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.protocols import SearchService
from app.types import SearchResult

logger = logging.getLogger("prompter.mcp")

DEFAULT_MAX_RESULTS = 5

TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return",
                    "default": DEFAULT_MAX_RESULTS,
                },
            },
            "required": ["query"],
        },
    },
}


def _serialize_result(result: SearchResult) -> dict[str, str]:
    return {
        "text": result.text,
        "source": result.source,
        "title": result.title,
    }


async def execute(
    query: str,
    max_results: int,
    search_service: SearchService,
) -> str:
    """Run web search via SearchService and return JSON-encoded results."""
    logger.info("web_search query=%r max_results=%d", query, max_results)
    results = await search_service.search(query, max_results=max_results)
    return json.dumps([_serialize_result(r) for r in results])
