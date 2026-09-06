"""In-process asyncio pub/sub fan-out feeding live debug taps
(docs/FEATURES.md A3, PLAN.md §4.16). `app/debug/trace.py` publishes every
finished span here; `app/debug/api.py`'s `/api/debug/stream` endpoint
subscribes per SSE client.

Bounded, drop-oldest: a slow or stalled tap client must never make the
publisher (the chat hot path, via trace.py) block or raise. Publishing is
plain sync code so it can be called from any context.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

_MAX_QUEUE = 200

_subscribers: set[asyncio.Queue[Any]] = set()


def publish(event: dict[str, Any]) -> None:
    """Fan `event` out to every live subscriber queue. If a subscriber's
    queue is full, the oldest queued item is dropped to make room — the tap
    is best-effort live viewing, not a durable log (that's the `traces`/
    `spans` tables). Never raises."""
    for queue in list(_subscribers):
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)


@contextlib.asynccontextmanager
async def subscribe() -> AsyncIterator[asyncio.Queue[Any]]:
    """Register a new subscriber queue for the duration of the `async with`
    block (typically the lifetime of one SSE connection). Any event
    published while subscribed is guaranteed a delivery attempt."""
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_MAX_QUEUE)
    _subscribers.add(queue)
    try:
        yield queue
    finally:
        _subscribers.discard(queue)


def subscriber_count() -> int:
    """Number of live subscribers — handy for tests/diagnostics."""
    return len(_subscribers)
