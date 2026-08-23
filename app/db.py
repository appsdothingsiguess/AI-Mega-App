"""SQLite storage core (docs/FEATURES.md A2). Connection factory (WAL,
busy_timeout), schema application, and a thread-executor wrapper so sync
sqlite3 calls stay off the event loop — no aiosqlite (rule 001; the executor
helper is the ~15 lines FEATURES.md calls for).

Feature-specific query helpers (chats, messages, traces, spans repositories)
live beside their feature module (e.g. app/chat/history.py), not here —
db.py stays generic: connect, schema, executor.
"""

from __future__ import annotations

import asyncio
import functools
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# ``check_same_thread=False`` is necessary because repository calls run in the
# default executor, but it does not make one sqlite3.Connection safe for
# concurrent executor calls.  The app owns one connection, so serialize those
# calls at this boundary rather than requiring every repository helper to
# coordinate independently.  A threading lock belongs here (not an asyncio
# lock) because the protected work executes in executor threads.
_EXECUTOR_DB_LOCK = threading.Lock()


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection with a sane busy_timeout. Creates
    the parent directory if needed. Row access by column name via
    sqlite3.Row."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply app/schema.sql. Idempotent — every statement is
    CREATE TABLE/INDEX IF NOT EXISTS."""
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(script)
    conn.commit()


def open_db(path: str | Path) -> sqlite3.Connection:
    """connect() + init_db() in one call — what app/main.py uses at startup."""
    conn = connect(path)
    init_db(conn)
    return conn


def check_connection(conn: sqlite3.Connection) -> bool:
    """Cheap liveness probe for /health."""
    try:
        conn.execute("SELECT 1")
        return True
    except sqlite3.Error:
        return False


async def run_sync[T](func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a blocking sqlite3 call in the default executor so it never
    blocks the event loop. Calls are serialized because the app shares one
    sqlite connection across request and background executor work. Feature
    repository modules build their query helpers as plain sync functions and
    await run_sync(helper, conn, ...)."""
    loop = asyncio.get_running_loop()
    call = functools.partial(func, *args, **kwargs)

    def locked_call() -> T:
        with _EXECUTOR_DB_LOCK:
            return call()

    return await loop.run_in_executor(None, locked_call)
