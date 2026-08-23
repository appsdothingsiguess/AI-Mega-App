"""Public chat-orchestrator facade and soft-import compatibility seams."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.config import Config, ModelEntry
from app.llm_client import LLMClient, LLMError
from app.types import ChatDelta, SSEEvent

from .streaming import stream_with_loading
from .turn import TurnSeams, execute_turn

try:
    from app.router import route as _route
except ImportError:
    _route = None

try:
    from app.background import (
        enqueue_summary_recovery as _enqueue_summary_recovery,
        on_turn_complete as _on_turn_complete,
    )
except ImportError:
    _on_turn_complete = None
    _enqueue_summary_recovery = None

try:
    from app.gpu.rewarm import mark_gpu0_activity as _mark_gpu0_activity
except ImportError:
    _mark_gpu0_activity = None


class ChatCompleter(Protocol):
    """The LLM client subset used by the chat turn executor."""

    def chat(
        self, model: str, messages: list[dict[str, str]], *, tools: Any = None,
        response_format: Any = None, thinking: bool | None = None,
        max_tokens: int | None = None, stream: bool = True,
    ) -> AsyncIterator[ChatDelta]: ...


FIRST_TOKEN_WARN_S = 2.0


async def _stream_with_loading(
    agen: AsyncIterator[ChatDelta], warn_s: float, timeout_s: float
) -> AsyncIterator[tuple[str, ChatDelta | None]]:
    """Compatibility alias for callers that imported the former helper."""
    async for item in stream_with_loading(agen, warn_s, timeout_s):
        yield item


class ChatOrchestrator:
    """Public facade; turn execution receives this module's soft seams."""

    def __init__(
        self, conn: sqlite3.Connection, config: Config,
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
        return next((entry for entry in self.config.models if entry.name == name), None)

    def _canonical_swap_name(self, model: str) -> str:
        entry = self._model_entry(model)
        if entry is None:
            return model
        canonical: dict[str, str] = {}
        resident: dict[str, bool] = {}
        for candidate in self.config.models:
            if not candidate.enabled:
                continue
            if candidate.file not in canonical or (candidate.resident and not resident[candidate.file]):
                canonical[candidate.file] = candidate.name
                resident[candidate.file] = candidate.resident
        return canonical.get(entry.file, model)

    async def _get_preferred_model(self) -> str | None:
        try:
            status = await self.llm_client.model_status()
        except Exception:
            return None
        gpu0 = [m.name for m in self.config.models if m.gpu == 0 and m.enabled]
        loaded = [name for name in gpu0 if status.get(name)]
        return loaded[0] if len(loaded) == 1 else None

    async def _swap_pending(self, model: str) -> bool:
        """Whether llama-swap explicitly reports this GPU0 swap slot cold.

        Absence, query failure, and a loaded status are deliberately all
        non-evidence.  In those cases a delayed first token can be prompt
        prefill, so the UI and trace must not claim a model swap happened.
        """
        entry = self._model_entry(model)
        if entry is None or entry.gpu != 0 or entry.resident:
            return False
        model_status = getattr(self.llm_client, "model_status", None)
        if model_status is None:
            return False
        try:
            status = await model_status()
        except Exception:
            return False
        canonical = self._canonical_swap_name(model)
        return status.get(canonical) is False

    async def handle_message(
        self, chat_id: str, text: str, model: str | None = None,
        attachments: list[str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        seams = TurnSeams(
            route=_route, on_turn_complete=_on_turn_complete,
            enqueue_summary_recovery=_enqueue_summary_recovery,
            mark_gpu0_activity=_mark_gpu0_activity, model_entry=self._model_entry,
            canonical_swap_name=self._canonical_swap_name,
            preferred_model=self._get_preferred_model,
            swap_pending=self._swap_pending,
            stream_with_loading=_stream_with_loading,
            first_token_warn_s=FIRST_TOKEN_WARN_S,
        )
        async for event in execute_turn(
            self.conn, self.config, self.llm_client, seams, chat_id, text, model, attachments,
        ):
            yield event
