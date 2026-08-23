"""Tests for the Phase-1 chat orchestrator + SSE endpoint (docs/FEATURES.md
A4; PLAN.md §4.2).

app/llm_client.py (p1/llm-client) is not yet merged into this worktree, so
`FakeLLMClient` below is the smallest stand-in matching its *declared*
signature (`async def chat(model, messages, *, tools=None,
response_format=None, thinking=None, max_tokens=None, stream=True) ->
AsyncIterator[ChatDelta]`, raising `LLMError(kind, detail)` on failure).
INTEGRATOR NOTE: llm-client's real `tests/fakes/fake_llama_swap.py` will
likely supersede this fake — reconcile at integration; this fake exists
only so this wave's tests don't depend on unmerged work.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from app.background.summary_coverage import coverage_fields
from app.chat import history
from app.chat.orchestrator import LLMError
from app.config import (
    Config,
    DbConfig,
    DefaultsConfig,
    LlamaSwapConfig,
    LlmConfig,
    ModelEntry,
)
from app.main import create_app
from app.types import ChatDelta, Usage

GOLDEN_DIR = Path(__file__).parent / "golden"


class FakeLLMClient:
    """Matches LLMClient's declared `chat()` signature exactly. Yields a
    fixed set of content chunks, optionally delayed, optionally raising.

    `raise_error` is raised after `delay_before_first` (if any), so tests
    can reproduce the race where the connection fails after the model_loading
    warn window has already fired.
    """

    def __init__(
        self,
        chunks: list[str] | None = None,
        reasoning_chunks: list[str] | None = None,
        usage: Usage | None = None,
        delay_before_first: float = 0.0,
        raise_error: Exception | None = None,
        timings: dict | None = None,
        model_status: dict[str, bool] | None = None,
    ) -> None:
        self.chunks = chunks if chunks is not None else ["Hello", ", ", "world!"]
        self.reasoning_chunks = reasoning_chunks or []
        self.usage = usage
        self.delay_before_first = delay_before_first
        self.raise_error = raise_error
        self.timings = timings
        self._model_status = model_status or {}
        self.seen_messages: list[dict[str, str]] | None = None
        self.last_model: str | None = None
        self.all_models: list[str] = []

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        tools=None,
        response_format=None,
        thinking: bool | None = None,
        max_tokens: int | None = None,
        stream: bool = True,
    ) -> AsyncIterator[ChatDelta]:
        import asyncio

        self.seen_messages = messages
        self.last_model = model
        self.all_models.append(model)
        if self.delay_before_first:
            await asyncio.sleep(self.delay_before_first)
        if self.raise_error is not None:
            raise self.raise_error
        for piece in self.reasoning_chunks:
            yield ChatDelta(reasoning_content=piece)
        for i, chunk in enumerate(self.chunks):
            is_last = i == len(self.chunks) - 1
            yield ChatDelta(
                content=chunk,
                finish_reason="stop" if is_last else None,
                usage=self.usage if is_last else None,
                timings=self.timings if is_last else None,
            )

    async def model_status(self) -> dict[str, bool]:
        return dict(self._model_status)


def _test_config(db_path: Path, first_token_timeout_s: float = 30) -> Config:
    return Config(
        llama_swap=LlamaSwapConfig(base_url="http://127.0.0.1:8080/v1"),
        db=DbConfig(path=str(db_path)),
        models=[
            ModelEntry(
                name="chat-default",
                **{"class": "general"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/models/chat-default.gguf",
                quant="Q4_K_M",
            )
        ],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="chat-default",
            title_model="chat-default",
        ),
        llm=LlmConfig(first_token_timeout_s=first_token_timeout_s),
    )


def _make_client(tmp_path: Path, llm_client, first_token_timeout_s: float = 30) -> TestClient:
    cfg = _test_config(tmp_path / "app.db", first_token_timeout_s)
    app = create_app(config=cfg)
    app.state.llm_client = llm_client
    client = TestClient(app)
    client.__enter__()
    return client


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event_line = next(line for line in lines if line.startswith("event: "))
        data_line = next(line for line in lines if line.startswith("data: "))
        events.append((event_line[len("event: ") :], json.loads(data_line[len("data: ") :])))
    return events

