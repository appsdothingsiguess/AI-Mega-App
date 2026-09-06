"""Lossless chat prompt assembly and context-fit refusal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_CHARS_PER_TOKEN = 3.5

SYSTEM_MESSAGE: dict[str, str] = {
    "role": "system",
    "content": (
        "You are a helpful AI assistant. "
        "You have access to the full conversation history. "
        "Answer the user's questions directly and helpfully."
    ),
}


@dataclass(frozen=True)
class ContextAssembly:
    """A fitting wire prompt, or an explicit refusal with no messages."""

    messages: list[dict[str, str]] | None
    estimated_prompt_tokens: float
    budget_tokens: int
    trusted_covered_count: int | None

    @property
    def fits(self) -> bool:
        return self.messages is not None


def _wire_message(message: dict[str, Any]) -> dict[str, str]:
    return {
        "role": str(message.get("role") or "unknown"),
        "content": str(message.get("content") or ""),
    }


def estimate_prompt_tokens(messages: list[dict[str, str]]) -> float:
    return sum(len(message.get("content", "")) / _CHARS_PER_TOKEN for message in messages)


def assemble_context(
    history: list[dict[str, Any]],
    summary: str | None,
    trusted_covered_count: int | None,
    ctx: int,
    max_tokens: int,
) -> ContextAssembly:
    """Build an exact summary/raw partition and refuse unsafe overflow.

    Trusted coverage renders ``history[:C]`` only through the committed
    summary and every message in ``history[C:]`` verbatim.  Any missing or
    invalid coverage excludes the untrusted summary and preserves all raw
    history.  No recent-tail clipping or raw truncation occurs here.
    """
    messages = [dict(SYSTEM_MESSAGE)]
    if trusted_covered_count is not None and summary is not None:
        messages.append(
            {
                "role": "system",
                "content": f"Summary of earlier conversation:\n{summary}",
            }
        )
        raw = history[trusted_covered_count:]
    else:
        raw = history
    messages.extend(_wire_message(message) for message in raw)

    budget_tokens = max(ctx - max_tokens, 0)
    estimate = estimate_prompt_tokens(messages)
    if budget_tokens <= 0 or estimate > budget_tokens:
        return ContextAssembly(
            messages=None,
            estimated_prompt_tokens=estimate,
            budget_tokens=budget_tokens,
            trusted_covered_count=trusted_covered_count,
        )
    return ContextAssembly(
        messages=messages,
        estimated_prompt_tokens=estimate,
        budget_tokens=budget_tokens,
        trusted_covered_count=trusted_covered_count,
    )


def render_prompt(messages: list[dict[str, str]]) -> str:
    """Flatten exact wire messages for the Debug prompt tab."""
    return "\n\n".join(
        f"[{message.get('role', '?')}]\n{message.get('content', '')}"
        for message in messages
    )


__all__ = [
    "ContextAssembly",
    "SYSTEM_MESSAGE",
    "assemble_context",
    "estimate_prompt_tokens",
    "render_prompt",
]
