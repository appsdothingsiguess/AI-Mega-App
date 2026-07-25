"""Fallback stand-in for app/debug (owned by the p1/debug-trace sub-agent,
not yet merged into this worktree/branch). Matches the *declared* interface
from docs/FEATURES.md A3 exactly:

    new_trace(chat_id) -> trace_id
    async with span(trace_id, stage, **fields) as sp: sp.set(...)

so app/chat/orchestrator.py can write real spans/traces rows and this
wave's tests (including the `/api/debug/trace/{id}` wiring-proof test) can
run standalone. `debug_router` below is a matching minimal stand-in for
`app.debug.api.router`'s `GET /api/debug/trace/{trace_id}` endpoint.

INTEGRATOR NOTE: this whole module is scaffolding, not a feature. Once
p1/debug-trace's app/debug package is merged, app/chat/orchestrator.py's
`except ImportError` branch and this file's use from app/chat/api.py and
app/main.py should be deleted in favor of the real package — do not extend
this module with new behavior. It is intentionally outside this wave's
listed FILE SCOPE (app/chat/__init__.py, orchestrator.py, api.py,
history.py, app/main.py, tests/**); it exists only because the acceptance
criterion (spans visible via GET /api/debug/trace/{id}) is otherwise
untestable before the sibling branch merges. Flagged for the integrator.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Request

_conn: sqlite3.Connection | None = None


def bind_connection(conn: sqlite3.Connection) -> None:
    """Wired once at app startup (app/main.py) so new_trace/span can write
    to the same SQLite connection the rest of the app uses."""
    global _conn
    _conn = conn


def new_trace(chat_id: str | None) -> str:
    trace_id = uuid.uuid4().hex
    if _conn is not None:
        _conn.execute(
            "INSERT INTO traces (trace_id, chat_id, started_at) VALUES (?, ?, ?)",
            (trace_id, chat_id, int(time.time() * 1000)),
        )
        _conn.commit()
    return trace_id


class _SpanHandle:
    def __init__(self, fields: dict[str, Any]) -> None:
        self._fields = fields

    def set(self, **fields: Any) -> None:
        self._fields.update(fields)


@asynccontextmanager
async def span(trace_id: str, stage: str, **fields: Any) -> AsyncIterator[_SpanHandle]:
    """Records start/end ms and any fields set via `sp.set(...)`. A failing
    write never raises into the caller (chat > observability) and an
    exception raised inside the span is recorded then re-raised untouched."""
    handle = _SpanHandle(dict(fields))
    started_at = int(time.time() * 1000)
    error: str | None = None
    try:
        yield handle
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        ended_at = int(time.time() * 1000)
        if error is not None:
            handle._fields["error"] = error
        if _conn is not None:
            try:
                _conn.execute(
                    "INSERT INTO spans (trace_id, stage, started_at, ended_at, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (trace_id, stage, started_at, ended_at, json.dumps(handle._fields)),
                )
                _conn.commit()
            except sqlite3.Error:
                pass  # trace-write failure must never fail the turn


debug_router = APIRouter(prefix="/api/debug", tags=["debug-fallback"])


@debug_router.get("/trace/{trace_id}")
def get_trace(trace_id: str, request: Request) -> dict[str, Any]:
    conn: sqlite3.Connection = request.app.state.db
    trace = conn.execute(
        "SELECT trace_id, chat_id, started_at FROM traces WHERE trace_id = ?", (trace_id,)
    ).fetchone()
    spans = conn.execute(
        "SELECT stage, started_at, ended_at, data FROM spans WHERE trace_id = ? "
        "ORDER BY id ASC",
        (trace_id,),
    ).fetchall()
    return {
        "trace_id": trace_id,
        "chat_id": trace["chat_id"] if trace else None,
        "started_at": trace["started_at"] if trace else None,
        "spans": [
            {
                "stage": s["stage"],
                "started_at": s["started_at"],
                "ended_at": s["ended_at"],
                "data": json.loads(s["data"]) if s["data"] else {},
            }
            for s in spans
        ],
    }
