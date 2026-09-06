"""Background queue titles + summaries (PLAN.md §4.15, FEATURES F18)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.background import on_turn_complete, start, stop
from app.background.queue import get_queue
from app.background.summaries import summary_status
from app.background.titles import clean_title, is_echo
from app.chat import history
from app.config import (
    BackgroundConfig,
    Config,
    DbConfig,
    DefaultsConfig,
    LlamaSwapConfig,
    ModelEntry,
    RoutingConfig,
    RoutingIntents,
)
from app.db import open_db
from app.debug import trace as trace_mod
from app.llm_client import LLMClient
from tests.fakes import FakeLlamaSwap

BASE_URL = "http://fake-llama-swap/v1/"

TEST_MODEL = ModelEntry(
    name="chat-default",
    **{"class": "general"},
    ctx=4096,
    gpu=0,
    tool_call="native",
    max_tokens=1024,
    file="/models/chat-default.gguf",
    quant="Q4_K_M",
)

TEST_UTILITY_MODEL = ModelEntry(
    name="utility",
    **{"class": "utility"},
    ctx=4096,
    gpu="cpu",
    tool_call="none",
    max_tokens=512,
    file="/models/utility.gguf",
    quant="Q4_K_M",
)


def make_llm(fake: FakeLlamaSwap, timeout_s: float = 5.0) -> LLMClient:
    client = LLMClient(base_url=BASE_URL, timeout_s=timeout_s)
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app),
        base_url=BASE_URL,
        timeout=timeout_s,
    )
    return client


def make_config(*, summary_every_n_turns: int = 6) -> Config:
    return Config(
        llama_swap=LlamaSwapConfig(base_url=BASE_URL, timeout_s=5.0),
        db=DbConfig(path=":memory:"),
        models=[TEST_MODEL, TEST_UTILITY_MODEL],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="utility",
            title_model="dispatcher",
        ),
        background=BackgroundConfig(
            title_model="dispatcher",
            summary_model="utility",
            summary_every_n_turns=summary_every_n_turns,
        ),
    )


def seed_exchange(conn, chat_id: str, user: str, asst: str) -> None:
    history.insert_message(conn, chat_id, "user", user, None)
    history.insert_message(conn, chat_id, "assistant", asst, "chat-default")


@pytest.fixture
async def bg_app(tmp_path: Path):
    """SimpleNamespace app with config/db/llm + started background queue."""
    fake = FakeLlamaSwap()
    conn = open_db(tmp_path / "bg.db")
    trace_mod.reset_connection(conn)
    config = make_config()
    llm = make_llm(fake)
    app = SimpleNamespace(
        state=SimpleNamespace(config=config, db=conn, llm_client=llm, summary_llm_client=llm)
    )
    await start(app)
    try:
        yield app, fake, conn
    finally:
        await stop(app)
        await llm.close()
        trace_mod.reset_connection(None)
        conn.close()


def seed_llm_stream_usage(
    conn, chat_id: str, prompt_tokens: int, model: str | None = None
) -> None:
    """Insert a trace + llm_stream span carrying real usage.prompt_tokens
    (+ optionally the model that turn ran on), as the orchestrator would
    after a real chat turn -- this is the signal maybe_enqueue_summary
    reads to trigger on token pressure."""
    trace_id = f"trace-{chat_id}-{uuid.uuid4().hex}"
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO traces (trace_id, chat_id, started_at) VALUES (?, ?, ?)",
        (trace_id, chat_id, now_ms),
    )
    payload: dict = {"usage": {"prompt_tokens": prompt_tokens}}
    if model is not None:
        payload["model"] = model
    data = json.dumps(payload)
    conn.execute(
        "INSERT INTO spans (trace_id, stage, started_at, ended_at, data) "
        "VALUES (?, 'llm_stream', ?, ?, ?)",
        (trace_id, now_ms, now_ms, data),
    )
    conn.commit()
