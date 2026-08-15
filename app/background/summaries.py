"""Rolling chat summaries — every N user turns (PLAN.md §4.15, FEATURES F18).

Model alias comes only from ``config.background.summary_model``; never a
hardcoded literal. Failures stay inside the background queue (retry once).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from app.background.queue import get_queue
from app.chat import history
from app.db import run_sync
from app.debug import new_trace, span

logger = logging.getLogger("app.background.summaries")

_MAX_TOKENS = 1024

_SUMMARY_PROMPT = (
    "Write a concise rolling summary of this conversation for later context "
    "and compaction. Capture key facts, decisions, and open questions. "
    "Output only the summary text — no preamble or labels."
)


def _count_user_turns(conn: sqlite3.Connection, chat_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE chat_id = ? AND role = 'user'",
        (chat_id,),
    ).fetchone()
    return int(row["n"])


def _set_summary(conn: sqlite3.Connection, chat_id: str, summary: str) -> None:
    conn.execute(
        "UPDATE chats SET summary = ?, updated_at = ? WHERE id = ?",
        (summary, int(time.time()), chat_id),
    )
    conn.commit()


def _format_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role") or "unknown"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines)


async def _run_summary(app: Any, chat_id: str) -> None:
    cfg = app.state.config
    model = cfg.background.summary_model
    conn = app.state.db
    llm = app.state.llm_client

    chat_row = await run_sync(history.get_chat, conn, chat_id)
    if chat_row is None:
        logger.warning("summary skipped: chat %s not found", chat_id)
        return

    messages = await run_sync(history.list_messages, conn, chat_id)
    transcript = _format_transcript(messages)
    if not transcript:
        logger.warning("summary skipped: chat %s has no message text", chat_id)
        return

    prior = chat_row["summary"]
    user_parts: list[str] = []
    if prior:
        user_parts.append(f"Previous summary:\n{prior}")
    user_parts.append(f"Conversation:\n{transcript}")
    user_parts.append("Updated summary:")

    prompt_messages = [
        {"role": "system", "content": _SUMMARY_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]

    trace_id = new_trace(chat_id)
    async with span(trace_id, "summary", model=model, chat_id=chat_id) as sp:
        content = ""
        usage = None
        async for delta in llm.chat(
            model=model,
            messages=prompt_messages,
            thinking=False,
            max_tokens=_MAX_TOKENS,
            stream=False,
        ):
            if delta.content:
                content = delta.content.strip()
            if delta.usage is not None:
                usage = delta.usage
        if not content:
            raise RuntimeError("summary model returned empty content")
        await run_sync(_set_summary, conn, chat_id, content)
        fields: dict[str, Any] = {
            "chars": len(content),
            "prompt": prompt_messages[-1]["content"],
            "response": content,
        }
        if usage is not None:
            fields["usage"] = usage.model_dump()
        sp.set(**fields)


async def maybe_enqueue_summary(app: Any, chat_id: str) -> None:
    """Enqueue a rolling summary when user-turn count hits the cadence."""
    try:
        cfg = app.state.config
        every_n = cfg.background.summary_every_n_turns
        if every_n <= 0:
            return
        turn_count = await run_sync(_count_user_turns, app.state.db, chat_id)
        if turn_count <= 0 or turn_count % every_n != 0:
            return
        queue = get_queue(app)
        if queue is None:
            logger.warning("summary not enqueued: background queue missing")
            return

        def factory() -> Any:
            return _run_summary(app, chat_id)

        queue.submit(factory)
    except Exception:
        logger.exception("maybe_enqueue_summary failed for chat %s", chat_id)
