"""Shared data types for Prompter X service contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass
class SearchResult:
    """Shared result type for SearchService and VectorStore."""

    text: str
    source: str
    title: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassifierOutput:
    intent: str
    tools: list[str]
    confidence: float


class RouteSource(StrEnum):
    KEYWORD = "keyword"
    CLASSIFIER = "classifier"
    VISION_OVERRIDE = "vision_override"


@dataclass
class RouteResult:
    intent: str
    tools: list[str]
    confidence: float
    source: RouteSource


@dataclass
class ToolCallDelta:
    """Accumulates streaming tool_call deltas."""

    id: str = ""
    name: str = ""
    arguments: str = ""

    def to_openai_format(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }
