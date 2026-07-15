"""Tests for app/tools/web_fetch.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.tools import web_fetch


def test_tool_schema_openai_format() -> None:
    schema = web_fetch.TOOL_SCHEMA
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "web_fetch"
    assert fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "url" in params["properties"]
    assert params["properties"]["url"]["type"] == "string"
    assert params["required"] == ["url"]


def _mock_response(
    *,
    status_code: int = 200,
    text: str = "",
    url: str = "https://example.com/",
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.url = httpx.URL(url)
    return response


def _patch_client(get: AsyncMock):
    client = AsyncMock()
    client.get = get
    client_cls = MagicMock()
    client_cls.return_value.__aenter__.return_value = client
    return client_cls


@pytest.mark.asyncio
async def test_execute_success_html() -> None:
    html = """
    <html>
      <head><title>Example &amp; Co</title>
      <style>body { color: red; }</style>
      <script>alert('x')</script>
      </head>
      <body><p>Hello   world</p><noscript>hidden</noscript></body>
    </html>
    """
    get = AsyncMock(
        return_value=_mock_response(
            text=html,
            url="https://example.com/page",
        )
    )

    with patch("app.tools.web_fetch.httpx.AsyncClient", _patch_client(get)):
        raw = await web_fetch.execute("https://example.com/page")

    parsed = json.loads(raw)
    assert parsed["url"] == "https://example.com/page"
    assert parsed["title"] == "Example & Co"
    assert "Hello world" in parsed["text"]
    assert "alert" not in parsed["text"]
    assert "color: red" not in parsed["text"]
    assert "hidden" not in parsed["text"]
    assert parsed["truncated"] is False
    get.assert_awaited_once_with("https://example.com/page")


@pytest.mark.asyncio
async def test_execute_truncates_long_text() -> None:
    body = "x" * (web_fetch.MAX_TEXT_CHARS + 500)
    html = f"<html><head><title>Long</title></head><body>{body}</body></html>"
    get = AsyncMock(return_value=_mock_response(text=html))

    with patch("app.tools.web_fetch.httpx.AsyncClient", _patch_client(get)):
        raw = await web_fetch.execute("https://example.com/")

    parsed = json.loads(raw)
    assert parsed["truncated"] is True
    assert len(parsed["text"]) == web_fetch.MAX_TEXT_CHARS
    assert parsed["title"] == "Long"


@pytest.mark.asyncio
async def test_execute_http_404() -> None:
    get = AsyncMock(return_value=_mock_response(status_code=404, text="Nope"))

    with patch("app.tools.web_fetch.httpx.AsyncClient", _patch_client(get)):
        raw = await web_fetch.execute("https://example.com/missing")

    parsed = json.loads(raw)
    assert parsed == {"error": "HTTP 404"}


@pytest.mark.asyncio
async def test_execute_rejects_file_scheme() -> None:
    with patch("app.tools.web_fetch.httpx.AsyncClient") as client_cls:
        raw = await web_fetch.execute("file:///etc/passwd")

    parsed = json.loads(raw)
    assert "error" in parsed
    assert "http" in parsed["error"].lower()
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_execute_rejects_invalid_scheme() -> None:
    with patch("app.tools.web_fetch.httpx.AsyncClient") as client_cls:
        raw = await web_fetch.execute("ftp://example.com/file")

    parsed = json.loads(raw)
    assert "error" in parsed
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_execute_empty_url() -> None:
    with patch("app.tools.web_fetch.httpx.AsyncClient") as client_cls:
        raw = await web_fetch.execute("   ")

    parsed = json.loads(raw)
    assert "error" in parsed
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_execute_timeout() -> None:
    get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

    with patch("app.tools.web_fetch.httpx.AsyncClient", _patch_client(get)):
        raw = await web_fetch.execute("https://example.com/slow")

    parsed = json.loads(raw)
    assert "error" in parsed
    assert "timed out" in parsed["error"]
