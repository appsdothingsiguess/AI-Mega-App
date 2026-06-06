"""Tests for app/debug_trace.py redaction helpers."""

from __future__ import annotations

import json

from app.debug_trace import debug_event, redact_dict, sanitize_messages_for_trace


def test_redact_api_key_in_dict() -> None:
    payload = {"api_key": "sk-secret-123", "model": "gpt-4"}
    redacted = redact_dict(payload)
    assert redacted["api_key"] == "***REDACTED***"
    assert redacted["model"] == "gpt-4"


def test_debug_event_never_contains_api_key() -> None:
    frame = debug_event(
        "llm_request",
        {
            "alias": "remote/deepseek-v4-pro",
            "api_key": "super-secret-key",
            "api_base": "https://example.com/v1",
        },
    )
    assert "super-secret-key" not in frame
    parsed = json.loads(frame)
    assert parsed["type"] == "debug"
    assert parsed["stage"] == "llm_request"
    assert parsed["data"]["api_key"] == "***REDACTED***"


def test_sanitize_messages_omits_image_data_urls() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe this"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg"},
                },
            ],
        }
    ]
    sanitized = sanitize_messages_for_trace(messages)
    url = sanitized[0]["content"][1]["image_url"]["url"]
    assert url == "[image omitted]"
    assert "data:image" not in json.dumps(sanitized)
