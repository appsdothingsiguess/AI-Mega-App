"""Minimal standalone fake llama-swap for e2e/dev use (docs/FEATURES.md F12).

This is the ci-harness agent's OWN stub, self-contained and dependency-free
w.r.t. the rest of the app, so `scripts/dev.sh` and the Playwright smoke
specs work before p1/llm-client's real `tests/fakes/fake_llama_swap.py`
exists on this branch. It serves just enough of the OpenAI-compatible
`/v1/chat/completions` surface (plain + streamed) to unblock local dev and
e2e smoke tests.

INTEGRATOR NOTE: once p1/llm-client merges its richer fake (multi-script
selection via `X-Fake-Script`, tool-call/error/slow/loading scripts —
docs/FEATURES.md F12), reconcile: either swap this module's internals to
delegate to that one, or keep this as the deliberately-dumb e2e/dev stub and
point `tests/conftest.py`'s guarded import at the real one for unit tests.
Do not silently keep two divergent fakes without a note in the PR.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="fake-llama-swap")

_CANNED_REPLY = "Hello from the fake llama-swap backend."


def _chat_completion_payload(model: str, content: str) -> dict[str, Any]:
    now = int(time.time())
    return {
        "id": "fake-chatcmpl-1",
        "object": "chat.completion",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": len(content.split()),
            "total_tokens": 8 + len(content.split()),
        },
    }


def _sse_chunks(model: str, content: str):
    now = int(time.time())
    words = content.split()
    for i, word in enumerate(words):
        piece = word + (" " if i < len(words) - 1 else "")
        chunk = {
            "id": "fake-chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": now,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    final = {
        "id": "fake-chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "chat-default", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = await request.json()
    model = body.get("model", "chat-default")
    stream = bool(body.get("stream", False))
    if stream:
        return StreamingResponse(
            _sse_chunks(model, _CANNED_REPLY), media_type="text/event-stream"
        )
    return _chat_completion_payload(model, _CANNED_REPLY)
