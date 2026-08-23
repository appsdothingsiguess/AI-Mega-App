"""Incremental, token-triggered rolling summaries (PLAN §4.15; FEATURES F18).

Each run folds the oldest budget-fitting unsummarized prefix into the prior
summary. Coverage is committed only with fingerprint metadata owned by
``summary_coverage``; invalid state restarts conservatively from raw history.
Model aliases and budgets come exclusively from config.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from app.background.queue import get_queue
from app.background.summary_coverage import coverage_fields, trusted_covered_count
from app.chat import history
from app.db import run_sync
from app.debug import new_trace, span
from app.llm_client import LLMError

logger = logging.getLogger("app.background.summaries")

# Rough chars-per-token for budgeting the summarizer's OWN prompt -- same
# conservative (low, over-counts tokens) constant app/chat/orchestrator.py
# uses for its ctx safety net.
_CHARS_PER_TOKEN = 3.5

_SUMMARY_PROMPT = (
    "Update the rolling summary of this conversation. You are given the "
    "previous summary and the messages that happened since it was written. "
    "Produce an updated summary that preserves everything still relevant "
    "from the previous summary and folds in what's new. Output only the "
    "summary text -- no preamble or labels.\n\n"
    "Structure it with these sections, omitting any that are empty:\n"
    "- Key facts and context\n"
    "- Decisions made\n"
    "- User preferences or constraints stated\n"
    "- Open questions / unresolved threads"
)

# Jobs currently running per chat_id, so a fast run of turns above the token
# threshold (regen takes several seconds) can't submit duplicate concurrent
# summary jobs for the same chat before the first one lands and compaction
# brings the next turn's prompt_tokens back down.
_in_flight: set[str] = set()


def _min_routable_ctx(cfg: Any) -> int | None:
    """Smallest ctx among every model the router could plausibly send this
    chat's *next* turn to (routing.intents targets + the classifier's
    fallback_model).

    2026-08-15: a chat can sit at, say, 20% of chat-default's 32768 ctx
    (comfortably under threshold) and then get routed to coder-small
    (ctx 8192) the moment a message reads as a coding question -- checking
    growth only against whichever model happened to serve the *last* turn
    would let raw history sail right past a smaller model's real budget
    with zero warning. This is the safety floor maybe_enqueue_summary pairs
    with the per-turn check and lossless prompt-fit refusal."""
    routing = getattr(cfg, "routing", None)
    if routing is None:
        return None
    names = set(routing.intents.model_dump().values())
    names.add(routing.classifier.fallback_model)
    ctxs = [m.ctx for m in cfg.models if m.name in names]
    return min(ctxs) if ctxs else None


def _count_user_turns(conn: sqlite3.Connection, chat_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE chat_id = ? AND role = 'user'",
        (chat_id,),
    ).fetchone()
    return int(row["n"])


def _latest_usage(conn: sqlite3.Connection, chat_id: str) -> tuple[int, str | None] | None:
    """(prompt_tokens, model) from the most recent llm_stream span for this
    chat -- llama.cpp's own count, never a client-side estimate. `model` is
    the alias that turn actually ran on, so the caller can size the trigger
    threshold to *that* model's ctx rather than a flat number (the roster
    spans ctx 8192-32768)."""
    row = conn.execute(
        "SELECT s.data AS data FROM spans s "
        "JOIN traces t ON t.trace_id = s.trace_id "
        "WHERE t.chat_id = ? AND s.stage = 'llm_stream' "
        "ORDER BY s.started_at DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    if row is None or row["data"] is None:
        return None
    try:
        data = json.loads(row["data"])
    except (TypeError, ValueError):
        return None
    usage = data.get("usage") or {}
    tokens = usage.get("prompt_tokens")
    if not isinstance(tokens, (int, float)):
        return None
    return int(tokens), data.get("model")


def _set_summary(conn: sqlite3.Connection, chat_id: str, summary: str) -> None:
    conn.execute(
        "UPDATE chats SET summary = ?, updated_at = ? WHERE id = ?",
        (summary, int(time.time()), chat_id),
    )
    conn.commit()


def _format_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role") or "unknown"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines)


# Safety margin subtracted from summary_timeout_s before converting the
# remainder into an input-token budget -- covers HTTP/queue overhead that
# isn't pure model prefill/decode time.
_TIME_BUDGET_SAFETY_MARGIN_S = 15.0


def _time_budget_tokens(
    timeout_s: float,
    prefill_tokens_per_sec: float,
    decode_tokens_per_sec: float,
    max_output_tokens: int,
) -> int:
    """Max input tokens a summarizer target can *finish processing* within
    timeout_s, given its own measured prefill/decode speed.

    ctx alone is not a usable budget: CPU-resident `utility` measured
    ~5 tok/s decode (docs/HANDOFF.md this-session entry) -- generating even
    a capped 512-token response alone costs ~100s, so an oversized bite
    that fits comfortably inside an 8192 ctx can still blow well past any
    reasonable timeout. This is what the 2026-08-11 double-timeout incident
    actually was: a ctx-sized bite the CPU model was ctx-permitted but not
    speed-permitted to finish."""
    decode_time_s = max_output_tokens / decode_tokens_per_sec
    remaining_s = timeout_s - _TIME_BUDGET_SAFETY_MARGIN_S - decode_time_s
    if remaining_s <= 0:
        return 0
    return int(remaining_s * prefill_tokens_per_sec)


def _fit_to_budget(
    new_messages: list[dict[str, Any]],
    prior: str | None,
    ctx: int,
    max_output_tokens: int,
    extra_budget_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """Keep the *oldest* prefix of new_messages whose formatted transcript
    fits the summarizer's own ctx budget (ctx minus the response budget,
    the fixed system prompt, and the prior-summary text this call also
    sends), further capped by extra_budget_tokens (the speed-derived time
    budget from _time_budget_tokens) when given -- whichever is smaller
    actually binds. Dropping from the newest end -- not the oldest -- means
    whatever gets cut simply stays "new" for the next regen instead of
    being silently lost from context forever."""
    overhead_chars = len(_SUMMARY_PROMPT) + len(prior or "")
    budget_tokens = ctx - max_output_tokens - (overhead_chars / _CHARS_PER_TOKEN)
    budget_tokens = max(budget_tokens, ctx // 4)  # floor so a tiny ctx still gets something
    if extra_budget_tokens is not None:
        budget_tokens = min(budget_tokens, extra_budget_tokens)
        budget_tokens = max(budget_tokens, 1)

    kept: list[dict[str, Any]] = []
    for msg in new_messages:
        candidate = kept + [msg]
        estimate = len(_format_transcript(candidate)) / _CHARS_PER_TOKEN
        if estimate > budget_tokens and kept:
            break
        kept = candidate
    return kept


def _summary_targets(cfg: Any) -> list[tuple[Any, float, float, str]]:
    """Ordered candidate targets: (model_entry, prefill_tps, decode_tps,
    device_label). GPU1 fast path first (utility-gpu, ~14x CPU decode speed
    per docs/HANDOFF.md's 2026-08-15 live benchmark), CPU fallback second.
    Only entries actually present + enabled in the roster are returned, so
    disabling utility-gpu in config.yaml cleanly degrades to CPU-only."""
    bg = cfg.background
    candidates = [
        (bg.summary_model_gpu, bg.summary_gpu_tokens_per_sec_prefill,
         bg.summary_gpu_tokens_per_sec_decode, "gpu1"),
        (bg.summary_model, bg.summary_cpu_tokens_per_sec_prefill,
         bg.summary_cpu_tokens_per_sec_decode, "cpu"),
    ]
    targets: list[tuple[Any, float, float, str]] = []
    seen_names: set[str] = set()
    for name, prefill_tps, decode_tps, device in candidates:
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        entry = next((m for m in cfg.models if m.name == name and m.enabled), None)
        if entry is not None:
            targets.append((entry, prefill_tps, decode_tps, device))
    return targets


async def _run_summary(app: Any, chat_id: str) -> None:
    cfg = app.state.config
    conn = app.state.db
    llm = app.state.summary_llm_client

    chat_row = await run_sync(history.get_chat, conn, chat_id)
    if chat_row is None:
        logger.warning("summary skipped: chat %s not found", chat_id)
        return

    messages = await run_sync(history.list_messages, conn, chat_id)
    if not messages:
        logger.warning("summary skipped: chat %s has no messages", chat_id)
        return

    prior = chat_row["summary"]
    trusted_count = await run_sync(
        trusted_covered_count, conn, chat_id, messages, prior
    )
    covered_so_far = trusted_count if trusted_count is not None else 0
    all_new_messages = messages[covered_so_far:]

    if not all_new_messages:
        logger.info("summary skipped: chat %s has no messages since last summary", chat_id)
        return

    targets = _summary_targets(cfg)
    if not targets:
        logger.warning("summary skipped: chat %s has no enabled summary model", chat_id)
        return

    timeout_s = cfg.background.summary_timeout_s
    last_error: Exception | None = None

    for model_entry, prefill_tps, decode_tps, device in targets:
        time_budget = _time_budget_tokens(
            timeout_s, prefill_tps, decode_tps, model_entry.max_tokens
        )
        pre_fit_count = len(all_new_messages)
        new_messages = _fit_to_budget(
            all_new_messages, prior, model_entry.ctx, model_entry.max_tokens, time_budget
        )
        if not new_messages:
            logger.warning(
                "summary target %s (%s) skipped for chat %s: time/ctx budget "
                "leaves no room for even the next unsummarized message",
                model_entry.name, device, chat_id,
            )
            continue
        if len(new_messages) < pre_fit_count:
            logger.info(
                "summary for chat %s on %s (%s): delta (%d msgs) exceeds its "
                "budget, summarizing oldest %d now and leaving the rest for "
                "the next regen",
                chat_id, model_entry.name, device, pre_fit_count, len(new_messages),
            )

        covered_message_count = covered_so_far + len(new_messages)
        transcript = _format_transcript(new_messages)
        if not transcript:
            logger.warning("summary skipped: chat %s has no message text", chat_id)
            return

        user_parts: list[str] = []
        if prior:
            user_parts.append(f"Previous summary:\n{prior}")
            user_parts.append(f"New messages since the previous summary:\n{transcript}")
        else:
            user_parts.append(f"Conversation so far:\n{transcript}")
        user_parts.append("Updated summary:")

        prompt_messages = [
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]

        trace_id = new_trace(chat_id)
        async with span(
            trace_id, "summary", model=model_entry.name, device=device, chat_id=chat_id
        ) as sp:
            try:
                content_parts: list[str] = []
                finish_reason: str | None = None
                usage = None
                async for delta in llm.chat(
                    model=model_entry.name,
                    messages=prompt_messages,
                    thinking=False,
                    max_tokens=model_entry.max_tokens,
                    stream=False,
                ):
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
                    raise RuntimeError(
                        "summary model did not complete cleanly "
                        f"(finish_reason={finish_reason!r})"
                    )
            except (LLMError, RuntimeError) as exc:
                last_error = exc
                sp.set(error=str(exc), time_budget_tokens=time_budget)
                logger.warning(
                    "summary target %s (%s) failed for chat %s: %s -- "
                    "trying next target if any",
                    model_entry.name, device, chat_id, exc,
                )
                continue

            await run_sync(_set_summary, conn, chat_id, content)
            fields: dict[str, Any] = {
                "chars": len(content),
                "new_message_count": len(new_messages),
                "time_budget_tokens": time_budget,
                "prompt": prompt_messages[-1]["content"],
                "response": content,
            }
            fields.update(coverage_fields(messages, covered_message_count, content))
            if usage is not None:
                fields["usage"] = usage.model_dump()
            sp.set(**fields)
            return

    if last_error is not None:
        raise last_error
    logger.warning(
        "summary skipped: chat %s -- no target had budget for the next "
        "unsummarized message", chat_id,
    )


async def _run_summary_guarded(app: Any, chat_id: str) -> None:
    try:
        await _run_summary(app, chat_id)
    finally:
        _in_flight.discard(chat_id)


def _trigger_state(
    cfg: Any, latest: tuple[int, str | None] | None, turn_count: int
) -> dict[str, Any]:
    """Pure computation of the token-pressure trigger (or, absent usage
    data, the turn-count fallback cadence) -- shared by maybe_enqueue_summary
    (which acts on it) and the debug summary-status endpoint (which just
    displays it, so the Debug panel can show "will trigger" without
    duplicating -- and risking drifting from -- this logic."""
    min_routable = _min_routable_ctx(cfg)
    if latest is not None:
        latest_tokens, latest_model = latest
        model_entry = next((m for m in cfg.models if m.name == latest_model), None)
        fraction = cfg.background.summary_context_fraction
        candidate_ctxs = [m.ctx for m in (model_entry,) if m is not None]
        if min_routable is not None:
            candidate_ctxs.append(min_routable)
        if candidate_ctxs:
            # The smaller of "this turn's own model" and "the tightest ctx
            # the router could send the next turn to" -- see
            # _min_routable_ctx for why the latter matters even when the
            # last turn ran on a roomy model.
            threshold = min(candidate_ctxs) * fraction
            source = "token_ctx_fraction"
        else:
            threshold = cfg.background.summary_token_threshold
            source = "token_flat_fallback"
        return {
            "latest_tokens": latest_tokens,
            "latest_model": latest_model,
            "threshold_tokens": threshold,
            "will_trigger": threshold > 0 and latest_tokens >= threshold,
            "min_routable_ctx": min_routable,
            "source": source,
        }
    every_n = cfg.background.summary_every_n_turns
    return {
        "latest_tokens": None,
        "latest_model": None,
        "threshold_tokens": None,
        "will_trigger": every_n > 0 and turn_count % every_n == 0,
        "min_routable_ctx": min_routable,
        "source": "turn_count_fallback",
        "summary_every_n_turns": every_n,
    }


def _last_summary_span(conn: sqlite3.Connection, chat_id: str) -> dict[str, Any] | None:
    """The most recent `summary` span's data + timestamp, for the debug
    summary-status endpoint -- when the last regen ran, which target it
    used (device: cpu/gpu1), and whether it failed."""
    row = conn.execute(
        "SELECT s.data AS data, s.started_at AS started_at FROM spans s "
        "JOIN traces t ON t.trace_id = s.trace_id "
        "WHERE t.chat_id = ? AND s.stage = 'summary' "
        "ORDER BY s.started_at DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    if row is None or row["data"] is None:
        return None
    try:
        data = json.loads(row["data"])
    except (TypeError, ValueError):
        return None
    return {
        "started_at": row["started_at"],
        "model": data.get("model"),
        "device": data.get("device"),
        "new_message_count": data.get("new_message_count"),
        "covered_message_count": data.get("covered_message_count"),
        "time_budget_tokens": data.get("time_budget_tokens"),
        "chars": data.get("chars"),
        "error": data.get("error"),
    }


async def summary_status(app: Any, chat_id: str) -> dict[str, Any]:
    """Read-only trigger/coverage snapshot for the Debug panel (docs/FEATURES.md
    F19): current token pressure vs. the trigger threshold, whether the next
    turn would enqueue a regen, and what the last regen actually did."""
    cfg = app.state.config
    conn = app.state.db

    turn_count = await run_sync(_count_user_turns, conn, chat_id)
    latest = await run_sync(_latest_usage, conn, chat_id)
    state = _trigger_state(cfg, latest, turn_count)
    state["turn_count"] = turn_count
    messages = await run_sync(history.list_messages, conn, chat_id)
    chat_row = await run_sync(history.get_chat, conn, chat_id)
    trusted_count = await run_sync(
        trusted_covered_count,
        conn,
        chat_id,
        messages,
        chat_row["summary"] if chat_row is not None else None,
    )
    state["covered_message_count"] = trusted_count or 0
    state["last_summary"] = await run_sync(_last_summary_span, conn, chat_id)
    state["in_flight"] = chat_id in _in_flight
    return state


async def maybe_enqueue_summary(app: Any, chat_id: str) -> None:
    """Enqueue a rolling summary when real llama.cpp context pressure (or,
    absent that, the turn-count fallback cadence) says it's time."""
    try:
        cfg = app.state.config
        conn = app.state.db

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
    """Submit through the one existing tracked queue/in-flight path."""
    if chat_id in _in_flight:
        return False
    queue = get_queue(app)
    if queue is None:
        logger.warning("summary not enqueued: background queue missing")
        return False

    _in_flight.add(chat_id)

    def factory() -> Any:
        return _run_summary_guarded(app, chat_id)

    queue.submit(factory)
    return True


async def enqueue_summary_recovery(app: Any, chat_id: str) -> bool:
    """Force a tracked regen after lossless context cannot fit.

    Unlike ``maybe_enqueue_summary``, this bypasses the pressure trigger:
    the attempted prompt has already proven that recovery is required.
    It still shares the same queue and per-chat in-flight guard.
    """
    try:
        return _submit_summary(app, chat_id)
    except Exception:
        logger.exception("summary recovery enqueue failed for chat %s", chat_id)
        return False
