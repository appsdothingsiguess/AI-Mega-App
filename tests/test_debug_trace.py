from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.db import open_db
from app.debug import api as debug_api
from app.debug import bus, new_trace, span
from app.debug import trace as trace_mod


@pytest.fixture()
def db_conn(tmp_path: Path):
    conn = open_db(tmp_path / "debug.db")
    trace_mod.reset_connection(conn)
    try:
        yield conn
    finally:
        trace_mod.reset_connection(None)
        conn.close()


def _spans(conn, trace_id: str) -> list:
    return conn.execute(
        "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at ASC", (trace_id,)
    ).fetchall()


def test_new_trace_persists_trace_row(db_conn) -> None:
    trace_id = new_trace(chat_id="chat-1")
    row = db_conn.execute(
        "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
    ).fetchone()
    assert row is not None
    assert row["chat_id"] == "chat-1"
    assert row["started_at"] > 0


@pytest.mark.asyncio
async def test_span_persists_row_with_correct_timing(db_conn) -> None:
    trace_id = new_trace(chat_id="chat-1")
    async with span(trace_id, "llm_request", model="chat-default") as sp:
        await asyncio.sleep(0.02)
        sp.set(tokens_in=5)

    rows = _spans(db_conn, trace_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["stage"] == "llm_request"
    assert row["started_at"] <= row["ended_at"]
    assert (row["ended_at"] - row["started_at"]) >= 0
    data = json.loads(row["data"])
    assert data["model"] == "chat-default"
    assert data["tokens_in"] == 5
    assert data["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_span_records_error_and_reraises(db_conn) -> None:
    trace_id = new_trace(chat_id="chat-1")
    with pytest.raises(ValueError):
        async with span(trace_id, "llm_request") as sp:
            sp.set(model="chat-default")
            raise ValueError("boom")

    rows = _spans(db_conn, trace_id)
    assert len(rows) == 1
    data = json.loads(rows[0]["data"])
    assert "boom" in data["error"]


@pytest.mark.asyncio
async def test_nested_spans_share_trace(db_conn) -> None:
    trace_id = new_trace(chat_id="chat-1")
    async with span(trace_id, "route") as route_sp:
        route_sp.set(source="rule")
        async with span(trace_id, "llm_request") as llm_sp:
            llm_sp.set(model="chat-default")

    rows = _spans(db_conn, trace_id)
    stages = {row["stage"] for row in rows}
    assert stages == {"route", "llm_request"}
    assert all(row["trace_id"] == trace_id for row in rows)


@pytest.mark.asyncio
async def test_failing_db_write_does_not_raise(db_conn, monkeypatch, caplog) -> None:
    def _boom(*args, **kwargs):
        raise sqlite3_error()

    def sqlite3_error():
        import sqlite3

        return sqlite3.OperationalError("disk full")

    monkeypatch.setattr(trace_mod, "_write_span_row", _boom)

    trace_id = new_trace(chat_id="chat-1")
    with caplog.at_level(logging.WARNING, logger="app.debug.trace"):
        async with span(trace_id, "llm_request") as sp:
            sp.set(model="chat-default")

    # No row was written (the write itself failed) and nothing raised.
    assert _spans(db_conn, trace_id) == []
    assert any("failed to persist span" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_store_prompts_false_filters_prompt_fields(db_conn, monkeypatch) -> None:
    class _FakeDebugCfg:
        store_prompts = False

    class _FakeCfg:
        debug = _FakeDebugCfg()

    monkeypatch.setattr(trace_mod, "get_config", lambda: _FakeCfg())

    trace_id = new_trace(chat_id="chat-1")
    async with span(trace_id, "llm_request", prompt="secret prompt text") as sp:
        sp.set(model="chat-default")

    rows = _spans(db_conn, trace_id)
    data = json.loads(rows[0]["data"])
    assert "prompt" not in data
    assert data["model"] == "chat-default"


@pytest.mark.asyncio
async def test_stream_delivers_span_published_after_subscribe(db_conn) -> None:
    # httpx's ASGITransport (as of this pin) fully drains an ASGI app before
    # returning a response, so it can't exercise a genuinely open-ended SSE
    # stream: `async with client.stream(...)` would just hang forever on our
    # never-terminating generator. Drive the ASGI 3-callable interface
    # directly instead — a real server (uvicorn) does exactly this, calling
    # app(scope, receive, send) as a long-lived task and delivering
    # `http.disconnect` via `receive()` when the client goes away, which is
    # what actually ends the endpoint's loop.
    app = FastAPI()
    app.state.db = db_conn
    app.include_router(debug_api.router)

    sent: list[dict] = []
    disconnect = asyncio.Event()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "headers": [],
        "scheme": "http",
        "path": "/api/debug/stream",
        "raw_path": b"/api/debug/stream",
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 123),
        "root_path": "",
    }

    async def receive() -> dict:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    def body_text() -> str:
        return b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        ).decode()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        for _ in range(200):
            if bus.subscriber_count() >= 1:
                break
            await asyncio.sleep(0.01)
        assert bus.subscriber_count() >= 1

        bus.publish({"type": "span", "trace_id": "t1", "stage": "llm_request"})

        for _ in range(200):
            if "event: span" in body_text():
                break
            await asyncio.sleep(0.01)
    finally:
        disconnect.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)

    text = body_text()
    assert "event: span" in text
    lines = text.splitlines()
    data_line = lines[lines.index("event: span") + 1]
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["trace_id"] == "t1"
    assert payload["stage"] == "llm_request"


def test_traces_and_trace_rest_endpoints(db_conn) -> None:
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.state.db = db_conn
    app.include_router(debug_api.router)

    trace_id = new_trace(chat_id="chat-1")

    with TestClient(app) as client:
        resp = client.get("/api/debug/traces", params={"chat_id": "chat-1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["trace_id"] == trace_id

        resp = client.get(f"/api/debug/trace/{trace_id}")
        assert resp.status_code == 200
        assert resp.json()["trace_id"] == trace_id

        resp = client.get("/api/debug/trace/does-not-exist")
        assert resp.status_code == 404


def test_trace_listing_clamps_results_and_nested_spans(db_conn) -> None:
    """The list view remains bounded even with untrusted query limits."""
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.state.db = db_conn
    app.include_router(debug_api.router)

    trace_ids = [new_trace(chat_id="chat-1") for _ in range(105)]
    # The endpoint returns the newest 100 traces.  Use the final insertion
    # so this deliberately crowded trace is definitely in that bounded set.
    crowded_trace = trace_ids[-1]
    db_conn.executemany(
        "INSERT INTO spans (trace_id, stage, started_at, ended_at, data) "
        "VALUES (?, ?, ?, ?, ?)",
        [(crowded_trace, "db", index, index, "{}") for index in range(250)],
    )
    db_conn.commit()

    with TestClient(app) as client:
        response = client.get("/api/debug/traces", params={"limit": 99999})
        assert response.status_code == 200
        traces = response.json()
        assert len(traces) == debug_api.MAX_TRACE_LIST_LIMIT
        crowded = next(trace for trace in traces if trace["trace_id"] == crowded_trace)
        assert len(crowded["spans"]) == debug_api.MAX_LIST_SPANS_PER_TRACE

        minimum = client.get("/api/debug/traces", params={"limit": -1})
        assert minimum.status_code == 200
        assert len(minimum.json()) == 1


def test_summary_status_rest_endpoint(db_conn) -> None:
    """GET /api/debug/summary-status?chat_id= wraps
    app.background.summaries.summary_status -- needs app.state.config too
    (not just db), unlike the plain trace endpoints above."""
    from fastapi.testclient import TestClient

    from app.chat import history
    from app.config import (
        BackgroundConfig,
        Config,
        DbConfig,
        DefaultsConfig,
        LlamaSwapConfig,
        ModelEntry,
    )

    chat_model = ModelEntry(
        name="chat-default", **{"class": "general"}, ctx=4096, gpu=0,
        tool_call="native", max_tokens=1024,
        file="/models/chat-default.gguf", quant="Q4_K_M",
    )
    config = Config(
        llama_swap=LlamaSwapConfig(base_url="http://fake/v1/", timeout_s=5.0),
        db=DbConfig(path=":memory:"),
        models=[chat_model],
        defaults=DefaultsConfig(
            chat_model="chat-default", utility_model="utility", title_model="dispatcher"
        ),
        background=BackgroundConfig(title_model="dispatcher", summary_model="utility"),
    )

    app = FastAPI()
    app.state.db = db_conn
    app.state.config = config
    app.include_router(debug_api.router)

    chat_id = history.create_chat(db_conn)["id"]
    history.insert_message(db_conn, chat_id, "user", "hi", None)
    history.insert_message(db_conn, chat_id, "assistant", "hello", "chat-default")

    with TestClient(app) as client:
        resp = client.get("/api/debug/summary-status", params={"chat_id": chat_id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "turn_count_fallback"
        assert body["will_trigger"] is False
        assert body["last_summary"] is None
        assert body["in_flight"] is False
        assert body["coverage"] == {
            "trusted": False,
            "covered_message_count": None,
            "reason": "no_summary",
        }
