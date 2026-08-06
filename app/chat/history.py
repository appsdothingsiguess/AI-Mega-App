"""Message persistence helpers over app/db.py (docs/FEATURES.md A4).

Plain sync functions taking a sqlite3.Connection — callers await them via
app.db.run_sync so the event loop never blocks (rule 001). No ORM; hand
written SQL, one function per operation (PLAN.md §6 guardrail).
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any


def _now() -> int:
    return int(time.time())


def create_chat(conn: sqlite3.Connection, project_id: str | None = None) -> dict[str, Any]:
    """Insert a new chat row and return it as a plain dict."""
    chat_id = uuid.uuid4().hex
    now = _now()
    conn.execute(
        "INSERT INTO chats (id, title, project_id, model_override, summary, "
        "created_at, updated_at) VALUES (?, NULL, ?, NULL, NULL, ?, ?)",
        (chat_id, project_id, now, now),
    )
    conn.commit()
    return {"id": chat_id, "title": None, "updated_at": now}


def list_chats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """id/title/updated_at for every chat, most recently updated first."""
    rows = conn.execute(
        "SELECT id, title, updated_at, summary FROM chats ORDER BY updated_at DESC"
    ).fetchall()
    return [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"], "summary": r["summary"]} for r in rows]


def get_chat(conn: sqlite3.Connection, chat_id: str) -> sqlite3.Row | None:
    """Full chat row (used for model_override resolution), or None."""
    return conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()


def set_model_override(conn: sqlite3.Connection, chat_id: str, model: str | None) -> None:
    """Set (or clear, when model is None) a chat's manual model override —
    the layer-1 winner in Phase 2's router (PLAN.md §4.3)."""
    conn.execute(
        "UPDATE chats SET model_override = ?, updated_at = ? WHERE id = ?",
        (model, _now(), chat_id),
    )
    conn.commit()


def touch_chat(conn: sqlite3.Connection, chat_id: str) -> None:
    conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (_now(), chat_id))
    conn.commit()


def list_messages(conn: sqlite3.Connection, chat_id: str) -> list[dict[str, Any]]:
    """Full message history for a chat, oldest first, per-message model."""
    rows = conn.execute(
        "SELECT id, role, content, model, created_at FROM messages "
        "WHERE chat_id = ? ORDER BY created_at ASC, rowid ASC",
        (chat_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "model": r["model"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def insert_message(
    conn: sqlite3.Connection,
    chat_id: str,
    role: str,
    content: str,
    model: str | None,
) -> dict[str, Any]:
    """Persist one message (user or assistant) and return it as a dict.
    Assistant rows are tagged with the model name that produced them."""
    message_id = uuid.uuid4().hex
    now = _now()
    conn.execute(
        "INSERT INTO messages (id, chat_id, role, content, model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (message_id, chat_id, role, content, model, now),
    )
    conn.commit()
    return {
        "id": message_id,
        "chat_id": chat_id,
        "role": role,
        "content": content,
        "model": model,
        "created_at": now,
    }


def build_llm_messages(conn: sqlite3.Connection, chat_id: str) -> list[dict[str, str]]:
    """The chat history as an OpenAI-format messages list for LLMClient.chat().
    Phase 3's context providers (RAG/memory) will prepend/inject around this
    seam; Phase 1 sends plain role/content turns only."""
    history = list_messages(conn, chat_id)
    return [{"role": m["role"], "content": m["content"]} for m in history]
