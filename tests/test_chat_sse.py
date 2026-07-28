"""Tests for the Phase-1 chat orchestrator + SSE endpoint (docs/FEATURES.md
A4; PLAN.md §4.2).

app/llm_client.py (p1/llm-client) is not yet merged into this worktree, so
`FakeLLMClient` below is the smallest stand-in matching its *declared*
signature (`async def chat(model, messages, *, tools=None,
response_format=None, thinking=None, max_tokens=None, stream=True) ->
AsyncIterator[ChatDelta]`, raising `LLMError(kind, detail)` on failure).
INTEGRATOR NOTE: llm-client's real `tests/fakes/fake_llama_swap.py` will
likely supersede this fake — reconcile at integration; this fake exists
only so this wave's tests don't depend on unmerged work.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from app.chat.orchestrator import LLMError
from app.config import (
    Config,
    DbConfig,
    DefaultsConfig,
    LlamaSwapConfig,
    LlmConfig,
    ModelEntry,
)
from app.main import create_app
from app.types import ChatDelta, Usage

GOLDEN_DIR = Path(__file__).parent / "golden"


class FakeLLMClient:
    """Matches LLMClient's declared `chat()` signature exactly. Yields a
    fixed set of content chunks, optionally delayed, optionally raising.

    `raise_error` is raised after `delay_before_first` (if any), so tests
    can reproduce the race where the connection fails after the model_loading
    warn window has already fired.
    """

    def __init__(
        self,
        chunks: list[str] | None = None,
        usage: Usage | None = None,
        delay_before_first: float = 0.0,
        raise_error: Exception | None = None,
    ) -> None:
        self.chunks = chunks if chunks is not None else ["Hello", ", ", "world!"]
        self.usage = usage
        self.delay_before_first = delay_before_first
        self.raise_error = raise_error

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        tools=None,
        response_format=None,
        thinking: bool | None = None,
        max_tokens: int | None = None,
        stream: bool = True,
    ) -> AsyncIterator[ChatDelta]:
        import asyncio

        if self.delay_before_first:
            await asyncio.sleep(self.delay_before_first)
        if self.raise_error is not None:
            raise self.raise_error
        for i, chunk in enumerate(self.chunks):
            is_last = i == len(self.chunks) - 1
            yield ChatDelta(
                content=chunk,
                finish_reason="stop" if is_last else None,
                usage=self.usage if is_last else None,
            )


def _test_config(db_path: Path, first_token_timeout_s: float = 30) -> Config:
    return Config(
        llama_swap=LlamaSwapConfig(base_url="http://127.0.0.1:8080/v1"),
        db=DbConfig(path=str(db_path)),
        models=[
            ModelEntry(
                name="chat-default",
                **{"class": "general"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
            )
        ],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="chat-default",
            title_model="chat-default",
        ),
        llm=LlmConfig(first_token_timeout_s=first_token_timeout_s),
    )


def _make_client(tmp_path: Path, llm_client, first_token_timeout_s: float = 30) -> TestClient:
    cfg = _test_config(tmp_path / "app.db", first_token_timeout_s)
    app = create_app(config=cfg)
    app.state.llm_client = llm_client
    client = TestClient(app)
    client.__enter__()
    return client


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event_line = next(line for line in lines if line.startswith("event: "))
        data_line = next(line for line in lines if line.startswith("data: "))
        events.append((event_line[len("event: ") :], json.loads(data_line[len("data: ") :])))
    return events


def test_basic_turn_matches_golden_transcript(tmp_path: Path) -> None:
    fake = FakeLLMClient(
        chunks=["Hello", ", ", "world!"],
        usage=Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
    )
    client = _make_client(tmp_path, fake)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds == ["token", "token", "token", "done"]

    done_data = events[-1][1]
    assert done_data["model"] == "chat-default"
    assert done_data["usage"] == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
    message_id = done_data["message_id"]
    trace_id = done_data["trace_id"]

    normalized = resp.text.replace(message_id, "<MESSAGE_ID>").replace(trace_id, "<TRACE_ID>")
    golden = (GOLDEN_DIR / "basic_turn.txt").read_text()
    assert normalized.strip() == golden.strip()


def test_wiring_proof_spans_exist_for_trace(tmp_path: Path) -> None:
    """End-to-end proof: create a chat, send a message, and confirm the
    turn's spans are retrievable via GET /api/debug/trace/{id} — not just a
    unit-level assertion on the orchestrator."""
    fake = FakeLLMClient(chunks=["hi"])
    client = _make_client(tmp_path, fake)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})
    events = _parse_sse(resp.text)
    done_data = next(data for ev, data in events if ev == "done")
    trace_id = done_data["trace_id"]

    trace_resp = client.get(f"/api/debug/trace/{trace_id}")
    assert trace_resp.status_code == 200
    body = trace_resp.json()
    assert body["trace_id"] == trace_id
    stages = [s["stage"] for s in body["spans"]]
    assert stages == ["route", "db", "llm_request", "llm_stream", "db", "sse_emit"]


def test_llm_error_becomes_terminal_error_event(tmp_path: Path) -> None:
    fake = FakeLLMClient(raise_error=LLMError("llm_unreachable", "connection refused"))
    client = _make_client(tmp_path, fake)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})

    events = _parse_sse(resp.text)
    assert [e for e, _ in events] == ["error"]
    assert events[0][1] == {"kind": "llm_unreachable", "detail": "connection refused"}


def test_model_loading_emitted_when_first_token_is_slow(tmp_path: Path, monkeypatch) -> None:
    import app.chat.orchestrator as orchestrator_mod

    monkeypatch.setattr(orchestrator_mod, "FIRST_TOKEN_WARN_S", 0.02)
    fake = FakeLLMClient(chunks=["slow"], delay_before_first=0.1)
    client = _make_client(tmp_path, fake, first_token_timeout_s=5)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})

    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert "model_loading" in kinds
    assert kinds[-1] == "done"
    loading_data = next(data for ev, data in events if ev == "model_loading")
    assert loading_data == {"model": "chat-default"}


def test_connection_error_after_loading_warn_is_terminal_error(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: if the backend fails after the model_loading warn window
    has fired, the stream must end with SSE `error`, not a silent empty `done`.

    Before the fix, asyncio.wait_for cancelled the in-flight __anext__ at the
    warn boundary, leaving the async generator exhausted (StopAsyncIteration)
    so the turn ended as a phantom `done` with zero tokens.
    """
    import app.chat.orchestrator as orchestrator_mod

    # Warn window is very short; backend "fails" after the warn has already fired.
    monkeypatch.setattr(orchestrator_mod, "FIRST_TOKEN_WARN_S", 0.02)
    fake = FakeLLMClient(
        delay_before_first=0.1,
        raise_error=LLMError("connection", "backend killed"),
    )
    client = _make_client(tmp_path, fake, first_token_timeout_s=5)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})

    events = _parse_sse(resp.text)
    kinds = [e for e, _ in events]
    # model_loading fires first (warn window elapsed), then error (not done).
    assert "model_loading" in kinds, f"expected model_loading in {kinds}"
    assert kinds[-1] == "error", f"expected terminal error, got {kinds}"
    error_data = next(data for ev, data in events if ev == "error")
    assert error_data["kind"] == "connection"


def test_send_message_to_unknown_chat_returns_404(tmp_path: Path) -> None:
    client = _make_client(tmp_path, FakeLLMClient())
    resp = client.post("/api/chats/does-not-exist/messages", json={"content": "hi"})
    assert resp.status_code == 404


def test_model_override_persists_and_is_used(tmp_path: Path) -> None:
    fake = FakeLLMClient(chunks=["ok"])
    client = _make_client(tmp_path, fake)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    set_resp = client.post(f"/api/chats/{chat_id}/model", json={"model": "chat-default"})
    assert set_resp.status_code == 200
    assert set_resp.json()["model_override"] == "chat-default"

    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})
    events = _parse_sse(resp.text)
    done_data = next(data for ev, data in events if ev == "done")
    assert done_data["model"] == "chat-default"

    messages = client.get(f"/api/chats/{chat_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["model"] == "chat-default"


def test_history_endpoint_lists_chats(tmp_path: Path) -> None:
    client = _make_client(tmp_path, FakeLLMClient())
    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.get("/api/chats")
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert chat_id in ids
