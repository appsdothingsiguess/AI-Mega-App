"""Rolling-summary execution and coverage-safe persistence."""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from app.background.summary_coverage import coverage_fields, trusted_covered_count
from app.chat import history
from app.db import run_sync
from app.debug import new_trace, span
from app.llm_client import LLMError

from .summary_policy import (
    _SUMMARY_PROMPT, fit_to_budget, format_transcript, summary_targets, time_budget_tokens,
)

logger = logging.getLogger("app.background.summaries")


def set_summary(conn: sqlite3.Connection, chat_id: str, summary: str) -> None:
    conn.execute("UPDATE chats SET summary = ?, updated_at = ? WHERE id = ?", (summary, int(time.time()), chat_id))
    conn.commit()


async def run_summary(app: Any, chat_id: str) -> None:
    cfg, conn, llm = app.state.config, app.state.db, app.state.summary_llm_client
    chat_row = await run_sync(history.get_chat, conn, chat_id)
    if chat_row is None:
        logger.warning("summary skipped: chat %s not found", chat_id)
        return
    messages = await run_sync(history.list_messages, conn, chat_id)
    if not messages:
        logger.warning("summary skipped: chat %s has no messages", chat_id)
        return
    prior = chat_row["summary"]
    trusted = await run_sync(trusted_covered_count, conn, chat_id, messages, prior)
    covered = trusted if trusted is not None else 0
    all_new = messages[covered:]
    if not all_new:
        logger.info("summary skipped: chat %s has no messages since last summary", chat_id)
        return
    targets = summary_targets(cfg)
    if not targets:
        logger.warning("summary skipped: chat %s has no enabled summary model", chat_id)
        return
    timeout_s, last_error = cfg.background.summary_timeout_s, None
    for entry, prefill, decode, device in targets:
        budget = time_budget_tokens(timeout_s, prefill, decode, entry.max_tokens)
        pre_fit_count = len(all_new)
        new = fit_to_budget(all_new, prior, entry.ctx, entry.max_tokens, budget)
        if not new:
            logger.warning("summary target %s (%s) skipped for chat %s: time/ctx budget leaves no room for even the next unsummarized message", entry.name, device, chat_id)
            continue
        if len(new) < pre_fit_count:
            logger.info("summary for chat %s on %s (%s): delta (%d msgs) exceeds its budget, summarizing oldest %d now and leaving the rest for the next regen", chat_id, entry.name, device, pre_fit_count, len(new))
        count = covered + len(new)
        transcript = format_transcript(new)
        if not transcript:
            logger.warning("summary skipped: chat %s has no message text", chat_id)
            return
        parts = ([f"Previous summary:\n{prior}", f"New messages since the previous summary:\n{transcript}"] if prior else [f"Conversation so far:\n{transcript}"])
        parts.append("Updated summary:")
        prompt_messages = [{"role": "system", "content": _SUMMARY_PROMPT}, {"role": "user", "content": "\n\n".join(parts)}]
        trace_id = new_trace(chat_id)
        async with span(trace_id, "summary", model=entry.name, device=device, chat_id=chat_id) as sp:
            try:
                content_parts: list[str] = []
                finish_reason: str | None = None
                usage = None
                async for delta in llm.chat(model=entry.name, messages=prompt_messages, thinking=False, max_tokens=entry.max_tokens, stream=False):
                    if delta.content:
                        content_parts.append(delta.content)
                    if delta.finish_reason is not None:
                        finish_reason = delta.finish_reason
                    if delta.usage is not None:
                        usage = delta.usage
                content = "".join(content_parts).strip()
                if not content:
                    raise RuntimeError("summary model returned empty content")
                if finish_reason != "stop":
                    raise RuntimeError(f"summary model did not complete cleanly (finish_reason={finish_reason!r})")
            except (LLMError, RuntimeError) as exc:
                last_error = exc
                sp.set(error=str(exc), time_budget_tokens=budget)
                logger.warning("summary target %s (%s) failed for chat %s: %s -- trying next target if any", entry.name, device, chat_id, exc)
                continue
            await run_sync(set_summary, conn, chat_id, content)
            fields: dict[str, Any] = {"chars": len(content), "new_message_count": len(new), "time_budget_tokens": budget, "prompt": prompt_messages[-1]["content"], "response": content}
            fields.update(coverage_fields(messages, count, content))
            if usage is not None:
                fields["usage"] = usage.model_dump()
            sp.set(**fields)
            return
    if last_error is not None:
        raise last_error
    logger.warning("summary skipped: chat %s -- no target had budget for the next unsummarized message", chat_id)


async def run_summary_guarded(app: Any, chat_id: str, in_flight: set[str]) -> None:
    try:
        await run_summary(app, chat_id)
    finally:
        in_flight.discard(chat_id)
