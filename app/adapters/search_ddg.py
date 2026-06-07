"""DuckDuckGo search adapter."""

from __future__ import annotations

import asyncio
import logging
import time

from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException

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

    def __init__(self) -> None:
        self._ddgs = DDGS()

    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        logger.info("Search query=%r max_results=%d", query, max_results)
        start = time.perf_counter()

        try:
            raw = await asyncio.to_thread(
                self._ddgs.text,
                query,
                max_results=max_results,
            )
        except RatelimitException as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "DuckDuckGo rate limited for query=%r after %.1fms: %s",
                query,
                latency_ms,
                exc,
            )
            return {"error": "rate_limited", "provider": "duckduckgo"}
        except DDGSException as exc:
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
