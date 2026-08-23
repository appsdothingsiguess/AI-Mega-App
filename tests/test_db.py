from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Config, DbConfig, DefaultsConfig, LlamaSwapConfig, ModelEntry
from app.db import check_connection, connect, init_db, open_db, run_sync
from app.main import create_app


def test_connect_creates_parent_dir_and_wal_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "app.db"
    conn = connect(db_path)
    try:
        assert db_path.parent.is_dir()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_init_db_creates_all_phase1_tables(tmp_path: Path) -> None:
    conn = connect(tmp_path / "app.db")
    try:
        init_db(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"chats", "messages", "traces", "spans", "settings_overlay"} <= tables
    finally:
        conn.close()


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "app.db")
    try:
        init_db(conn)
        init_db(conn)  # must not raise
    finally:
        conn.close()


def test_open_db_connects_and_initializes(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "sub" / "app.db")
    try:
        assert check_connection(conn) is True
        row = conn.execute("SELECT name FROM sqlite_master WHERE name='chats'").fetchone()
        assert row is not None
    finally:
        conn.close()


def test_check_connection_false_on_closed_connection(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "app.db")
    conn.close()
    assert check_connection(conn) is False


def test_chat_and_message_round_trip(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "app.db")
    try:
        now = int(time.time())
        conn.execute(
            "INSERT INTO chats (id, title, project_id, model_override, summary, "
            "created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, ?, ?)",
            ("chat-1", "Test chat", now, now),
        )
        conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("msg-1", "chat-1", "user", "hello", None, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", ("msg-1",)).fetchone()
        assert row["chat_id"] == "chat-1"
        assert row["content"] == "hello"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_sync_executes_blocking_call_off_loop(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "app.db")
    try:

        def fetch_one(c: sqlite3.Connection, sql: str):
            return c.execute(sql).fetchone()

        row = await run_sync(fetch_one, conn, "SELECT 1")
        assert row[0] == 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_sync_serializes_concurrent_work_for_shared_connection(
    tmp_path: Path,
) -> None:
    conn = open_db(tmp_path / "app.db")
    started = threading.Event()
    release = threading.Event()
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    def blocking_query(c: sqlite3.Connection) -> int:
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            started.set()
        release.wait(timeout=1)
        with state_lock:
            state["active"] -= 1
        return c.execute("SELECT 1").fetchone()[0]

    try:
        first = asyncio.create_task(run_sync(blocking_query, conn))
        assert await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(run_sync(blocking_query, conn))
        await asyncio.sleep(0.02)
        assert state["max_active"] == 1
        release.set()
        assert await first == 1
        assert await second == 1
        assert state["max_active"] == 1
    finally:
        release.set()
        conn.close()


def _test_config(db_path: Path) -> Config:
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
    )


def test_health_endpoint_reports_db_ok(tmp_path: Path) -> None:
    """Proves uvicorn app.main:app can start: TestClient hits GET /health and
    gets 200 with db: ok, without binding a real port."""
    cfg = _test_config(tmp_path / "health.db")
    app = create_app(config=cfg)
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
        assert "version" in body
        assert body["models"] == [{"name": "chat-default", "class": "general", "enabled": True}]
