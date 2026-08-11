"""FastAPI app factory (docs/FEATURES.md; PLAN.md §5 Phase 1). Loads config,
opens the SQLite DB, mounts /health, serves web/ statically, and leaves
marked mount points for wave-2 routers to app.include_router() into.
"""

from __future__ import annotations

import asyncio
import logging
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
from app.llm_client import LLMClient
from app.settings.api import router as settings_router
from app.warmup import (
    warmup_resident_models,
    all_residents_loaded,
    loaded_resident_names,
    _STARTUP_BACKOFF_S as _WARMUP_STARTUP_BACKOFF_S,
)

# uvicorn's default logging config (dictConfig) only sets up the "uvicorn"/
# "uvicorn.error"/"uvicorn.access" loggers -- it never touches the root
# logger, which defaults to WARNING. Every app.* logger.info(...) call
# (warmup loop included) is silently dropped in production as a result.
# basicConfig is a no-op if a handler is already installed on root, so this
# is safe regardless of import order relative to uvicorn's own setup.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Soft-import wave-2 peers that land via parallel merges. Missing packages
# are expected until interface-gate blockers clear (app.gpu / app.background
# not in this worktree; start_rewarm is async and must be awaited when present).
try:
    from app.gpu.api import router as gpu_router
    from app.gpu.rewarm import start_rewarm
except ImportError:  # BLOCKED: app.gpu absent — interface-gate (step 2)
    gpu_router = None
    start_rewarm = None

try:
    from app.background import start as background_start, stop as background_stop
except ImportError:  # BLOCKED: app.background absent — interface-gate (step 2)
    background_start = None
    background_stop = None

_logger = logging.getLogger(__name__)

WEB_DIR = REPO_ROOT / "web"

# How often the resident-model warm-up sweep re-fires after startup. A
# llama-swap config reload (e.g. Settings UI "Apply GPU config") kills every
# running llama-server process, not just the swapping GPU0 slot — resident
# CPU/GPU1 models (classifier, dispatcher, utility, embed) then
# sit cold until the next real request hits them. A one-shot startup warm-up
# can't detect that; a periodic sweep self-heals within one interval.
_WARMUP_INTERVAL_S = 300.0


async def _warmup_loop(app: FastAPI) -> None:
    """Startup warm-up, then a periodic sweep so resident models recover
    automatically after a llama-swap config reload wipes them (see
    _WARMUP_INTERVAL_S). app/gpu/api.py additionally fires an immediate
    warm-up right after a successful /api/gpu/apply, so this loop is the
    steady-state fallback, not the only recovery path.

    Startup phase: retry with _WARMUP_STARTUP_BACKOFF_S until every
    resident swap name reports loaded, then settle into the
    _WARMUP_INTERVAL_S periodic sweep.  Each individual sweep body is
    wrapped in try/except so a single failure (e.g. llama-swap temporarily
    unreachable) never kills the loop."""
    import logging
    logger = logging.getLogger(__name__)
    startup_phase = True
    while True:
        try:
            llm = getattr(getattr(app, "state", None), "llm_client", None)
            cfg: Config | None = getattr(getattr(app, "state", None), "config", None)
            logger.info("warmup loop: starting sweep (llm=%s, cfg=%s, phase=%s)",
                        llm is not None, cfg is not None,
                        "startup" if startup_phase else "steady-state")
            # Only re-ping models not already confirmed loaded — during the
            # startup retry storm, re-pinging stragglers' already-warm
            # siblings every _STARTUP_BACKOFF_S sends real completions that
            # compete with live traffic for CPU (see HANDOFF 2026-08-11:
            # this starved a background summary job to a 120s timeout).
            already_loaded = await loaded_resident_names(llm, cfg) if startup_phase else set()
            await warmup_resident_models(llm, cfg, skip=already_loaded)
            logger.info("warmup loop: sweep complete")
            if startup_phase and await all_residents_loaded(llm, cfg):
                startup_phase = False
                logger.info("warmup loop: all residents loaded, switching to steady-state (%.0fs interval)",
                            _WARMUP_INTERVAL_S)
        except Exception:
            logger.exception("warmup loop: sweep failed, retrying after backoff")
        interval = _WARMUP_STARTUP_BACKOFF_S if startup_phase else _WARMUP_INTERVAL_S
        await asyncio.sleep(interval)


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
        # Set up llm_client before background.start so warmup always has
        # a client regardless of whether the soft-import succeeded.
        if getattr(app.state, "llm_client", None) is None:
            app.state.llm_client = LLMClient(
                base_url=cfg.llama_swap.base_url,
                timeout_s=cfg.llama_swap.timeout_s,
            )
        if background_start is not None:
            await background_start(app)
        if start_rewarm is not None:
            await start_rewarm(app)  # async when present (sibling drift)
        app.state._warmup_task = asyncio.create_task(_warmup_loop(app))
        try:
            yield
        finally:
            if background_stop is not None:
                await background_stop(app)
            task = getattr(app.state, "_warmup_task", None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
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
    app.include_router(settings_router)  # Phase 2 -> app/settings/api.py
    if gpu_router is not None:
        app.include_router(gpu_router)  # Phase 2 -> app/gpu/api.py
    # -----------------------------------------------------------------------

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = create_app()
