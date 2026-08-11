"""Auto-title after a chat's first exchange (FEATURES.md F18; PLAN.md §4.15).

Enqueues a non-blocking job that calls config.background.title_model (never a
hardcoded alias), post-processes with clean_title, writes chats.title, and
best-effort emits an SSE `title` event when a chat bus is attached.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from typing import Any

from app.background.queue import get_queue
from app.chat import history
from app.db import run_sync
from app.debug import new_trace, span
from app.types import SSEEvent

logger = logging.getLogger("app.background.titles")

FENCE_RE = re.compile(r"^```(?:\w+)?\s*\n?|\n?```$")

# Few-shot prompt that measured 8/8 on Hammer (scripts/eval_title_gen.py).
# clean_title truncates to max_words=6; the prompt still asks 5-8 so the
# model produces the same shape Phase 0 scored.
_TITLE_PROMPT = (
    "Generate a short 5-8 word title summarizing this chat exchange. "
    "Output ONLY the title text -- no quotes, no code fences, no trailing "
    "period or punctuation, no preamble like \"Title:\".\n\n"
    "Example 1:\n"
    "User: How do I undo my last git commit but keep the changes?\n"
    "Assistant: Use git reset --soft HEAD~1, it undoes the commit but keeps "
    "your changes staged.\n"
    "Title: Undoing a Git Commit While Keeping Changes\n\n"
    "Example 2:\n"
    "User: What's the fastest way to check if a Python list has duplicates?\n"
    "Assistant: Compare len(lst) to len(set(lst)) -- if they differ, there "
    "are duplicates.\n"
    "Title: Checking for Duplicates in a Python List\n\n"
    "Now generate a title for this exchange:\n{exchange}\nTitle:"
)


def clean_title(raw: str, max_words: int = 6) -> str:
    """Deterministic title cleanup (mirrors scripts/postprocess_title.py).

    Strips fence/quote wrapping, a Title: preamble, and trailing punct;
    truncates overlong output. Never rejects a short title.
    """
    text = raw.strip()
    if text.startswith("```") and text.endswith("```") and len(text) >= 6:
        text = FENCE_RE.sub("", text).strip()
    while len(text) >= 2 and text[0] in "`\"'" and text[-1] == text[0]:
        text = text[1:-1].strip()
    text = re.sub(r"^title:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[.!?]+$", "", text).strip()
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
    return text


def _eligible(conn: sqlite3.Connection, chat_id: str) -> bool:
    row = conn.execute("SELECT title FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if row is None or row["title"] is not None:
        return False
    counts = conn.execute(
        "SELECT "
        "SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END) AS n_user, "
        "SUM(CASE WHEN role = 'assistant' THEN 1 ELSE 0 END) AS n_asst "
        "FROM messages WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    return (counts["n_user"] or 0) >= 1 and (counts["n_asst"] or 0) >= 1


_EXCHANGE_TRUNCATE_CHARS = 400


def _truncate(text: str, limit: int = _EXCHANGE_TRUNCATE_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _first_exchange(conn: sqlite3.Connection, chat_id: str) -> str | None:
    msgs = history.list_messages(conn, chat_id)
    user = next((m for m in msgs if m["role"] == "user"), None)
    asst = next((m for m in msgs if m["role"] == "assistant"), None)
    if user is None or asst is None:
        return None
    # _TITLE_PROMPT's few-shot examples are two short sentences; real
    # assistant replies (code blocks, long explanations) run far longer
    # and confuse the small title_model into continuing the conversation
    # instead of summarizing it (observed live: dispatcher just echoed the
    # tail of a long reply verbatim). Truncating keeps the exchange shaped
    # like the examples it's meant to pattern-match against.
    return f"User: {_truncate(user['content'])}\nAssistant: {_truncate(asst['content'])}"


def _set_title(conn: sqlite3.Connection, chat_id: str, title: str) -> bool:
    """Write title only while still NULL. Returns True if a row was updated."""
    cur = conn.execute(
        "UPDATE chats SET title = ?, updated_at = ? WHERE id = ? AND title IS NULL",
        (title, int(time.time()), chat_id),
    )
    conn.commit()
    return cur.rowcount > 0


async def _emit_title_sse(app: Any, chat_id: str, title: str) -> None:
    emit = getattr(app.state, "emit_chat_sse", None)
    if not callable(emit):
        return
    event = SSEEvent(event="title", data={"chat_id": chat_id, "title": title})
    try:
        result = emit(chat_id, event)
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            await result
    except Exception:  # noqa: BLE001 - best-effort; never break the job
        logger.debug("title SSE emit failed for chat %s", chat_id, exc_info=True)


async def _run_title_job(app: Any, chat_id: str) -> None:
    conn: sqlite3.Connection = app.state.db
    config = app.state.config
    model = config.background.title_model
    llm = getattr(app.state, "llm_client", None)
    if llm is None:
        raise RuntimeError("app.state.llm_client is not attached")

    async with span(new_trace(chat_id), "title", chat_id=chat_id, model=model) as sp:
        if not await run_sync(_eligible, conn, chat_id):
            sp.set(skipped=True, reason="not_eligible")
            return

        exchange = await run_sync(_first_exchange, conn, chat_id)
        if exchange is None:
            sp.set(skipped=True, reason="no_exchange")
            return

        prompt = _TITLE_PROMPT.format(exchange=exchange)
        raw = ""
        async for delta in llm.chat(
            model,
            [{"role": "user", "content": prompt}],
            thinking=False,
            max_tokens=64,
            stream=False,
        ):
            if delta.content:
                raw = delta.content

        title = clean_title(raw)
        sp.set(raw=raw, title=title)
        if not title:
            sp.set(skipped=True, reason="empty_title")
            return

        wrote = await run_sync(_set_title, conn, chat_id, title)
        sp.set(wrote=wrote)
        if wrote:
            await _emit_title_sse(app, chat_id, title)


async def maybe_enqueue_title(app: Any, chat_id: str) -> None:
    """Enqueue a title job when the chat is still untitled and has ≥1 exchange.

    Returns immediately; the queue worker runs the LLM call. No-ops when the
    queue is missing or the chat is ineligible.
    """
    queue = get_queue(app)
    if queue is None:
        return
    conn: sqlite3.Connection = app.state.db
    if not await run_sync(_eligible, conn, chat_id):
        return
    queue.submit(lambda: _run_title_job(app, chat_id))
