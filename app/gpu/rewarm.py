"""Lifecycle-owned GPU0 default-model rewarm policy (PLAN.md §4.1)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.config import Config, ModelEntry
from app.warmup import server_identity

logger = logging.getLogger(__name__)

_REWARM_POLL_S = 60.0
_TASK_ATTR = "_rewarm_task"
_PENDING_ATTR = "_rewarm_pending_at"
_active_app: Any | None = None


def _model_entry(config: Config, name: str) -> ModelEntry | None:
    return next((m for m in config.models if m.enabled and m.name == name), None)


def mark_gpu0_activity(model: str, config: Config) -> None:
    """Record a substantive response from a distinct GPU0 server.

    Aliases such as ``reasoner`` share the default model's ``(file, gpu)``
    identity and do not represent a swap. CPU/GPU1 models do not affect the
    GPU0 slot. Activity is retained on app state only while the lifespan-owned
    rewarm loop is running.
    """
    app = _active_app
    if app is None:
        return
    model_entry = _model_entry(config, model)
    default_entry = _model_entry(config, config.defaults.chat_model)
    if model_entry is None or default_entry is None or model_entry.gpu != 0:
        return
    if server_identity(model_entry) == server_identity(default_entry):
        return
    setattr(app.state, _PENDING_ATTR, time.monotonic())


async def _rewarm_loop(app: Any) -> None:
    while True:
        await asyncio.sleep(_REWARM_POLL_S)
        pending_at = getattr(app.state, _PENDING_ATTR, None)
        if pending_at is None:
            continue
        llm = getattr(app.state, "llm_client", None)
        config: Config | None = getattr(app.state, "config", None)
        if llm is None or config is None:
            continue
        threshold_s = config.gpu.rewarm_default_after_min * 60
        if time.monotonic() - pending_at < threshold_s:
            continue
        try:
            async for _ in llm.chat(
                config.defaults.chat_model,
                [{"role": "user", "content": "ping"}],
                max_tokens=1,
                stream=False,
            ):
                pass
        except Exception as exc:  # best-effort idle policy; retry next poll
            logger.warning("GPU0 default rewarm failed: %s", exc)
        else:
            # Do not erase newer activity that arrived while the ping ran.
            if getattr(app.state, _PENDING_ATTR, None) == pending_at:
                setattr(app.state, _PENDING_ATTR, None)


async def start_rewarm(app: Any) -> None:
    """Start exactly one app-owned rewarm loop."""
    global _active_app
    existing = getattr(app.state, _TASK_ATTR, None)
    if existing is not None and not existing.done():
        return
    _active_app = app
    setattr(app.state, _PENDING_ATTR, None)
    setattr(app.state, _TASK_ATTR, asyncio.create_task(_rewarm_loop(app)))


async def stop_rewarm(app: Any) -> None:
    """Cancel and await the app-owned rewarm loop."""
    global _active_app
    task = getattr(app.state, _TASK_ATTR, None)
    if task is not None and not task.done():
        task.cancel()
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("GPU0 default rewarm task failed during shutdown")
    setattr(app.state, _TASK_ATTR, None)
    setattr(app.state, _PENDING_ATTR, None)
    if _active_app is app:
        _active_app = None
