"""Shared types and service Protocols — the one frozen-contract module other
Phase-1 agents import from (PLAN.md §3.1, §4.2; CLAUDE.md "Frozen contracts").

Model and provider names are never Python string literals here or anywhere
else — they only ever arrive as values sourced from config.yaml at runtime.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

# Type aliases used across the debug tracing and chat modules.
TraceId = str
# A finished span row as it is stored/published — see app/debug/trace.py
# (owned by the p1/debug-trace agent) for the writer. This alias exists so
# other modules can type-hint "a span dict" without importing that package.
Span = dict[str, Any]


class ToolCallDelta(BaseModel):
    """One tool-call fragment from a streamed completion, accumulated by
    index as llama.cpp emits partial `arguments` chunks."""

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str | None = None


class Usage(BaseModel):
    """Real token counts from llama.cpp's own `usage` field — never a
    client-side estimate (PLAN.md §4.16)."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatDelta(BaseModel):
    """One increment of a streamed chat completion, as produced by
    app/llm_client.py's LLMClient.chat()."""

    content: str | None = None
    tool_calls: list[ToolCallDelta] | None = None
    finish_reason: str | None = None
    usage: Usage | None = None
    # llama.cpp's own `timings` block (prompt_per_second,
    # predicted_per_second, ...) as sent verbatim on the final chunk.
    # Additive/optional: the Debug view's tok/s must come from the server,
    # never a client-side estimate (PLAN.md §4.16, docs/FEATURES.md F19).
    timings: dict[str, Any] | None = None


class SSEEvent(BaseModel):
    """A single Server-Sent Event. `event` is one of the frozen vocabulary
    in PLAN.md §4.2 (`token`, `model_loading`, `tool_start`, `tool_result`,
    `title`, `done`, `error`, plus non-chat relays' own payload names) —
    `done`/`error` are the only terminal events, exactly one per stream."""

    event: str
    data: dict[str, Any]

    def encode(self) -> str:
        """Render as wire-format SSE text: `event: <name>\\ndata: <json>\\n\\n`."""
        payload = json.dumps(self.data, separators=(",", ":"))
        return f"event: {self.event}\ndata: {payload}\n\n"


class RouteResult(BaseModel):
    """Router decision — stub for Phase 2's router; the field shape is
    frozen now so the chat orchestrator's resolution seam doesn't change
    shape when routing lands (PLAN.md §4.3)."""

    model: str
    source: Literal["override", "rule", "classifier"]
    intent: str
    latency_ms: float
    confidence: float | None = None
