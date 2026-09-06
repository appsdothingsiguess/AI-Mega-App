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

from app.background.summaries import summary_status
from app.db import run_sync
from app.debug import bus

router = APIRouter(prefix="/api/debug", tags=["debug"])

HEARTBEAT_INTERVAL_S = 15
MAX_TRACE_LIST_LIMIT = 100
MAX_LIST_SPANS_PER_TRACE = 200


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


def _spans_for(
    conn: sqlite3.Connection, trace_id: str, limit: int | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at ASC"
    params: tuple[Any, ...] = (trace_id,)
    if limit is not None:
        query += " LIMIT ?"
        params += (limit,)
    rows = conn.execute(query, params).fetchall()
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
            "spans": _spans_for(conn, row["trace_id"], MAX_LIST_SPANS_PER_TRACE),
        }
        for row in rows
    ]


@router.get("/traces")
async def list_traces(
    request: Request, chat_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Recent traces, most recent first, each with its spans nested."""
    safe_limit = max(1, min(limit, MAX_TRACE_LIST_LIMIT))
    return await run_sync(_fetch_traces, _conn(request), chat_id, safe_limit)


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


@router.get("/summary-status")
async def get_summary_status(request: Request, chat_id: str) -> dict[str, Any]:
    """Rolling-summary trigger snapshot for one chat (docs/FEATURES.md F19):
    current token pressure vs. the trigger threshold, whether the next turn
    would enqueue a regen, and what the last regen actually did (device,
    coverage). Read-only -- computed from the same logic
    maybe_enqueue_summary acts on (app/background/summaries.py:_trigger_state),
    never a duplicated/divergent copy."""
    return await summary_status(request.app, chat_id)


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """SSE tap: every finished span as event `span` (JSON span row), plus a
    `heartbeat` event at least every 15s so clients/proxies don't time the
    connection out during quiet periods."""

    async def event_source():
        # Poll disconnect while waiting on the queue so a client abort
        # (pagehide / tab close) ends the generator within ~0.5s instead of
        # blocking up to HEARTBEAT_INTERVAL_S — otherwise uvicorn graceful
        # shutdown hangs on the open SSE connection.
        poll_s = 0.5

        async def _next_event(queue: asyncio.Queue[Any]) -> Any | None:
            """Return a queued event, None on client disconnect, or raise
            TimeoutError after HEARTBEAT_INTERVAL_S with no event."""
            waited = 0.0
            while waited < HEARTBEAT_INTERVAL_S:
                if await request.is_disconnected():
                    return None
                try:
                    return await asyncio.wait_for(queue.get(), timeout=poll_s)
                except TimeoutError:
                    waited += poll_s
            raise TimeoutError

        try:
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
                        event = await _next_event(queue)
                    except TimeoutError:
                        yield "event: heartbeat\ndata: {}\n\n"
                        continue
                    except asyncio.CancelledError:
                        break
                    if event is None:
                        break
                    payload = json.dumps(event, separators=(",", ":"), default=str)
                    yield f"event: span\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(event_source(), media_type="text/event-stream")
