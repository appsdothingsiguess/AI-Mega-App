"""Lossless summary coverage and safe context-fit regressions."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.background.summaries import _run_summary
from app.background.summary_coverage import coverage_fields, trusted_covered_count
from app.chat import history
from app.chat.context import assemble_context
from app.chat.orchestrator import ChatOrchestrator
from app.config import (
    BackgroundConfig,
    Config,
    DbConfig,
    DefaultsConfig,
    LlamaSwapConfig,
    ModelEntry,
)
from app.db import open_db
from app.debug import trace as trace_mod
from app.llm_client import LLMError
from app.types import ChatDelta


class RecordingLLM:
    def __init__(
        self,
        chunks: list[ChatDelta] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.chunks = chunks or [ChatDelta(content="ok", finish_reason="stop")]
        self.error = error
        self.calls: list[dict] = []

    async def chat(self, model: str, messages: list[dict[str, str]], **kwargs) -> AsyncIterator[ChatDelta]:
        self.calls.append({"model": model, "messages": messages, **kwargs})
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk

    async def model_status(self) -> dict[str, bool]:
        return {}


def _config(db_path: Path, *, ctx: int = 4096, max_tokens: int = 256) -> Config:
    chat_model = ModelEntry(
        name="chat-default",
        **{"class": "general"},
        ctx=ctx,
        gpu=0,
        tool_call="native",
        max_tokens=max_tokens,
        file="/models/chat.gguf",
        quant="Q4_K_M",
    )
    utility_model = ModelEntry(
        name="utility",
        **{"class": "utility"},
        ctx=4096,
        gpu="cpu",
        tool_call="none",
        max_tokens=128,
        file="/models/utility.gguf",
        quant="Q4_K_M",
    )
    return Config(
        llama_swap=LlamaSwapConfig(base_url="http://unused/v1"),
        db=DbConfig(path=str(db_path)),
        models=[chat_model, utility_model],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="utility",
            title_model="utility",
        ),
        background=BackgroundConfig(
            title_model="utility",
            summary_model="utility",
            summary_every_n_turns=1,
        ),
    )


@pytest.fixture
def db(tmp_path: Path):
    conn = open_db(tmp_path / "context.db")
    trace_mod.reset_connection(conn)
    try:
        yield conn
    finally:
        trace_mod.reset_connection(None)
        conn.close()


def _messages(count: int) -> list[dict]:
    return [
        {
            "id": f"id-{index:02d}",
            "role": "user" if index % 2 else "assistant",
            "content": f"sentinel-m{index:02d}",
            "model": None,
            "created_at": index,
        }
        for index in range(1, count + 1)
    ]


def _write_summary_span(conn, chat_id: str, data: dict, offset: int = 0) -> None:
    trace_id = uuid.uuid4().hex
    now_ms = int(time.time() * 1000) + offset
    conn.execute(
        "INSERT INTO traces (trace_id, chat_id, started_at) VALUES (?, ?, ?)",
        (trace_id, chat_id, now_ms),
    )
    conn.execute(
        "INSERT INTO spans (trace_id, stage, started_at, ended_at, data) "
        "VALUES (?, 'summary', ?, ?, ?)",
        (trace_id, now_ms, now_ms, json.dumps(data)),
    )
    conn.commit()


def test_coverage_25_preserves_every_message_26_through_35_once() -> None:
    messages = _messages(35)
    result = assemble_context(messages, "trusted-prefix-summary", 25, 8192, 256)

    assert result.fits
    assert result.messages is not None
    assert result.messages[1]["content"].endswith("trusted-prefix-summary")
    raw = [message["content"] for message in result.messages[2:]]
    assert raw == [f"sentinel-m{index:02d}" for index in range(26, 36)]
    assert len(raw) == len(set(raw)) == 10


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(covered_message_count=-1),
        lambda data: data.update(covered_message_count=36),
        lambda data: data.update(covered_prefix_sha256="mismatch"),
        lambda data: data.update(summary_sha256="mismatch"),
        lambda data: data.pop("covered_prefix_sha256"),
    ],
)
def test_invalid_coverage_falls_back_to_all_raw_history(db, mutate) -> None:
    chat_id = history.create_chat(db)["id"]
    messages = _messages(35)
    summary = "committed summary"
    data = coverage_fields(messages, 25, summary)
    mutate(data)
    _write_summary_span(db, chat_id, data)

    assert trusted_covered_count(db, chat_id, messages, summary) is None
    result = assemble_context(messages, summary, None, 8192, 256)
    assert result.messages is not None
    assert [message["content"] for message in result.messages[1:]] == [
        f"sentinel-m{index:02d}" for index in range(1, 36)
    ]
    assert all("committed summary" not in message["content"] for message in result.messages)


@pytest.mark.asyncio
async def test_successful_summary_commits_matching_fingerprints(db, tmp_path: Path) -> None:
    chat_id = history.create_chat(db)["id"]
    history.insert_message(db, chat_id, "user", "first question", None)
    history.insert_message(db, chat_id, "assistant", "first answer", "chat-default")
    llm = RecordingLLM([ChatDelta(content="landed summary", finish_reason="stop")])
    app = SimpleNamespace(
        state=SimpleNamespace(config=_config(tmp_path / "unused.db"), db=db, summary_llm_client=llm)
    )

    await _run_summary(app, chat_id)

    messages = history.list_messages(db, chat_id)
    committed = history.get_chat(db, chat_id)["summary"]
    assert committed == "landed summary"
    assert trusted_covered_count(db, chat_id, messages, committed) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "error"),
    [
        ([ChatDelta(content="cut off", finish_reason="length")], None),
        ([], LLMError("http_error", "summary failed")),
    ],
)
async def test_partial_or_failed_summary_never_advances_coverage(
    db, tmp_path: Path, chunks, error
) -> None:
    chat_id = history.create_chat(db)["id"]
    history.insert_message(db, chat_id, "user", "must remain raw", None)
    llm = RecordingLLM(chunks, error)
    app = SimpleNamespace(
        state=SimpleNamespace(config=_config(tmp_path / "unused.db"), db=db, summary_llm_client=llm)
    )

    with pytest.raises((LLMError, RuntimeError)):
        await _run_summary(app, chat_id)

    messages = history.list_messages(db, chat_id)
    assert history.get_chat(db, chat_id)["summary"] is None
    assert trusted_covered_count(db, chat_id, messages, None) is None


@pytest.mark.asyncio
async def test_safe_fit_refusal_queues_recovery_without_chat_model_call(
    db, tmp_path: Path, monkeypatch
) -> None:
    import app.chat.orchestrator as orchestrator_mod

    chat_id = history.create_chat(db)["id"]
    history.insert_message(db, chat_id, "user", "oversized " * 100, None)
    llm = RecordingLLM()
    queued: list[str] = []

    async def capture_recovery(value: str) -> bool:
        queued.append(value)
        return True

    async def ignore_turn_complete(value: str) -> None:
        return None

    monkeypatch.setattr(orchestrator_mod, "_enqueue_summary_recovery", capture_recovery)
    monkeypatch.setattr(orchestrator_mod, "_on_turn_complete", ignore_turn_complete)
    orchestrator = ChatOrchestrator(db, _config(tmp_path / "unused.db", ctx=128, max_tokens=64), llm)

    events = [event async for event in orchestrator.handle_message(chat_id, "new sentinel", "chat-default")]

    assert [event.event for event in events] == ["error"]
    assert events[0].data["kind"] == "context_overflow"
    assert llm.calls == []
    assert queued == [chat_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, LLMError("connection", "down")])
async def test_fitting_prompt_preserves_done_and_error_behavior(
    db, tmp_path: Path, monkeypatch, error
) -> None:
    import app.chat.orchestrator as orchestrator_mod

    async def ignore_turn_complete(value: str) -> None:
        return None

    monkeypatch.setattr(orchestrator_mod, "_on_turn_complete", ignore_turn_complete)
    chat_id = history.create_chat(db)["id"]
    llm = RecordingLLM(error=error)
    orchestrator = ChatOrchestrator(db, _config(tmp_path / "unused.db"), llm)

    events = [event async for event in orchestrator.handle_message(chat_id, "fits", "chat-default")]

    assert [event.event for event in events] == (["token", "done"] if error is None else ["error"])
    assert len(llm.calls) == 1
