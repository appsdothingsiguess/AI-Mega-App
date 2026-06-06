"""Tests for LM Studio client routing and REST payload assembly."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.chat_service import build_prompt_messages
from app.config import Settings
from app.lmstudio_client import ChatMessage, LMStudioClient


def test_multi_turn_llm_mode_uses_rest_messages_array() -> None:
    """Follow-up turns must use OpenAI messages[], not a flattened transcript."""
    settings = Settings(lmstudio_mode="llm", lmstudio_model="test-model")
    client = LMStudioClient(settings)
    captured: dict = {}

    def fake_request(
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ) -> MagicMock:
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": "assistant reply"}}]
        }
        return response

    client._request = fake_request  # type: ignore[method-assign]

    messages = build_prompt_messages(
        project_name="Demo",
        system_prompt="Be concise.",
        retrieved_chunks=[],
        history=[
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
        ],
        user_message="Follow-up question",
    )
    assert len([m for m in messages if m.role != "system"]) > 1

    result = client.chat(messages)

    assert result == "assistant reply"
    assert captured["path"] == "/v1/chat/completions"
    api_messages = captured["json"]["messages"]
    assert len(api_messages) == 4
    assert [m["role"] for m in api_messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert api_messages[-1]["content"] == "Follow-up question"
    assert "USER:" not in str(captured["json"])


def test_single_turn_llm_mode_uses_native_api() -> None:
    settings = Settings(lmstudio_mode="llm", lmstudio_model="test-model")
    client = LMStudioClient(settings)
    captured: dict = {}

    def fake_request(
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ) -> MagicMock:
        captured["path"] = path
        captured["json"] = json
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"output": [{"type": "message", "content": "ok"}]}
        return response

    client._request = fake_request  # type: ignore[method-assign]
    client.effective_chat_model = lambda **_: "test-model"  # type: ignore[method-assign]

    messages = [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="only turn"),
    ]
    client.chat(messages)

    assert captured["path"] == "/api/v1/chat"
    assert captured["json"]["input"] == "only turn"
