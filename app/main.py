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
from app.config import REPO_ROOT, Config, get_config
from app.db import check_connection, open_db

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
        try:
            yield
        finally:
            app.state.db.close()

    app = FastAPI(title="AI Mega App", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        conn: sqlite3.Connection = app.state.db
        return {
            "status": "ok",
            "version": __version__,
            "db": "ok" if check_connection(conn) else "error",
        }

    # --- Wave-2 mount points (each agent includes its own router here) ---
    # app.include_router(chat_api.router)     # p1/chat-sse -> app/chat/api.py
    # app.include_router(debug_api.router)    # p1/debug-trace -> app/debug/api.py
    # app.include_router(settings_api.router) # Phase 2 -> app/settings/api.py
    # -----------------------------------------------------------------------

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = create_app()
