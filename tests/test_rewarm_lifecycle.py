"""GPU0 rewarm activity and application lifecycle regressions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.chat.orchestrator as orchestrator_mod
import app.gpu.rewarm as rewarm_mod
import app.main as main_mod
from app.chat import history
from app.chat.orchestrator import ChatOrchestrator
from app.config import (
    Config,
    DbConfig,
    DefaultsConfig,
    GpuConfig,
    LlamaSwapConfig,
    ModelEntry,
)
from app.db import open_db
from app.gpu.rewarm import mark_gpu0_activity, start_rewarm, stop_rewarm
from tests.test_chat_sse import FakeLLMClient


def _model(name: str, gpu: int | str, file: str, *, resident: bool = False) -> ModelEntry:
    return ModelEntry(
        name=name,
        **{"class": "general"},
        ctx=4096,
        gpu=gpu,
        tool_call="native",
        max_tokens=128,
        file=file,
        quant="Q4_K_M",
        resident=resident,
        ttl_s=0 if resident else None,
    )


def _config(db_path: Path | str = ":memory:", *, threshold_min: int = 10) -> Config:
    return Config(
        llama_swap=LlamaSwapConfig(base_url="http://127.0.0.1:8080/v1"),
        db=DbConfig(path=str(db_path)),
        models=[
            _model("chat-default", 0, "/default.gguf"),
            _model("reasoner", 0, "/default.gguf"),
            _model("coder", 0, "/coder.gguf"),
            _model("dispatcher", 1, "/dispatcher.gguf", resident=True),
            _model("utility", "cpu", "/utility.gguf", resident=True),
        ],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="utility",
            title_model="dispatcher",
        ),
        gpu=GpuConfig(rewarm_default_after_min=threshold_min),
    )


async def test_rewarm_activity_requires_distinct_gpu0_server() -> None:
    app = SimpleNamespace(state=SimpleNamespace(config=_config()))
    await start_rewarm(app)
    try:
        for alias in ("chat-default", "reasoner", "dispatcher", "utility", "missing"):
            mark_gpu0_activity(alias, app.state.config)
            assert app.state._rewarm_pending_at is None

        mark_gpu0_activity("coder", app.state.config)
        assert app.state._rewarm_pending_at is not None
    finally:
        await stop_rewarm(app)


async def test_rewarm_task_is_coalesced_and_one_pending_window_pings_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = asyncio.Event()

    class RewarmLLM:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def chat(self, model, messages, **kwargs):
            from app.types import ChatDelta

            self.calls.append(model)
            completed.set()
            yield ChatDelta(content="ok")

    monkeypatch.setattr(rewarm_mod, "_REWARM_POLL_S", 0.01)
    llm = RewarmLLM()
    app = SimpleNamespace(
        state=SimpleNamespace(config=_config(threshold_min=0), llm_client=llm)
    )
    await start_rewarm(app)
    first_task = app.state._rewarm_task
    await start_rewarm(app)
    assert app.state._rewarm_task is first_task

    try:
        mark_gpu0_activity("coder", app.state.config)
        mark_gpu0_activity("coder", app.state.config)
        await asyncio.wait_for(completed.wait(), timeout=1)
        await asyncio.sleep(0.03)
        assert llm.calls == ["chat-default"]
    finally:
        await stop_rewarm(app)

    assert first_task.cancelled()
    assert app.state._rewarm_task is None


async def _run_turn(tmp_path: Path, monkeypatch, *, delay: float) -> list[tuple[str, Config]]:
    cfg = _config(tmp_path / "chat.db")
    conn = open_db(cfg.db.path)
    fake = FakeLLMClient(chunks=["one", "two"], delay_before_first=delay)
    calls: list[tuple[str, Config]] = []
    monkeypatch.setattr(
        orchestrator_mod,
        "_mark_gpu0_activity",
        lambda model, config: calls.append((model, config)),
    )
    chat_id = history.create_chat(conn, None)["id"]
    try:
        events = [
            event
            async for event in ChatOrchestrator(conn, cfg, fake).handle_message(
                chat_id, "hello", model="coder"
            )
        ]
        assert events[-1].event == "done"
    finally:
        conn.close()
    return calls


async def test_orchestrator_marks_once_after_first_substantive_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = await _run_turn(tmp_path, monkeypatch, delay=0)
    assert len(calls) == 1
    assert calls[0][0] == "coder"


async def test_slow_first_token_does_not_mark_rewarm_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator_mod, "FIRST_TOKEN_WARN_S", 0.01)
    calls = await _run_turn(tmp_path, monkeypatch, delay=0.03)
    assert calls == []


@pytest.mark.parametrize("shared_client", [True, False])
def test_lifespan_stops_tasks_then_closes_distinct_clients_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared_client: bool
) -> None:
    events: list[str] = []

    class ClosingClient:
        def __init__(self, name: str) -> None:
            self.name = name
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            events.append(f"close:{self.name}")

    async def fake_background_start(app) -> None:
        events.append("background_start")

    async def fake_background_stop(app) -> None:
        events.append("background_stop")

    async def fake_start_rewarm(app) -> None:
        events.append("rewarm_start")

    async def fake_stop_rewarm(app) -> None:
        events.append("rewarm_stop")

    async def fake_warmup_loop(app) -> None:
        events.append("warmup_start")
        try:
            await asyncio.Event().wait()
        finally:
            events.append("warmup_stop")

    monkeypatch.setattr(main_mod, "background_start", fake_background_start)
    monkeypatch.setattr(main_mod, "background_stop", fake_background_stop)
    monkeypatch.setattr(main_mod, "start_rewarm", fake_start_rewarm)
    monkeypatch.setattr(main_mod, "stop_rewarm", fake_stop_rewarm)
    monkeypatch.setattr(main_mod, "_warmup_loop", fake_warmup_loop)

    primary = ClosingClient("primary")
    summary = primary if shared_client else ClosingClient("summary")
    app = main_mod.create_app(config=_config(tmp_path / "lifespan.db"))
    app.state.llm_client = primary
    app.state.summary_llm_client = summary

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert primary.close_calls == 1
    assert summary.close_calls == 1
    assert events.index("background_stop") < events.index("warmup_stop")
    assert events.index("warmup_stop") < events.index("rewarm_stop")
    assert events.index("rewarm_stop") < events.index("close:primary")
    if not shared_client:
        assert events.count("close:summary") == 1
