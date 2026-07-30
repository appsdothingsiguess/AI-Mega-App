"""GPU0 rewarm policy (PLAN.md §4.1 default-model paragraph).

When the GPU0 slot has served a non-default model and then been idle for
> config.gpu.rewarm_default_after_min minutes, request 1 token from
defaults.chat_model to keep it warm.

Rationale (PLAN §4.1): chat-default cold 12.47s / warm 0.67s.  Keeping the
default warm means nearly every fresh chat gets an instant first token.

Public surface (wired by settings-api at startup):
  mark_gpu0_activity(model, default)  — call after any GPU0 model dispatch
  start_rewarm(app) -> None           — creates background asyncio task
"""

from __future__ import annotations

import asyncio
import time

_last_nondefault_at: float | None = None


def mark_gpu0_activity(model: str, default: str) -> None:
    """Record that a non-default model was dispatched on GPU0.

    Call-site: orchestrator or settings-api (wired externally; out of scope
    for this module).  No-op when model == default.
    """
    global _last_nondefault_at
    if model != default:
        _last_nondefault_at = time.monotonic()


async def _rewarm_loop(app) -> None:  # type: ignore[type-arg]
    global _last_nondefault_at
    while True:
        await asyncio.sleep(60)
        if _last_nondefault_at is None:
            continue
        llm = getattr(getattr(app, "state", None), "llm_client", None)
        if llm is None:
            continue
        config = getattr(app.state, "config", None)
        if config is None:
            continue
        idle_s = time.monotonic() - _last_nondefault_at
        threshold_s = config.gpu.rewarm_default_after_min * 60
        if idle_s < threshold_s:
            continue
        try:
            async for _ in llm.chat(
                config.defaults.chat_model,
                [{"role": "user", "content": "ping"}],
                max_tokens=1,
                stream=False,
            ):
                pass
        except Exception:
            pass
        _last_nondefault_at = None


async def start_rewarm(app) -> None:  # type: ignore[type-arg]
    """Create the background rewarm task.  Called once at startup by settings-api."""
    asyncio.create_task(_rewarm_loop(app))
