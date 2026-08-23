"""First-token loading wrapper used by the chat turn executor."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from app.types import ChatDelta


async def stream_with_loading(
    agen: AsyncIterator[ChatDelta], warn_s: float, timeout_s: float
) -> AsyncIterator[tuple[str, ChatDelta | None]]:
    """Yield a loading marker while retaining the pending first-token task."""
    start = time.monotonic()
    warned = False
    pending_task: asyncio.Task[ChatDelta] | None = None
    try:
        while True:
            remaining = timeout_s - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError("first_token_timeout")
            if pending_task is None:
                pending_task = asyncio.create_task(agen.__anext__())  # type: ignore[arg-type]
            wait = remaining if warned else min(warn_s, remaining)
            done, _ = await asyncio.wait({pending_task}, timeout=wait)
            if not done:
                if warned:
                    raise TimeoutError("first_token_timeout")
                warned = True
                yield ("loading", None)
                continue
            pending_task = None
            try:
                delta = done.pop().result()
            except StopAsyncIteration:
                return
            yield ("delta", delta)
            break
        async for delta in agen:
            yield ("delta", delta)
    finally:
        if pending_task is not None and not pending_task.done():
            pending_task.cancel()
            try:
                await pending_task
            except (asyncio.CancelledError, Exception):
                pass
