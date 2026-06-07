"""Debug SSE trace helpers: redaction and event formatting."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "secret",
        "token",
        "password",
        "opencode_api_key",
        "tavily_api_key",
    }
)

_REDACTED = "***REDACTED***"
_BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/=]{200,}")
_DATA_URL_RE = re.compile(r"data:[^;]+;base64,[A-Za-z0-9+/=]+")


def _should_redact_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SENSITIVE_KEYS or any(
        part in lowered for part in ("secret", "password", "token", "api_key")
    )


def _redact_string(value: str) -> str:
    if value.startswith("data:") and "base64," in value:
        return "[image omitted]"
    if _DATA_URL_RE.search(value):
        return _DATA_URL_RE.sub("[base64 omitted]", value)
    if len(value) > 500 and _BASE64_BLOB_RE.fullmatch(value):
        return "[base64 omitted]"
    return value


def redact_value(key: str, value: Any) -> Any:
    """Recursively redact sensitive values from trace payloads."""
    if _should_redact_key(key):
        return _REDACTED
    if isinstance(value, dict):
        return redact_dict(value)
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {key: redact_value(key, value) for key, value in data.items()}


def sanitize_messages_for_trace(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy messages with image data URLs replaced for debug SSE."""
    sanitized = deepcopy(messages)
    for message in sanitized:
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = _redact_string(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    image_url = part.get("image_url")
                    if isinstance(image_url, dict):
                        url = image_url.get("url", "")
                        if isinstance(url, str) and url.startswith("data:"):
                            image_url["url"] = "[image omitted]"
                text = part.get("text")
                if isinstance(text, str):
                    part["text"] = _redact_string(text)
    return redact_dict({"messages": sanitized})["messages"]


def debug_event(stage: str, data: dict[str, Any], elapsed_ms: float | None = None) -> str:
    """Build a redacted debug SSE JSON frame."""
    payload: dict[str, Any] = {
        "type": "debug",
        "stage": stage,
        "data": redact_dict(data),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    if elapsed_ms is not None:
        payload["elapsed_ms"] = round(elapsed_ms, 1)
    return json.dumps(payload)
