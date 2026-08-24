"""Pure rolling-summary trigger and target-budget policy."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_CHARS_PER_TOKEN = 3.5
_SUMMARY_PROMPT = (
    "Update the rolling summary of this conversation so that the same agent "
    "or user could resume exactly where things left off -- this is working "
    "context for continuing the task, not a synopsis for an outside reader. "
    "You are given the previous summary and the messages that happened "
    "since it was written. Produce an updated summary that preserves "
    "everything still relevant from the previous summary and folds in "
    "what's new. Output only the summary text -- no preamble or labels.\n\n"
    "Structure it with these sections, omitting any that are empty:\n"
    "- Entities and specifics stated (exact names, numbers, dates, quoted facts)\n"
    "- Decisions made and why\n- Commitments or constraints stated\n"
    "- Open questions / next steps\n\n"
    "Preserve numbers, dates, proper nouns, and quoted phrases verbatim -- "
    "do not round, generalize, or drop them even if space is tight."
)
_TIME_BUDGET_SAFETY_MARGIN_S = 15.0


def min_routable_ctx(cfg: Any) -> int | None:
    routing = getattr(cfg, "routing", None)
    if routing is None:
        return None
    names = set(routing.intents.model_dump().values())
    names.add(routing.classifier.fallback_model)
    ctxs = [m.ctx for m in cfg.models if m.name in names]
    return min(ctxs) if ctxs else None


def count_user_turns(conn: sqlite3.Connection, chat_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE chat_id = ? AND role = 'user'", (chat_id,)).fetchone()
    return int(row["n"])


def latest_usage(conn: sqlite3.Connection, chat_id: str) -> tuple[int, str | None] | None:
    row = conn.execute(
        "SELECT s.data AS data FROM spans s JOIN traces t ON t.trace_id = s.trace_id "
        "WHERE t.chat_id = ? AND s.stage = 'llm_stream' ORDER BY s.started_at DESC LIMIT 1", (chat_id,),
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


def format_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role") or "unknown"
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines)


def time_budget_tokens(timeout_s: float, prefill_tokens_per_sec: float, decode_tokens_per_sec: float, max_output_tokens: int) -> int:
    remaining_s = timeout_s - _TIME_BUDGET_SAFETY_MARGIN_S - max_output_tokens / decode_tokens_per_sec
    return 0 if remaining_s <= 0 else int(remaining_s * prefill_tokens_per_sec)


def fit_to_budget(new_messages: list[dict[str, Any]], prior: str | None, ctx: int, max_output_tokens: int, extra_budget_tokens: int | None = None) -> list[dict[str, Any]]:
    budget_tokens = ctx - max_output_tokens - ((len(_SUMMARY_PROMPT) + len(prior or "")) / _CHARS_PER_TOKEN)
    budget_tokens = max(budget_tokens, ctx // 4)
    if extra_budget_tokens is not None:
        budget_tokens = max(min(budget_tokens, extra_budget_tokens), 1)
    kept: list[dict[str, Any]] = []
    for msg in new_messages:
        candidate = kept + [msg]
        if len(format_transcript(candidate)) / _CHARS_PER_TOKEN > budget_tokens and kept:
            break
        kept = candidate
    return kept


def summary_targets(cfg: Any) -> list[tuple[Any, float, float, str]]:
    bg = cfg.background
    candidates = [(bg.summary_model_gpu, bg.summary_gpu_tokens_per_sec_prefill, bg.summary_gpu_tokens_per_sec_decode, "gpu1"), (bg.summary_model, bg.summary_cpu_tokens_per_sec_prefill, bg.summary_cpu_tokens_per_sec_decode, "cpu")]
    targets: list[tuple[Any, float, float, str]] = []
    seen: set[str] = set()
    for name, prefill, decode, device in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        entry = next((m for m in cfg.models if m.name == name and m.enabled), None)
        if entry is not None:
            targets.append((entry, prefill, decode, device))
    return targets


def trigger_state(cfg: Any, latest: tuple[int, str | None] | None, turn_count: int) -> dict[str, Any]:
    minimum = min_routable_ctx(cfg)
    if latest is not None:
        tokens, model = latest
        entry = next((m for m in cfg.models if m.name == model), None)
        candidate_ctxs = [m.ctx for m in (entry,) if m is not None]
        if minimum is not None:
            candidate_ctxs.append(minimum)
        if candidate_ctxs:
            threshold, source = min(candidate_ctxs) * cfg.background.summary_context_fraction, "token_ctx_fraction"
        else:
            threshold, source = cfg.background.summary_token_threshold, "token_flat_fallback"
        return {"latest_tokens": tokens, "latest_model": model, "threshold_tokens": threshold, "will_trigger": threshold > 0 and tokens >= threshold, "min_routable_ctx": minimum, "source": source}
    every_n = cfg.background.summary_every_n_turns
    return {"latest_tokens": None, "latest_model": None, "threshold_tokens": None, "will_trigger": every_n > 0 and turn_count % every_n == 0, "min_routable_ctx": minimum, "source": "turn_count_fallback", "summary_every_n_turns": every_n}
