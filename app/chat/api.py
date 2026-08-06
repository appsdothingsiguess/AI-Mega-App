"""Chat REST + SSE endpoints (docs/FEATURES.md A4; PLAN.md §4.2 — the
frozen chat contract). Owns the SSE encoder and the terminal-event
guarantee: every stream ends with exactly one `done` or one `error`,
enforced in a `finally` block here (not inside the orchestrator's own
generator, since Python forbids yielding from a `finally` once a consumer
has closed the generator via GeneratorExit).
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import Config

from . import history
from .orchestrator import ChatOrchestrator

router = APIRouter(prefix="/api/chats", tags=["chat"])


class CreateChatRequest(BaseModel):
    project_id: str | None = None


class ChatIdOut(BaseModel):
    id: str


class ChatSummaryOut(BaseModel):
    id: str
    title: str | None
    updated_at: int
    summary: str | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    model: str | None
    created_at: int


class SendMessageRequest(BaseModel):
    content: str
    attachments: list[str] = []
    model: str | None = None


class SetModelRequest(BaseModel):
    model: str | None = None


def _conn(request: Request) -> sqlite3.Connection:
    return request.app.state.db


def _config(request: Request) -> Config:
    return request.app.state.config


@router.post("", response_model=ChatIdOut)
async def create_chat(body: CreateChatRequest, request: Request) -> ChatIdOut:
    from app.db import run_sync

    row = await run_sync(history.create_chat, _conn(request), body.project_id)
    return ChatIdOut(id=row["id"])


@router.get("", response_model=list[ChatSummaryOut])
async def list_chats(request: Request) -> list[ChatSummaryOut]:
    from app.db import run_sync

    rows = await run_sync(history.list_chats, _conn(request))
    return [ChatSummaryOut(**row) for row in rows]


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def get_messages(chat_id: str, request: Request) -> list[MessageOut]:
    from app.db import run_sync

    conn = _conn(request)
    chat_row = await run_sync(history.get_chat, conn, chat_id)
    if chat_row is None:
        raise HTTPException(status_code=404, detail=f"no chat with id {chat_id}")
    rows = await run_sync(history.list_messages, conn, chat_id)
    return [MessageOut(**row) for row in rows]


@router.post("/{chat_id}/model")
async def set_model(chat_id: str, body: SetModelRequest, request: Request) -> dict[str, str | None]:
    from app.db import run_sync

    conn = _conn(request)
    chat_row = await run_sync(history.get_chat, conn, chat_id)
    if chat_row is None:
        raise HTTPException(status_code=404, detail=f"no chat with id {chat_id}")
    await run_sync(history.set_model_override, conn, chat_id, body.model)
    return {"model_override": body.model}


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: str, body: SendMessageRequest, request: Request
) -> StreamingResponse:
    from app.db import run_sync

    conn = _conn(request)
    chat_row = await run_sync(history.get_chat, conn, chat_id)
    if chat_row is None:
        raise HTTPException(status_code=404, detail=f"no chat with id {chat_id}")

    config = _config(request)
    llm_client = getattr(request.app.state, "llm_client", None)
    orchestrator = ChatOrchestrator(conn, config, llm_client=llm_client)

    async def event_stream() -> AsyncIterator[str]:
        terminal_sent = False
        try:
            async for event in orchestrator.handle_message(chat_id, body.content, body.model):
                if event.event in ("done", "error"):
                    terminal_sent = True
                yield event.encode()
        finally:
            # The golden rule (PLAN.md §4.2, old Bug 2): a stream that ends
            # any other way is a bug. This is the single enforcement point.
            if not terminal_sent:
                from app.types import SSEEvent

                fallback = SSEEvent(
                    event="error",
                    data={
                        "kind": "stream_incomplete",
                        "detail": "stream ended without a terminal event",
                    },
                )
                yield fallback.encode()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
