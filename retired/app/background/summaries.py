"""Compatibility facade and queue coordination for rolling summaries."""

from __future__ import annotations

import logging
from typing import Any

from app.background.queue import get_queue
from app.db import run_sync

from .summary_policy import (
    count_user_turns as _count_user_turns,
    fit_to_budget as _fit_to_budget,
    format_transcript as _format_transcript,
    latest_usage as _latest_usage,
    min_routable_ctx as _min_routable_ctx,
    summary_targets as _summary_targets,
    time_budget_tokens as _time_budget_tokens,
    trigger_state as _trigger_state,
)
from .summary_runner import (
    run_summary as _run_summary,
    run_summary_guarded as _run_summary_guarded,
    set_summary as _set_summary,
)
from .summary_status import last_summary_span as _last_summary_span
from .summary_status import summary_status as _summary_status

logger = logging.getLogger("app.background.summaries")
_in_flight: set[str] = set()


async def summary_status(app: Any, chat_id: str) -> dict[str, Any]:
    return await _summary_status(app, chat_id, _in_flight)


async def maybe_enqueue_summary(app: Any, chat_id: str) -> None:
    """Enqueue a rolling summary when token pressure says it is time."""
    try:
        cfg, conn = app.state.config, app.state.db
        turn_count = await run_sync(_count_user_turns, conn, chat_id)
        if turn_count <= 0:
            return
        latest = await run_sync(_latest_usage, conn, chat_id)
        if not _trigger_state(cfg, latest, turn_count)["will_trigger"]:
            return
        _submit_summary(app, chat_id)
    except Exception:
        logger.exception("maybe_enqueue_summary failed for chat %s", chat_id)


def _submit_summary(app: Any, chat_id: str) -> bool:
    if chat_id in _in_flight:
        return False
    queue = get_queue(app)
    if queue is None:
        logger.warning("summary not enqueued: background queue missing")
        return False
    _in_flight.add(chat_id)

    def factory() -> Any:
        return _run_summary_guarded(app, chat_id, _in_flight)

    queue.submit(factory)
    return True


async def enqueue_summary_recovery(app: Any, chat_id: str) -> bool:
    """Force a tracked summary recovery after a safe-fit refusal."""
    try:
        return _submit_summary(app, chat_id)
    except Exception:
        logger.exception("summary recovery enqueue failed for chat %s", chat_id)
        return False
