"""Background jobs: titles + rolling summaries (PLAN.md §4.15, FEATURES F18).

Lifecycle hooks for settings-api / main: ``start`` / ``stop`` / ``on_turn_complete``.
Jobs run on a sequential queue; failures never block the chat path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.background.queue import BackgroundQueue, attach_queue, get_queue
from app.background.summaries import maybe_enqueue_summary
from app.background.titles import maybe_enqueue_title
from app.llm_client import LLMClient

logger = logging.getLogger("app.background")

_app: Any | None = None

# A summary job on CPU-resident `utility` can take 17-40s (PLAN.md §5); a
# graceful shutdown that just awaits the queue can silently block for that
# long. Bound the wait and cancel the worker if it's still running a job,
# rather than making systemctl restart look hung with no explanation.
_STOP_DRAIN_TIMEOUT_S = 8.0


async def start(app: Any) -> None:
    """Attach LLM client + background queue and start the worker."""
    global _app
    _app = app
    if getattr(app.state, "llm_client", None) is None:
        cfg = app.state.config
        app.state.llm_client = LLMClient(
            base_url=cfg.llama_swap.base_url,
            timeout_s=cfg.llama_swap.timeout_s,
        )
    queue = BackgroundQueue()
    attach_queue(app, queue)
    await queue.start()


async def stop(app: Any) -> None:
    """Stop the background worker (await in-flight job + sentinel).

    Bounded by _STOP_DRAIN_TIMEOUT_S: an in-flight summary job on the CPU
    `utility` model can take up to ~40s, which would otherwise make a
    service restart look hung. On timeout, the worker task is cancelled
    instead of awaited further so shutdown still completes promptly.
    """
    global _app
    queue = get_queue(app)
    if queue is not None:
        try:
            await asyncio.wait_for(queue.stop(), timeout=_STOP_DRAIN_TIMEOUT_S)
        except TimeoutError:
            logger.warning(
                "background queue did not drain within %.0fs "
                "(likely a slow in-flight summary job) — cancelling worker",
                _STOP_DRAIN_TIMEOUT_S,
            )
            queue.cancel()
    if _app is app:
        _app = None


async def on_turn_complete(chat_id: str) -> None:
    """Best-effort enqueue of title/summary jobs after a chat turn.

    Never raises; never awaits the LLM — only the enqueue path.
    """
    app = _app
    if app is None:
        return
    try:
        await maybe_enqueue_title(app, chat_id)
    except Exception:  # noqa: BLE001 - chat path must stay clean
        logger.exception("on_turn_complete title enqueue failed for %s", chat_id)
    try:
        await maybe_enqueue_summary(app, chat_id)
    except Exception:  # noqa: BLE001 - chat path must stay clean
        logger.exception("on_turn_complete summary enqueue failed for %s", chat_id)


__all__ = ["start", "stop", "on_turn_complete"]
