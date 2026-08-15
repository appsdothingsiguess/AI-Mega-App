"""Concurrent asyncio background job runner.

Jobs are submitted as coroutine factories so each attempt (including retry)
gets a fresh awaitable. Each submitted job runs as its own task rather than
queueing behind unrelated jobs -- a single global FIFO worker previously
serialized e.g. title-gen (dispatcher, GPU1) behind summary-gen (utility,
CPU) even though they're separate llama-server processes with no shared
resource to contend over (2026-08-15, user-reported "clog"). Failures never
propagate to ``submit`` callers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("app.background.queue")

_CoroFactory = Callable[[], Awaitable[None]]


class BackgroundQueue:
    """Runs each submitted job concurrently; one retry on failure, then drop."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopping = False

    def submit(self, coro_factory: _CoroFactory) -> None:
        try:
            if self._stopping:
                logger.warning("background queue submit ignored while stopping")
                return
            task = asyncio.create_task(self._execute(coro_factory))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception:
            logger.exception("background queue submit failed")

    async def start(self) -> None:
        self._stopping = False

    async def stop(self) -> None:
        """Stop accepting new jobs and await every in-flight/queued job."""
        self._stopping = True
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def cancel(self) -> None:
        """Forcibly cancel every in-flight job (used when stop()'s graceful
        drain times out). Best-effort; does not await completion."""
        self._stopping = True
        for task in list(self._tasks):
            task.cancel()

    async def drain(self) -> None:
        """Block until every submitted job has finished (test helper)."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _execute(self, factory: _CoroFactory) -> None:
        for attempt in range(2):
            try:
                await factory()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt == 0:
                    logger.exception("background job failed; retrying once")
                else:
                    logger.exception("background job failed after retry; dropping")


def attach_queue(app: Any, queue: BackgroundQueue) -> None:
    app.state.background_queue = queue


def get_queue(app: Any) -> BackgroundQueue | None:
    return getattr(getattr(app, "state", None), "background_queue", None)
