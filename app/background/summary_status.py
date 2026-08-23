"""Read-only rolling-summary status queries."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.background.summary_coverage import trusted_covered_count
from app.chat import history
from app.db import run_sync

from .summary_policy import count_user_turns, latest_usage, trigger_state


def last_summary_span(conn: sqlite3.Connection, chat_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT s.data AS data, s.started_at AS started_at FROM spans s "
        "JOIN traces t ON t.trace_id = s.trace_id WHERE t.chat_id = ? "
        "AND s.stage = 'summary' ORDER BY s.started_at DESC LIMIT 1", (chat_id,),
    ).fetchone()
    if row is None or row["data"] is None:
        return None
    try:
        data = json.loads(row["data"])
    except (TypeError, ValueError):
        return None
    return {"started_at": row["started_at"], "model": data.get("model"), "device": data.get("device"), "new_message_count": data.get("new_message_count"), "covered_message_count": data.get("covered_message_count"), "time_budget_tokens": data.get("time_budget_tokens"), "chars": data.get("chars"), "error": data.get("error")}


async def summary_status(app: Any, chat_id: str, in_flight: set[str]) -> dict[str, Any]:
    cfg, conn = app.state.config, app.state.db
    turns = await run_sync(count_user_turns, conn, chat_id)
    state = trigger_state(cfg, await run_sync(latest_usage, conn, chat_id), turns)
    state["turn_count"] = turns
    messages = await run_sync(history.list_messages, conn, chat_id)
    chat_row = await run_sync(history.get_chat, conn, chat_id)
    trusted = await run_sync(trusted_covered_count, conn, chat_id, messages, chat_row["summary"] if chat_row is not None else None)
    state["covered_message_count"] = trusted or 0
    state["last_summary"] = await run_sync(last_summary_span, conn, chat_id)
    state["in_flight"] = chat_id in in_flight
    return state
