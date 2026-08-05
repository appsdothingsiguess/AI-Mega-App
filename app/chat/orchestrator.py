"""The turn loop (docs/FEATURES.md A4; PLAN.md §4.2). No tool logic —
routing is delegated to app.router.route via a soft-imported seam; this
module resolves the model, streams a completion, and persists the turn.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.config import Config, ModelEntry
from app.db import run_sync
from app.debug import new_trace, span
from app.llm_client import LLMClient, LLMError
from app.types import ChatDelta, SSEEvent

from . import history

# Soft-import Phase-2 seams so this worktree merges cleanly before
# app.router / app.background land. Tests monkeypatch `_route` /
# `_on_turn_complete` on this module.
try:
    from app.router import route as _route
except ImportError:  # BLOCKED: app.router absent — interface-gate
    _route = None

try:
    from app.background import on_turn_complete as _on_turn_complete
except ImportError:  # BLOCKED: app.background absent — interface-gate
    _on_turn_complete = None


class ChatCompleter(Protocol):
    """The subset of LLMClient.chat's declared signature the orchestrator
    depends on — lets tests inject a fake without importing app.llm_client."""

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        tools: Any = None,
        response_format: Any = None,
        thinking: bool | None = None,
        max_tokens: int | None = None,
        stream: bool = True,
    ) -> AsyncIterator[ChatDelta]: ...


FIRST_TOKEN_WARN_S = 2.0  # PLAN.md §4.2: model_loading fires past this


def _render_prompt(messages: list[dict[str, str]]) -> str:
    """Flatten wire messages into the readable transcript the Debug view's
    prompt tab shows. Only ever reaches a span when debug.store_prompts is
    on (app/debug/trace.py filters `prompt`)."""
    return "\n\n".join(
        f"[{m.get('role', '?')}]\n{m.get('content', '')}" for m in messages
    )


async def _stream_with_loading(
    agen: AsyncIterator[ChatDelta], warn_s: float, timeout_s: float
) -> AsyncIterator[tuple[str, ChatDelta | None]]:
    """Wrap a ChatDelta async iterator, yielding ("loading", None) once if no
    delta has arrived within `warn_s` (llama-swap is swapping the 3090 slot,
    PLAN.md §4.1 measured up to 12.47s cold), then ("delta", delta) for each
    chunk. Raises TimeoutError if no first token arrives within `timeout_s`
    (config.llm.first_token_timeout_s). No timeout is applied once the first
    token has arrived — only the cold-load wait is bounded here.

    Implementation note: the pending __anext__ Task is kept alive across the
    warn window rather than cancelled. asyncio.wait_for cancels the inner task
    when its timeout fires, which aborts the in-flight httpx connection before
    LLMClient can surface the real error — producing a silent StopAsyncIteration
    instead of an LLMError. asyncio.wait() leaves the task running so a late
    connection failure propagates correctly as an LLMError (→ SSE error event).
    """
    start = time.monotonic()
    warned = False

    # Hold the pending first-token task across warn windows so it is never
    # cancelled prematurely (see implementation note above).
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
                # Warn window elapsed; task still in flight.
                if warned:
                    raise TimeoutError("first_token_timeout")
                warned = True
                yield ("loading", None)
                continue

            # Task completed (success, StopAsyncIteration, or exception).
            pending_task = None
            try:
                delta = done.pop().result()
            except StopAsyncIteration:
                return
            # LLMError and any other exception propagate to orchestrator's
            # outer try/except, which emits the terminal SSE error event.
            yield ("delta", delta)
            break  # first token received; hand off to the simple loop below

        # After first token the stream is flowing — no need for task wrappers.
        async for delta in agen:
            yield ("delta", delta)

    finally:
        if pending_task is not None and not pending_task.done():
            pending_task.cancel()
            try:
                await pending_task
            except (asyncio.CancelledError, Exception):
                pass


class ChatOrchestrator:
    """One instance per request (cheap — holds only a DB connection, config,
    and an LLMClient). `handle_message` is the entire Phase-1 turn loop."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        config: Config,
        llm_client: ChatCompleter | None = None,
    ) -> None:
        self.conn = conn
        self.config = config
        self.llm_client = llm_client if llm_client is not None else self._default_llm_client()

    def _default_llm_client(self) -> ChatCompleter:
        return LLMClient(
            base_url=self.config.llama_swap.base_url,
            timeout_s=self.config.llama_swap.timeout_s,
        )

    def _model_entry(self, name: str) -> ModelEntry | None:
        for entry in self.config.models:
            if entry.name == name:
                return entry
        return None

    async def handle_message(
        self,
        chat_id: str,
        text: str,
        model: str | None = None,
        attachments: list[str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Resolve model -> persist user msg -> stream completion -> persist
        assistant msg. Every stage is a debug span. Yields SSEEvent objects
        only — the finally-block terminal-event guarantee lives in
        app/chat/api.py, which wraps this generator."""
        attachments = attachments or []
        trace_id = new_trace(chat_id)
        accumulated: list[str] = []
        usage_dict: dict[str, Any] | None = None
        timings_dict: dict[str, Any] | None = None
        route_info: dict[str, Any]
        route_extra: dict[str, Any] = {}

        chat_row = await run_sync(history.get_chat, self.conn, chat_id)
        if chat_row is None:
            yield SSEEvent(
                event="error",
                data={"kind": "chat_not_found", "detail": f"no chat with id {chat_id}"},
            )
            return

        try:
            async with span(trace_id, "route", explicit_model=model) as sp:
                # PLAN.md §4.3: override > rules > classifier. Explicit
                # `model` and `chat.model_override` both map to source
                # "override" (frozen RouteResult.source set).
                if model or chat_row["model_override"]:
                    resolved_model = model or chat_row["model_override"]
                    route_info = {
                        "model": resolved_model,
                        "source": "override",
                        "intent": "manual",
                        "confidence": None,
                    }
                    route_extra = {"layer": "override"}
                elif _route is not None:
                    # `details` carries the *why* (winning layer, fallback
                    # reason, classifier prompt/response) that RouteResult's
                    # frozen shape can't; without it a classifier timeout
                    # looks identical to a confident `chat` in the Debug view.
                    route_details: dict[str, Any] = {}
                    result = await _route(
                        chat_row,
                        text,
                        attachments,
                        llm_client=self.llm_client,
                        config=self.config,
                        details=route_details,
                    )
                    resolved_model = result.model
                    route_info = result.model_dump()
                    route_extra = route_details
                else:
                    # BLOCKED: app.router absent — degraded path uses
                    # defaults.chat_model with frozen-set source
                    # "classifier" so tests can monkeypatch `_route`.
                    resolved_model = self.config.defaults.chat_model
                    route_info = {
                        "model": resolved_model,
                        "source": "classifier",
                        "intent": "chat",
                        "confidence": None,
                    }
                    route_extra = {
                        "layer": "classifier",
                        "fallback_reason": "router_unavailable",
                    }
                sp.set(**route_info, **route_extra)

            async with span(trace_id, "db", op="persist_user_message"):
                await run_sync(history.insert_message, self.conn, chat_id, "user", text, None)

            model_entry = self._model_entry(resolved_model)

            async with span(trace_id, "llm_request", model=resolved_model) as sp:
                messages = await run_sync(history.build_llm_messages, self.conn, chat_id)
                # Stopgap: prepend a system prompt so local models understand
                # they're a persistent assistant (full app/prompts/ is Phase 3+).
                _SYSTEM_MSG: dict[str, str] = {
                    "role": "system",
                    "content": (
                        "You are a helpful AI assistant. "
                        "You have access to the full conversation history. "
                        "Answer the user's questions directly and helpfully."
                    ),
                }
                messages = [_SYSTEM_MSG] + messages
                agen = self.llm_client.chat(
                    model=resolved_model,
                    messages=messages,
                    thinking=model_entry.thinking if model_entry else None,
                    max_tokens=model_entry.max_tokens if model_entry else 1024,
                    stream=True,
                )
                # `messages`/`prompt` are dropped by app/debug/trace.py unless
                # debug.store_prompts is true. The Debug view's prompt tab
                # reads `prompt`, so send the exact wire messages *and* a
                # readable rendering of them (docs/FEATURES.md F19: "exactly
                # what each model was sent").
                sp.set(
                    message_count=len(messages),
                    thinking=model_entry.thinking if model_entry else None,
                    max_tokens=model_entry.max_tokens if model_entry else 1024,
                    messages=messages,
                    prompt=_render_prompt(messages),
                )

            async with span(trace_id, "llm_stream", model=resolved_model) as sp:
                tokens_out = 0
                # A model_loading warn means llama-swap is loading/swapping
                # the slot; bracket that wait in its own swap_wait span so the
                # Debug view can show the swap badge (docs/FEATURES.md F1/F19).
                swap_span = None
                try:
                    async for kind, value in _stream_with_loading(
                        agen.__aiter__(),
                        FIRST_TOKEN_WARN_S,
                        self.config.llm.first_token_timeout_s,
                    ):
                        if kind == "loading":
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
                        if delta.content:
                            accumulated.append(delta.content)
                            tokens_out += 1
                            yield SSEEvent(event="token", data={"text": delta.content})
                        if delta.usage is not None:
                            usage_dict = delta.usage.model_dump()
                        if delta.timings is not None:
                            timings_dict = delta.timings
                finally:
                    if swap_span is not None:
                        await swap_span.__aexit__(None, None, None)
                # usage/timings come from llama.cpp itself — never estimated
                # client-side (PLAN.md §4.16).
                sp.set(
                    tokens_out=tokens_out,
                    usage=usage_dict,
                    timings=timings_dict,
                    response="".join(accumulated),
                )

            async with span(trace_id, "db", op="persist_assistant_message"):
                message_row = await run_sync(
                    history.insert_message,
                    self.conn,
                    chat_id,
                    "assistant",
                    "".join(accumulated),
                    resolved_model,
                )
                await run_sync(history.touch_chat, self.conn, chat_id)

            if _on_turn_complete is not None:
                await _on_turn_complete(chat_id)

            # `done` is built as a dict later phases add keys to (route,
            # citations/context) — never a fixed literal.
            done_payload: dict[str, Any] = {
                "message_id": message_row["id"],
                "model": resolved_model,
                "usage": usage_dict,
                "route": {
                    "source": route_info["source"],
                    "intent": route_info["intent"],
                    "model": resolved_model,
                    "confidence": route_info.get("confidence"),
                },
                # Not in the frozen 3-field literal (PLAN.md §4.2) but the
                # dict is explicitly meant for later stages to add keys to;
                # trace_id is what makes the turn's spans discoverable via
                # GET /api/debug/trace/{id} (the ACCEPTANCE wiring proof).
                "trace_id": trace_id,
            }
            async with span(trace_id, "sse_emit", event="done"):
                yield SSEEvent(event="done", data=done_payload)

        except LLMError as exc:
            async with span(trace_id, "sse_emit", event="error", kind=exc.kind):
                yield SSEEvent(event="error", data={"kind": exc.kind, "detail": exc.detail})
        except TimeoutError as exc:
            async with span(trace_id, "sse_emit", event="error", kind="first_token_timeout"):
                yield SSEEvent(
                    event="error", data={"kind": "first_token_timeout", "detail": str(exc)}
                )
        except Exception as exc:  # noqa: BLE001 - last-resort terminal error
            async with span(trace_id, "sse_emit", event="error", kind="internal_error"):
                yield SSEEvent(event="error", data={"kind": "internal_error", "detail": str(exc)})
