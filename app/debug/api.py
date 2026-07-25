"""Debug REST + SSE tap (docs/FEATURES.md A3, PLAN.md §4.16). The standalone
`#/debug` frontend view consumes these endpoints verbatim. No server-side
rendering here — pure JSON/SSE.

Exposes `router = APIRouter()` for app/main.py to `include_router()` (wired
by the p1/chat-sse agent this wave, per FILE SCOPE/NON-GOALS — this module
does not touch app/main.py itself).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.db import run_sync
from app.debug import bus

router = APIRouter(prefix="/api/debug", tags=["debug"])

HEARTBEAT_INTERVAL_S = 15


def _conn(request: Request) -> sqlite3.Connection:
    """The app-wide connection main.py opens at startup (app.state.db) —
    same pattern as /health in app/main.py. WAL mode makes sharing it with
    app/debug/trace.py's own connection safe."""
    return request.app.state.db


def _row_to_span(row: sqlite3.Row) -> dict[str, Any]:
    data = json.loads(row["data"]) if row["data"] else {}
    return {
        "id": row["id"],
        "trace_id": row["trace_id"],
        "stage": row["stage"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "data": data,
    }


def _spans_for(conn: sqlite3.Connection, trace_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at ASC",
        (trace_id,),
    ).fetchall()
    return [_row_to_span(r) for r in rows]


def _fetch_traces(
    conn: sqlite3.Connection, chat_id: str | None, limit: int
) -> list[dict[str, Any]]:
    if chat_id:
        rows = conn.execute(
            "SELECT * FROM traces WHERE chat_id = ? ORDER BY started_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM traces ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {
            "trace_id": row["trace_id"],
            "chat_id": row["chat_id"],
            "started_at": row["started_at"],
            "spans": _spans_for(conn, row["trace_id"]),
        }
        for row in rows
    ]


@router.get("/traces")
async def list_traces(
    request: Request, chat_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Recent traces, most recent first, each with its spans nested."""
    return await run_sync(_fetch_traces, _conn(request), chat_id, limit)


def _fetch_trace(conn: sqlite3.Connection, trace_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "trace_id": row["trace_id"],
        "chat_id": row["chat_id"],
        "started_at": row["started_at"],
        "spans": _spans_for(conn, trace_id),
    }


@router.get("/trace/{trace_id}")
async def get_trace(request: Request, trace_id: str) -> dict[str, Any]:
    """Full waterfall JSON for one trace: ordered spans with timing/data."""
    result = await run_sync(_fetch_trace, _conn(request), trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return result


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """SSE tap: every finished span as event `span` (JSON span row), plus a
    `heartbeat` event at least every 15s so clients/proxies don't time the
    connection out during quiet periods."""

    async def event_source():
        async with bus.subscribe() as queue:
            # Flush an immediate comment so the response starts streaming
            # right away (some clients/proxies wait on the first byte
            # before considering the connection open) rather than sitting
            # silent until the first real span or the first heartbeat.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_INTERVAL_S
                    )
                except TimeoutError:
                    yield "event: heartbeat\ndata: {}\n\n"
                    continue
                payload = json.dumps(event, separators=(",", ":"), default=str)
                yield f"event: span\ndata: {payload}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
