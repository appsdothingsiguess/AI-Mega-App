"""Chat turn execution; dependencies are injected by the orchestrator facade."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.background.summary_coverage import trusted_covered_count
from app.config import Config, ModelEntry
from app.db import run_sync
from app.debug import new_trace, span
from app.llm_client import LLMError
from app.types import ChatDelta, SSEEvent

from . import history
from .context import assemble_context, render_prompt


@dataclass
class TurnSeams:
    route: Any
    on_turn_complete: Any
    enqueue_summary_recovery: Any
    mark_gpu0_activity: Any
    model_entry: Callable[[str], ModelEntry | None]
    canonical_swap_name: Callable[[str], str]
    preferred_model: Callable[[], Awaitable[str | None]]
    swap_pending: Callable[[str], Awaitable[bool]]
    stream_with_loading: Callable[..., AsyncIterator[tuple[str, ChatDelta | None]]]
    first_token_warn_s: float


@dataclass
class _StreamState:
    content: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    timings: dict[str, Any] | None = None


async def _resolve_route(
    seams: TurnSeams, config: Config, llm_client: Any, chat_row: Any,
    text: str, attachments: list[str], model: str | None, trace_id: str,
) -> tuple[str, dict[str, Any]]:
    extra: dict[str, Any] = {}
    async with span(trace_id, "route", explicit_model=model) as sp:
        if model or chat_row["model_override"]:
            resolved = model or chat_row["model_override"]
            info = {"model": resolved, "source": "override", "intent": "manual", "confidence": None}
            extra = {"layer": "override"}
        elif seams.route is not None:
            details: dict[str, Any] = {}
            result = await seams.route(
                chat_row, text, attachments, llm_client=llm_client, config=config,
                details=details, preferred_model=await seams.preferred_model(),
            )
            resolved, info, extra = result.model, result.model_dump(), details
        else:
            resolved = config.defaults.chat_model
            info = {"model": resolved, "source": "classifier", "intent": "chat", "confidence": None}
            extra = {"layer": "classifier", "fallback_reason": "router_unavailable"}
        sp.set(**info, **extra)
    return resolved, info


async def _request_completion(
    conn: sqlite3.Connection, config: Config, llm_client: Any, seams: TurnSeams,
    chat_id: str, resolved_model: str, trace_id: str,
) -> tuple[AsyncIterator[ChatDelta] | None, ModelEntry | None, bool]:
    entry = seams.model_entry(resolved_model)
    gpu = entry.gpu if entry else None
    agen: AsyncIterator[ChatDelta] | None = None
    refused = False
    async with span(trace_id, "llm_request", model=resolved_model, gpu=gpu) as sp:
        raw = await run_sync(history.list_messages, conn, chat_id)
        row = await run_sync(history.get_chat, conn, chat_id)
        summary = row["summary"] if row is not None else None
        covered = await run_sync(trusted_covered_count, conn, chat_id, raw, summary)
        ctx = entry.ctx if entry else 8192
        max_tokens = entry.max_tokens if entry else 1024
        assembled = assemble_context(raw, summary, covered, ctx, max_tokens)
        messages = assembled.messages
        refused = messages is None
        if messages is not None:
            agen = llm_client.chat(
                model=seams.canonical_swap_name(resolved_model), messages=messages,
                thinking=entry.thinking if entry else None, max_tokens=max_tokens, stream=True,
            )
        sp.set(
            message_count=len(messages) if messages is not None else len(raw) + 1,
            thinking=entry.thinking if entry else None, max_tokens=max_tokens, messages=messages,
            prompt=render_prompt(messages) if messages is not None else None,
            context_fit=assembled.fits, context_budget_tokens=assembled.budget_tokens,
            estimated_prompt_tokens=assembled.estimated_prompt_tokens,
            covered_message_count=covered,
        )
    return agen, entry, refused


async def _stream_completion(
    agen: AsyncIterator[ChatDelta], config: Config, seams: TurnSeams, entry: ModelEntry | None,
    resolved_model: str, trace_id: str, state: _StreamState,
) -> AsyncIterator[SSEEvent]:
    gpu = entry.gpu if entry else None
    async with span(trace_id, "llm_stream", model=resolved_model, gpu=gpu) as sp:
        tokens_out = 0
        first_token_delayed = False
        rewarm_reported = False
        swap_span = None
        # A slow first token alone is not lifecycle evidence: a loaded model
        # can spend a long time prefilling a large prompt.  Snapshot
        # llama-swap's explicit unloaded status before the request instead.
        swap_pending = await seams.swap_pending(resolved_model)
        try:
            async for kind, value in seams.stream_with_loading(
                agen.__aiter__(), seams.first_token_warn_s, config.llm.first_token_timeout_s,
            ):
                if kind == "loading":
                    first_token_delayed = True
                    if swap_pending:
                        if swap_span is None:
                            swap_span = span(trace_id, "swap_wait", model=resolved_model)
                            await swap_span.__aenter__()
                        yield SSEEvent(event="model_loading", data={"model": resolved_model})
                    continue
                if swap_span is not None:
                    await swap_span.__aexit__(None, None, None)
                    swap_span = None
                delta = value
                assert delta is not None
                if bool(delta.content or delta.reasoning_content or delta.tool_calls) and not first_token_delayed and not rewarm_reported:
                    rewarm_reported = True
                    if seams.mark_gpu0_activity is not None:
                        try:
                            seams.mark_gpu0_activity(resolved_model, config)
                        except Exception:
                            pass
                if delta.content:
                    state.content.append(delta.content)
                    tokens_out += 1
                    yield SSEEvent(event="token", data={"text": delta.content})
                if delta.reasoning_content:
                    state.reasoning.append(delta.reasoning_content)
                if delta.usage is not None:
                    state.usage = delta.usage.model_dump()
                if delta.timings is not None:
                    state.timings = delta.timings
        finally:
            if swap_span is not None:
                await swap_span.__aexit__(None, None, None)
        sp.set(tokens_out=tokens_out, usage=state.usage, timings=state.timings,
               response="".join(state.content),
               reasoning="".join(state.reasoning) if state.reasoning else None)


async def execute_turn(
    conn: sqlite3.Connection, config: Config, llm_client: Any, seams: TurnSeams,
    chat_id: str, text: str, model: str | None, attachments: list[str] | None,
) -> AsyncIterator[SSEEvent]:
    attachments = attachments or []
    trace_id = new_trace(chat_id)
    state = _StreamState()
    chat_row = await run_sync(history.get_chat, conn, chat_id)
    if chat_row is None:
        yield SSEEvent(event="error", data={"kind": "chat_not_found", "detail": f"no chat with id {chat_id}"})
        return
    persisted = False
    resolved_model = ""
    try:
        resolved_model, route_info = await _resolve_route(
            seams, config, llm_client, chat_row, text, attachments, model, trace_id,
        )
        async with span(trace_id, "db", op="persist_user_message"):
            await run_sync(history.insert_message, conn, chat_id, "user", text, None)
        agen, entry, refused = await _request_completion(
            conn, config, llm_client, seams, chat_id, resolved_model, trace_id,
        )
        if refused:
            if seams.enqueue_summary_recovery is not None:
                try:
                    await seams.enqueue_summary_recovery(chat_id)
                except Exception:
                    pass
            async with span(trace_id, "sse_emit", event="error", kind="context_overflow"):
                yield SSEEvent(event="error", data={"kind": "context_overflow", "detail": "conversation history cannot fit this model safely; summary recovery was queued"})
            return
        assert agen is not None
        async for event in _stream_completion(agen, config, seams, entry, resolved_model, trace_id, state):
            yield event
        async with span(trace_id, "db", op="persist_assistant_message"):
            row = await run_sync(history.insert_message, conn, chat_id, "assistant", "".join(state.content), resolved_model)
            await run_sync(history.touch_chat, conn, chat_id)
            persisted = True
        if seams.on_turn_complete is not None:
            await seams.on_turn_complete(chat_id)
        payload: dict[str, Any] = {
            "message_id": row["id"], "model": resolved_model, "usage": state.usage,
            "timings": state.timings,
            "route": {"source": route_info["source"], "intent": route_info["intent"], "model": resolved_model, "confidence": route_info.get("confidence")},
            "trace_id": trace_id,
        }
        async with span(trace_id, "sse_emit", event="done"):
            yield SSEEvent(event="done", data=payload)
    except LLMError as exc:
        async with span(trace_id, "sse_emit", event="error", kind=exc.kind):
            yield SSEEvent(event="error", data={"kind": exc.kind, "detail": exc.detail})
        if seams.on_turn_complete is not None:
            try:
                await seams.on_turn_complete(chat_id)
            except Exception:
                pass
    except TimeoutError as exc:
        async with span(trace_id, "sse_emit", event="error", kind="first_token_timeout"):
            yield SSEEvent(event="error", data={"kind": "first_token_timeout", "detail": str(exc)})
        if seams.on_turn_complete is not None:
            try:
                await seams.on_turn_complete(chat_id)
            except Exception:
                pass
    except Exception as exc:
        async with span(trace_id, "sse_emit", event="error", kind="internal_error"):
            yield SSEEvent(event="error", data={"kind": "internal_error", "detail": str(exc)})
        if seams.on_turn_complete is not None:
            try:
                await seams.on_turn_complete(chat_id)
            except Exception:
                pass
    finally:
        if not persisted and state.content:
            try:
                await run_sync(history.insert_message, conn, chat_id, "assistant", "".join(state.content), resolved_model or None)
                await run_sync(history.touch_chat, conn, chat_id)
            except Exception:
                pass
