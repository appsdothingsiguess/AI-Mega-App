"""Tests for app/debug_trace.py redaction helpers."""

from __future__ import annotations

import json

from app.debug_trace import debug_event, redact_dict, redact_value, sanitize_messages_for_trace

_REDACTED = "***REDACTED***"


def test_redact_api_key_in_dict() -> None:
    payload = {"api_key": "sk-secret-123", "model": "gpt-4"}
    redacted = redact_dict(payload)
    assert redacted["api_key"] == _REDACTED
    assert redacted["model"] == "gpt-4"


def test_redact_nested_secrets() -> None:
    payload = {
        "auth": {
            "token": "bearer-abc",
            "password": "hunter2",
            "opencode_api_key": "oc-secret",
        },
        "nested": [{"tavily_api_key": "tv-secret"}],
    }
    redacted = redact_dict(payload)
    assert redacted["auth"]["token"] == _REDACTED
    assert redacted["auth"]["password"] == _REDACTED
    assert redacted["auth"]["opencode_api_key"] == _REDACTED
    assert redacted["nested"][0]["tavily_api_key"] == _REDACTED


def test_redact_key_substring_match() -> None:
    payload = {"client_secret_value": "hidden", "my_api_key_backup": "also-hidden"}
    redacted = redact_dict(payload)
    assert redacted["client_secret_value"] == _REDACTED
    assert redacted["my_api_key_backup"] == _REDACTED


def test_redact_authorization_header() -> None:
    redacted = redact_dict({"authorization": "Bearer xyz"})
    assert redacted["authorization"] == _REDACTED


def test_redact_base64_blob_in_string() -> None:
    blob = "A" * 501
    assert redact_value("content", blob) == "[base64 omitted]"


def test_redact_data_url_embedded_in_text() -> None:
    text = "prefix data:image/png;base64,iVBORw0KGgoAAAANSUhEUg suffix"
    redacted = redact_value("content", text)
    assert "[base64 omitted]" in redacted
    assert "iVBORw0KGgo" not in redacted


def test_redact_string_preserves_short_strings() -> None:
    assert redact_value("content", "hello world") == "hello world"


def test_redact_list_values() -> None:
    redacted = redact_value("items", [{"api_key": "x"}, "plain"])
    assert redacted[0]["api_key"] == _REDACTED
    assert redacted[1] == "plain"


def test_debug_event_shape() -> None:
    frame = debug_event("route", {"intent": "general_chat", "confidence": 0.9})
    parsed = json.loads(frame)
    assert set(parsed.keys()) == {"type", "stage", "data"}
    assert parsed["type"] == "debug"
    assert parsed["stage"] == "route"
    assert parsed["data"]["intent"] == "general_chat"
    assert parsed["data"]["confidence"] == 0.9


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
    assert parsed["data"]["api_key"] == _REDACTED


def test_debug_event_redacts_nested_in_data() -> None:
    frame = debug_event(
        "tool_dispatch",
        {"name": "web_search", "result": {"token": "leak"}},
    )
    parsed = json.loads(frame)
    assert parsed["data"]["result"]["token"] == _REDACTED


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


def test_sanitize_messages_string_content_with_data_url() -> None:
    messages = [
        {
            "role": "user",
            "content": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
        }
    ]
    sanitized = sanitize_messages_for_trace(messages)
    assert sanitized[0]["content"] == "[image omitted]"


def test_sanitize_messages_text_part_base64() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "data:text/plain;base64,SGVsbG8=",
                }
            ],
        }
    ]
    sanitized = sanitize_messages_for_trace(messages)
    assert sanitized[0]["content"][0]["text"] == "[image omitted]"


def test_sanitize_messages_does_not_mutate_original() -> None:
    original_url = "data:image/png;base64,abc"
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": original_url},
                }
            ],
        }
    ]
    sanitize_messages_for_trace(messages)
    assert messages[0]["content"][0]["image_url"]["url"] == original_url
