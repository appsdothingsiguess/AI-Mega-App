"""Reusable in-process fake of llama-swap's OpenAI-compatible surface:
POST /v1/chat/completions (streaming + non-streaming), POST /v1/embeddings,
GET /v1/models. Every Phase-1+ agent's tests import this to exercise
app/llm_client.py — and later the chat orchestrator — with no real network
and no GPU (PLAN.md §4.10, docs/FEATURES.md F1).

Usage
-----
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=["Hel", "lo"], usage={"prompt_tokens": 5,
                                                           "completion_tokens": 2})
    transport = httpx.ASGITransport(app=fake.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/v1/") as c:
        resp = await c.post("chat/completions", json={...})

`LLMClient` builds its own httpx.AsyncClient internally, so tests typically
swap it in after construction:
    client = LLMClient(base_url="http://test/v1/", timeout_s=5)
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url="http://test/v1/", timeout=5
    )

Each `script_*` call queues exactly one canned response, consumed by the
next matching request (FIFO); queue several to script a sequence of calls.
If the queue is empty, a request gets a bland default response rather than
an error, so tests that don't care about the exact content still pass.
`fake.chat_requests` / `fake.embed_requests` record every raw request body
in arrival order, for assertions on what the client actually sent.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


@dataclass
class ScriptedChat:
    """One canned /v1/chat/completions response.

    content_chunks: streamed as successive `delta.content` fragments in
        stream mode, or joined into one `message.content` in non-stream mode.
    tool_calls: list of {"index", "id", "name", "arguments"} dicts. In
        stream mode each entry is emitted as its own delta fragment; give an
        entry an "arg_chunks" list (list[str]) to split its arguments across
        several fragments sharing one index — `name`/`id` are sent only on
        the first fragment, matching real incremental tool-call streaming.
    finish_reason, usage, timings: attached to the final chunk (stream) or
        the response body (non-stream).
    status_code: >=400 simulates an HTTP error from llama-swap.
    delay_s: sleep before responding at all (before headers/body) — pair
        with a short client timeout_s to exercise the timeout -> LLMError
        path.
    """

    content_chunks: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = "stop"
    usage: dict[str, int] | None = None
    # llama.cpp's own `timings` block, attached alongside `usage` on the
    # final chunk / response body — the only source of real tok/s.
    timings: dict[str, Any] | None = None
    status_code: int = 200
    delay_s: float = 0.0
    error_body: str = "internal error"


@dataclass
class ScriptedEmbeddings:
    """One canned /v1/embeddings response."""

    vectors: list[list[float]]
    status_code: int = 200
    error_body: str = "internal error"


class FakeLlamaSwap:
    """In-process ASGI stand-in for llama-swap. Not a real server — mount
    `.app` behind httpx.ASGITransport, never bind a socket."""

    def __init__(self) -> None:
        self._chat_queue: list[ScriptedChat] = []
        self._embed_queue: list[ScriptedEmbeddings] = []
        self._models: list[str] = ["chat-default"]
        self._model_status: dict[str, str] = {}
        self.chat_requests: list[dict] = []
        self.embed_requests: list[dict] = []
        self.app: FastAPI = self._build_app()

    def script_chat(self, **kwargs: Any) -> None:
        self._chat_queue.append(ScriptedChat(**kwargs))

    def script_embeddings(self, vectors: list[list[float]], **kwargs: Any) -> None:
        self._embed_queue.append(ScriptedEmbeddings(vectors=vectors, **kwargs))

    def script_models(self, names: list[str]) -> None:
        self._models = list(names)

    def script_model_status(self, status_map: dict[str, str]) -> None:
        """Set per-model status values (e.g. {"chat-default": "loaded"})."""
        self._model_status = dict(status_map)

    def _next_chat(self) -> ScriptedChat:
        return self._chat_queue.pop(0) if self._chat_queue else ScriptedChat(content_chunks=["ok"])

    def _next_embed(self) -> ScriptedEmbeddings:
        if self._embed_queue:
            return self._embed_queue.pop(0)
        return ScriptedEmbeddings(vectors=[[0.0]])

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def chat_completions(request: Request):  # type: ignore[no-untyped-def]
            body = await request.json()
            self.chat_requests.append(body)
            script = self._next_chat()
            if script.delay_s:
                await asyncio.sleep(script.delay_s)
            if script.status_code >= 400:
                return JSONResponse(
                    status_code=script.status_code, content={"error": script.error_body}
                )
            if body.get("stream", True):
                return StreamingResponse(_stream_chat(script), media_type="text/event-stream")
            return JSONResponse(content=_full_chat_body(script))

        @app.post("/v1/embeddings")
        async def embeddings(request: Request):  # type: ignore[no-untyped-def]
            body = await request.json()
            self.embed_requests.append(body)
            script = self._next_embed()
            if script.status_code >= 400:
                return JSONResponse(
                    status_code=script.status_code, content={"error": script.error_body}
                )
            data = [
                {"index": i, "object": "embedding", "embedding": vec}
                for i, vec in enumerate(script.vectors)
            ]
            return JSONResponse(content={"object": "list", "data": data})

        @app.get("/v1/models")
        async def models():  # type: ignore[no-untyped-def]
            data = []
            for name in self._models:
                entry: dict[str, Any] = {"id": name, "object": "model"}
                if name in self._model_status:
                    entry["status"] = {"value": self._model_status[name]}
                data.append(entry)
            return JSONResponse(content={"object": "list", "data": data})

        return app


def _full_chat_body(script: ScriptedChat) -> dict:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(script.content_chunks) or None,
    }
    if script.tool_calls:
        message["tool_calls"] = [
            {
                "index": tc.get("index", i),
                "id": tc.get("id"),
                "type": "function",
                "function": {"name": tc.get("name"), "arguments": tc.get("arguments", "")},
            }
            for i, tc in enumerate(script.tool_calls)
        ]
    body: dict[str, Any] = {
        "choices": [{"index": 0, "message": message, "finish_reason": script.finish_reason}]
    }
    if script.usage:
        body["usage"] = script.usage
    if script.timings:
        body["timings"] = script.timings
    return body


async def _stream_chat(script: ScriptedChat) -> AsyncIterator[str]:
    # Role preamble chunk, matching real servers — carries no content or
    # tool-call info, exercised by LLMClient's "skip empty chunk" path.
    yield _sse({"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
    for piece in script.content_chunks:
        yield _sse({"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
    for tc in script.tool_calls:
        arg_chunks = tc.get("arg_chunks") or [tc.get("arguments", "")]
        for j, arg_piece in enumerate(arg_chunks):
            function: dict[str, Any] = {"arguments": arg_piece}
            if j == 0:
                function["name"] = tc.get("name")
            delta_tc: dict[str, Any] = {"index": tc.get("index", 0), "function": function}
            if j == 0 and tc.get("id"):
                delta_tc["id"] = tc.get("id")
            chunk = {
                "choices": [
                    {"index": 0, "delta": {"tool_calls": [delta_tc]}, "finish_reason": None}
                ]
            }
            yield _sse(chunk)
    final_chunk: dict[str, Any] = {
        "choices": [{"index": 0, "delta": {}, "finish_reason": script.finish_reason}]
    }
    if script.usage:
        final_chunk["usage"] = script.usage
    if script.timings:
        final_chunk["timings"] = script.timings
    yield _sse(final_chunk)
    yield "data: [DONE]\n\n"


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"
