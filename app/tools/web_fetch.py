"""web_fetch tool — fetch a URL and return plain text extracted from HTML."""

from __future__ import annotations

import json
import logging
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("prompter.mcp")

MAX_TEXT_CHARS = 8000
REQUEST_TIMEOUT_S = 15.0
MAX_REDIRECTS = 3


TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "Fetch a web page by URL and return its title and text content",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "HTTP or HTTPS URL to fetch",
                },
            },
            "required": ["url"],
        },
    },
}


class _HTMLTextExtractor(HTMLParser):
    """Stdlib HTML → text extractor; skips script/style and collects title."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in ("script", "style", "noscript"):
            self._skip_depth += 1
        elif lowered == "title" and self._skip_depth == 0:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in ("script", "style", "noscript"):
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif lowered == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self._title_parts.append(data)
        else:
            self._text_parts.append(data)

    @property
    def title(self) -> str:
        return unescape("".join(self._title_parts)).strip()

    @property
    def text(self) -> str:
        raw = unescape("".join(self._text_parts))
        return " ".join(raw.split())


def _extract_html(html: str) -> tuple[str, str]:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed HTML still yields whatever was collected.
        pass
    return parser.title, parser.text


def _error(message: str) -> str:
    return json.dumps({"error": message})


async def execute(url: str) -> str:
    """Fetch ``url`` and return JSON with title, text, and truncation flag."""
    if not isinstance(url, str) or not url.strip():
        return _error("Missing or invalid required parameter: url")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return _error("Only http and https URLs are allowed")

    logger.info("web_fetch url=%r", url)
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_S,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
        ) as client:
            response = await client.get(url.strip())
    except httpx.HTTPError as exc:
        return _error(str(exc))

    if not 200 <= response.status_code < 300:
        return _error(f"HTTP {response.status_code}")

    title, text = _extract_html(response.text)
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS]

    final_url = str(response.url) if response.url else url.strip()
    return json.dumps(
        {
            "url": final_url,
            "title": title,
            "text": text,
            "truncated": truncated,
        }
    )
