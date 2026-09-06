"""The span API every Phase-1+ pipeline stage calls through (PLAN.md §4.16,
docs/FEATURES.md A3). This is critical infrastructure built FIRST: every
later feature (routing, RAG, tools, ...) wraps its work in `span()` so a
turn is fully traced from day one.

Public API (frozen shape — see docs/PHASE_PROMPTS.md "p1/debug-trace"):

    new_trace(chat_id: str | None = None) -> TraceId
    async with span(trace_id, stage, **fields) as sp:
        sp.set(more="fields")   # add/override data mid-span

On exit, `span()` writes one row to the `spans` table (stage, started_at,
ended_at, data JSON) and publishes the same row to `app/debug/bus.py` for
live SSE taps. Nothing here ever raises out of the debug path into the
caller: a DB failure (or bus failure) logs a warning and moves on, because
tracing must never be the reason a chat turn breaks.

Phase-1 stage-name vocabulary (free-form strings, not an enum — this is
just the vocabulary other Phase-1 agents are expected to use so waterfalls
read consistently):

    route        — router decision (Phase 2; chat-sse leaves the seam)
    llm_request  — one request to app/llm_client.py
    llm_stream   — the streamed-token phase of a request
    sse_emit     — a server-sent event written to the client
    swap_wait    — time spent waiting on llama-swap to load/switch a model
    db           — a storage operation worth tracing (e.g. slow write)

Additional stages (rag, tool, dispatcher, browser, ...) are added by later
features using the same free-form convention; this module does not enumerate
or validate stage names.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT, get_config
from app.db import connect, run_sync
from app.debug import bus
from app.types import TraceId

logger = logging.getLogger("app.debug.trace")

# Fields dropped from persisted/published span data unless
# config.debug.store_prompts is true — the full-prompt/response toggle
# (docs/FEATURES.md A3, PLAN.md §4.16).
_PROMPT_FIELDS = {"prompt", "response", "raw_prompt", "raw_response", "messages"}

_conn: sqlite3.Connection | None = None


def _resolve_db_path() -> Path:
    cfg = get_config()
    db_path = Path(cfg.db.path)
    return db_path if db_path.is_absolute() else REPO_ROOT / db_path


def _connection() -> sqlite3.Connection:
    """Lazily-opened connection to the same SQLite file as the rest of the
    app (WAL mode makes a second connection from this module safe). Kept
    separate from `app.state.db` because this module exposes free functions
    with no request/app object to hang a connection off of."""
    global _conn
    if _conn is None:
        _conn = connect(_resolve_db_path())
    return _conn


def reset_connection(conn: sqlite3.Connection | None = None) -> None:
    """Test hook: force the next _connection() call to open a fresh
    connection (or install `conn` directly, e.g. an in-memory test DB).
    Production code never calls this."""
    global _conn
    _conn = conn


def new_trace(chat_id: str | None = None) -> TraceId:
    """Start a new trace for one chat turn (or any traced unit of work) and
    return its id. Writes the `traces` row fire-and-forget: a failure here
    is logged and swallowed, never raised, so a tracing outage can't break
    the chat path. Callers get a usable trace_id even if persistence
    failed."""
    trace_id = str(uuid.uuid4())
    started_at = int(time.time() * 1000)
    try:
        conn = _connection()
        conn.execute(
            "INSERT INTO traces (trace_id, chat_id, started_at) VALUES (?, ?, ?)",
            (trace_id, chat_id, started_at),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - fire-and-forget by design
        logger.warning("debug: failed to persist trace %s", trace_id, exc_info=True)
    return trace_id


class SpanHandle:
    """Mutable record for one in-flight span. `set(**fields)` merges more
    data in at any point before the `span()` block exits."""

    def __init__(self, trace_id: TraceId, stage: str, fields: dict[str, Any]) -> None:
        self.trace_id = trace_id
        self.stage = stage
        self.fields: dict[str, Any] = dict(fields)
        self.started_at_ms = int(time.time() * 1000)

    def set(self, **fields: Any) -> None:
        self.fields.update(fields)


def _filtered_data(fields: dict[str, Any]) -> dict[str, Any]:
    if get_config().debug.store_prompts:
        return dict(fields)
    return {k: v for k, v in fields.items() if k not in _PROMPT_FIELDS}


def _write_span_row(
    conn: sqlite3.Connection,
    trace_id: str,
    stage: str,
    started_at: int,
    ended_at: int,
    data_json: str,
) -> int:
    cursor = conn.execute(
        "INSERT INTO spans (trace_id, stage, started_at, ended_at, data) "
        "VALUES (?, ?, ?, ?, ?)",
        (trace_id, stage, started_at, ended_at, data_json),
    )
    conn.commit()
    return int(cursor.lastrowid)


async def _persist_and_publish(
    handle: SpanHandle, started_at: int, ended_at: int
) -> None:
    data = _filtered_data(handle.fields)
    data.setdefault("duration_ms", ended_at - started_at)
    row: dict[str, Any] = {
        "id": None,
        "trace_id": handle.trace_id,
        "stage": handle.stage,
        "started_at": started_at,
        "ended_at": ended_at,
        "data": data,
    }
    try:
        data_json = json.dumps(data, default=str)
        conn = _connection()
        row_id = await run_sync(
            _write_span_row, conn, handle.trace_id, handle.stage, started_at, ended_at, data_json
        )
        row["id"] = row_id
    except Exception:  # noqa: BLE001 - fire-and-forget by design
        logger.warning(
            "debug: failed to persist span (stage=%s trace=%s)",
            handle.stage,
            handle.trace_id,
            exc_info=True,
        )
    try:
        bus.publish({"type": "span", **row})
    except Exception:  # noqa: BLE001 - the tap must never break tracing either
        logger.warning("debug: failed to publish span to bus", exc_info=True)


@asynccontextmanager
async def span(trace_id: TraceId, stage: str, **fields: Any) -> AsyncIterator[SpanHandle]:
    """Wrap one traced unit of work. Usage:

        async with span(trace_id, "llm_request", model=model) as sp:
            ... do the work ...
            sp.set(tokens_in=123)

    Records start/end timestamps and, on exception, the error string —
    then re-raises the caller's exception untouched. Persistence/publish
    failures are logged and swallowed; they never mask or replace the
    caller's own exception.
    """
    handle = SpanHandle(trace_id, stage, fields)
    started_at = handle.started_at_ms
    try:
        yield handle
    except Exception as exc:
        handle.fields["error"] = repr(exc)
        raise
    finally:
        ended_at = int(time.time() * 1000)
        await _persist_and_publish(handle, started_at, ended_at)
