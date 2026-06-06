"""DuckDuckGo search adapter."""

from __future__ import annotations

import logging
import time

from duckduckgo_search import AsyncDDGS
from duckduckgo_search.exceptions import DuckDuckGoSearchException

from app.types import SearchResult

logger = logging.getLogger("prompter.search")


def _map_result(item: dict[str, str]) -> SearchResult:
    return SearchResult(
        text=item.get("body") or item.get("snippet") or "",
        source=item.get("href") or item.get("url") or "",
        title=item.get("title") or "",
        score=0.0,
    )


class DuckDuckGoSearchAdapter:
    """SearchService implementation using DuckDuckGo."""

    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        logger.info("Search query=%r max_results=%d", query, max_results)
        start = time.perf_counter()

        try:
            async with AsyncDDGS() as ddgs:
                raw = await ddgs.atext(query, max_results=max_results)
        except DuckDuckGoSearchException as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "DuckDuckGo search failed for query=%r after %.1fms: %s",
                query,
                latency_ms,
                exc,
            )
            return []

        results = [_map_result(item) for item in raw]
        latency_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Search query=%r returned %d result(s) in %.1fms",
            query,
            len(results),
            latency_ms,
        )
        return results
