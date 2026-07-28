"""FastAPI app factory (docs/FEATURES.md; PLAN.md §5 Phase 1). Loads config,
opens the SQLite DB, mounts /health, serves web/ statically, and leaves
marked mount points for wave-2 routers to app.include_router() into.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.chat.api import router as chat_router
from app.config import REPO_ROOT, Config, get_config
from app.db import check_connection, open_db
from app.debug.api import router as debug_router
from app.debug.trace import reset_connection as reset_debug_connection

WEB_DIR = REPO_ROOT / "web"


def _resolve_db_path(config: Config) -> Path:
    db_path = Path(config.db.path)
    return db_path if db_path.is_absolute() else REPO_ROOT / db_path


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.config = cfg
        app.state.db = open_db(_resolve_db_path(cfg))
        # app/debug/trace.py opens its own connection lazily via the process-
        # global get_config() cache, which does not track a per-app Config
        # instance (e.g. one test's tmp db). Bind it explicitly to the same
        # connection this app instance uses so traces/spans always land in
        # the right database, and unbind on shutdown so a later app instance
        # (e.g. the next test) doesn't inherit a closed connection.
        reset_debug_connection(app.state.db)
        try:
            yield
        finally:
            reset_debug_connection(None)
            app.state.db.close()

    app = FastAPI(title="AI Mega App", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, object]:
        conn: sqlite3.Connection = app.state.db
        return {
            "status": "ok",
            "version": __version__,
            "db": "ok" if check_connection(conn) else "error",
            # Static roster from config.yaml, not live llama-swap state.
            # Placeholder for the composer model picker until Phase 2's
            # GET /api/models adds resident/loaded flags (docs/FEATURES.md F3).
            "models": [
                {"name": m.name, "class": m.class_, "enabled": m.enabled}
                for m in cfg.models
                if m.enabled
            ],
        }

    # --- Wave-2 mount points (each agent includes its own router here) ---
    app.include_router(chat_router)  # p1/chat-sse -> app/chat/api.py
    app.include_router(debug_router)  # p1/debug-trace -> app/debug/api.py
    # app.include_router(settings_api.router) # Phase 2 -> app/settings/api.py
    # -----------------------------------------------------------------------

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = create_app()
