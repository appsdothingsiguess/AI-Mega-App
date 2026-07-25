"""The turn loop (docs/FEATURES.md A4; PLAN.md §4.2). No tool logic, no
routing logic — those are Phase 2/3 modules the orchestrator calls through
marked seams; this module only resolves the model, streams a completion,
and persists the turn.
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


async def _stream_with_loading(
    agen: AsyncIterator[ChatDelta], warn_s: float, timeout_s: float
) -> AsyncIterator[tuple[str, ChatDelta | None]]:
    """Wrap a ChatDelta async iterator, yielding ("loading", None) once if no
    delta has arrived within `warn_s` (llama-swap is swapping the 3090 slot,
    PLAN.md §4.1 measured up to 12.47s cold), then ("delta", delta) for each
    chunk. Raises TimeoutError if no first token arrives within `timeout_s`
    (config.llm.first_token_timeout_s). No timeout is applied once the first
    token has arrived — only the cold-load wait is bounded here."""
    start = time.monotonic()
    warned = False
    seen_first = False
    while True:
        if seen_first:
            try:
                delta = await agen.__anext__()
            except StopAsyncIteration:
                return
            yield ("delta", delta)
            continue

        remaining = timeout_s - (time.monotonic() - start)
        if remaining <= 0:
            raise TimeoutError("first_token_timeout")
        wait = remaining if warned else min(warn_s, remaining)
        try:
            delta = await asyncio.wait_for(agen.__anext__(), timeout=wait)
        except StopAsyncIteration:
            return
        except TimeoutError:
            if warned:
                raise TimeoutError("first_token_timeout") from None
            warned = True
            yield ("loading", None)
            continue
        seen_first = True
        yield ("delta", delta)


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
        self, chat_id: str, text: str, model: str | None = None
    ) -> AsyncIterator[SSEEvent]:
        """The Phase-1 turn: resolve model -> persist user msg -> stream
        completion -> persist assistant msg. Every stage is a debug span.
        Yields SSEEvent objects only — the finally-block terminal-event
        guarantee lives in app/chat/api.py, which wraps this generator."""
        trace_id = new_trace(chat_id)
        accumulated: list[str] = []
        usage_dict: dict[str, Any] | None = None

        chat_row = await run_sync(history.get_chat, self.conn, chat_id)
        if chat_row is None:
            yield SSEEvent(
                event="error",
                data={"kind": "chat_not_found", "detail": f"no chat with id {chat_id}"},
            )
            return

        try:
            async with span(trace_id, "route", explicit_model=model) as sp:
                # --- Phase 2 router seam ---
                # Phase 2's smart router (PLAN.md §4.3: override > deterministic
                # rules > classifier) replaces this three-way `or` with a call to
                # `app.router.route(chat, text, attachments) -> RouteResult`.
                # The explicit `model` param and `chat.model_override` must keep
                # winning ahead of the classifier per the frozen layer ordering —
                # this exact line is where that call slots in.
                resolved_model = (
                    model or chat_row["model_override"] or self.config.defaults.chat_model
                )
                if model:
                    source = "explicit"
                elif chat_row["model_override"]:
                    source = "chat_override"
                else:
                    source = "default"
                sp.set(model=resolved_model, source=source)

            async with span(trace_id, "db", op="persist_user_message"):
                await run_sync(history.insert_message, self.conn, chat_id, "user", text, None)

            model_entry = self._model_entry(resolved_model)

            async with span(trace_id, "llm_request", model=resolved_model) as sp:
                messages = await run_sync(history.build_llm_messages, self.conn, chat_id)
                agen = self.llm_client.chat(
                    model=resolved_model,
                    messages=messages,
                    thinking=model_entry.thinking if model_entry else None,
                    max_tokens=model_entry.max_tokens if model_entry else 1024,
                    stream=True,
                )
                sp.set(message_count=len(messages))

            async with span(trace_id, "llm_stream", model=resolved_model) as sp:
                tokens_out = 0
                async for kind, value in _stream_with_loading(
                    agen.__aiter__(),
                    FIRST_TOKEN_WARN_S,
                    self.config.llm.first_token_timeout_s,
                ):
                    if kind == "loading":
                        yield SSEEvent(event="model_loading", data={"model": resolved_model})
                        continue
                    delta = value
                    assert delta is not None
                    if delta.content:
                        accumulated.append(delta.content)
                        tokens_out += 1
                        yield SSEEvent(event="token", data={"text": delta.content})
                    if delta.usage is not None:
                        usage_dict = delta.usage.model_dump()
                sp.set(tokens_out=tokens_out)

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

            # `done` is built as a dict later phases add keys to (route in
            # Phase 2, citations/context in Phase 3) — never a fixed literal.
            done_payload: dict[str, Any] = {
                "message_id": message_row["id"],
                "model": resolved_model,
                "usage": usage_dict,
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
