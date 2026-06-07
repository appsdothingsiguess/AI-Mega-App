"""In-memory ring buffer tracking structured data for the last N chat turns.

TurnRecord is populated incrementally by ChatOrchestrator during handle_message().
TurnTracker is stored on app.state.turn_tracker and read by /debug/* endpoints.
"""

from __future__ import annotations

import dataclasses
import threading
from collections import deque
from typing import Any


@dataclasses.dataclass
class TurnRecord:
    turn_id: str
    timestamp: str                         # ISO 8601
    project_id: str
    thread_id: str
    user_input: str                        # truncated to 500 chars
    intent: str = ""
    route_source: str = ""                 # "keyword" | "classifier" | "vision_override"
    route_confidence: float = 0.0
    model_alias: str = ""
    resolved_model: str = ""
    api_base: str | None = None
    tools_available: list[str] = dataclasses.field(default_factory=list)
    tools_invoked: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    rag_chunks_retrieved: int = 0
    rag_sources: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    llm_iterations: int = 0
    total_elapsed_ms: float = 0.0
    phase_timings: dict[str, float] = dataclasses.field(default_factory=dict)
    error: str | None = None
    token_usage: dict[str, Any] | None = None


class TurnTracker:
    """Thread-safe ring buffer of the last N TurnRecords."""

    def __init__(self, max_entries: int = 10) -> None:
        self._buffer: deque[TurnRecord] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def record(self, turn: TurnRecord) -> None:
        with self._lock:
            self._buffer.append(turn)

    def last(self) -> TurnRecord | None:
        with self._lock:
            if not self._buffer:
                return None
            return self._buffer[-1]

    def all(self) -> list[TurnRecord]:
        """Return all records, most recent first."""
        with self._lock:
            return list(reversed(self._buffer))

    def to_json(self, turn: TurnRecord) -> dict[str, Any]:
        return dataclasses.asdict(turn)
