"""Tests for prompt assembly."""

from app.chat_service import build_prompt_messages, format_retrieved_context
from app.lmstudio_client import ChatMessage


def test_build_prompt_separates_system_and_context() -> None:
    messages = build_prompt_messages(
        project_name="Sample",
        system_prompt="Be concise.",
        retrieved_chunks=[
            {
                "source_file": "guide.md",
                "text": "Use LM Studio locally.",
                "score": 1.2,
            }
        ],
        history=[
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ],
        user_message="How do I run this?",
    )
    assert messages[0].role == "system"
    assert "Be concise." in messages[0].content
    assert "Retrieved project context" in messages[0].content
    assert "guide.md" in messages[0].content
    assert messages[-1] == ChatMessage(role="user", content="How do I run this?")
    assert any(m.role == "assistant" for m in messages)


def test_format_retrieved_context_empty() -> None:
    assert format_retrieved_context([]) == ""
