"""Sequential asyncio background job queue.

Jobs are submitted as coroutine factories so each attempt (including retry)
gets a fresh awaitable. Failures never propagate to ``submit`` callers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("app.background.queue")

_CoroFactory = Callable[[], Awaitable[None]]
_SENTINEL: object = object()


class BackgroundQueue:
    """FIFO worker: one job at a time, one retry on failure, then drop."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._stopping = False

    def submit(self, coro_factory: _CoroFactory) -> None:
        try:
            if self._stopping:
                logger.warning("background queue submit ignored while stopping")
                return
            self._queue.put_nowait(coro_factory)
        except Exception:
            logger.exception("background queue submit failed")

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._stopping = False
        self._worker = asyncio.create_task(
            self._run(),
            name="background-queue-worker",
        )

    async def stop(self) -> None:
        self._stopping = True
        await self._queue.put(_SENTINEL)
        worker = self._worker
        if worker is not None:
            await worker
            self._worker = None

    def cancel(self) -> None:
        """Forcibly cancel the worker task (used when stop()'s graceful
        drain times out). Best-effort; does not await completion."""
        self._stopping = True
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None

    async def drain(self) -> None:
        """Block until every submitted job has finished (test helper)."""
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _SENTINEL:
                    break
                await self._execute(item)
            finally:
                self._queue.task_done()

    async def _execute(self, factory: _CoroFactory) -> None:
        for attempt in range(2):
            try:
                await factory()
                return
            except Exception:
                if attempt == 0:
                    logger.exception("background job failed; retrying once")
                else:
                    logger.exception("background job failed after retry; dropping")


def attach_queue(app: Any, queue: BackgroundQueue) -> None:
    app.state.background_queue = queue


def get_queue(app: Any) -> BackgroundQueue | None:
    return getattr(getattr(app, "state", None), "background_queue", None)
