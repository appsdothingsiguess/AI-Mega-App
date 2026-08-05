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
        timings: dict | None = None,
    ) -> None:
        self.chunks = chunks if chunks is not None else ["Hello", ", ", "world!"]
        self.usage = usage
        self.delay_before_first = delay_before_first
        self.raise_error = raise_error
        self.timings = timings
        self.seen_messages: list[dict[str, str]] | None = None
        self.last_model: str | None = None

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

        self.seen_messages = messages
        self.last_model = model
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
                timings=self.timings if is_last else None,
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
                file="/models/chat-default.gguf",
                quant="Q4_K_M",
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


def test_no_override_forwards_llm_client_and_config_to_router(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: the orchestrator's call to app.router.route() must pass
    llm_client and config, not just (chat, text, attachments). Omitting
    them silently defaults llm_client to None inside route(), which skips
    the classifier layer entirely on every real turn -- every message
    routes to routing.classifier.fallback_model regardless of content,
    with source="classifier"/confidence=None/near-zero latency (looks like
    a real classifier decision in the debug panel but isn't one)."""
    import app.chat.orchestrator as orchestrator_mod
    from app.types import RouteResult

    captured: dict = {}

    async def fake_route(chat, text, attachments, **kwargs):
        captured.update(kwargs)
        # The router reports *why* it decided through this out-dict; the
        # orchestrator must merge it into its own route span.
        details = kwargs.get("details")
        if details is not None:
            details.update({"layer": "classifier"})
        return RouteResult(
            model="chat-default", source="classifier", intent="chat",
            latency_ms=1.0, confidence=0.9,
        )

    monkeypatch.setattr(orchestrator_mod, "_route", fake_route)

    fake = FakeLLMClient(chunks=["ok"])
    client = _make_client(tmp_path, fake)
    chat_id = client.post("/api/chats", json={}).json()["id"]
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})

    assert captured["llm_client"] is fake
    assert captured["config"] is not None
    assert "details" in captured


def test_history_endpoint_lists_chats(tmp_path: Path) -> None:
    client = _make_client(tmp_path, FakeLLMClient())
    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.get("/api/chats")
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert chat_id in ids


# ---------------------------------------------------------------------------
# Debug observability: the Debug view's prompt/response tabs and tok/s
# readout are fed from span data. If nothing writes those fields the tabs
# are permanently empty, which is how it shipped (docs/HANDOFF.md).
# ---------------------------------------------------------------------------


def _spans_by_stage(client: TestClient, trace_id: str) -> dict:
    body = client.get(f"/api/debug/trace/{trace_id}").json()
    out: dict = {}
    for s in body["spans"]:
        out.setdefault(s["stage"], []).append(s["data"])
    return out


def _run_turn(client: TestClient, content: str = "hi") -> str:
    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": content})
    events = _parse_sse(resp.text)
    return next(data for ev, data in events if ev == "done")["trace_id"]


def test_llm_spans_record_prompt_and_response(tmp_path: Path) -> None:
    """llm_request records exactly what the model was sent; llm_stream
    records what came back (docs/FEATURES.md F19)."""
    fake = FakeLLMClient(chunks=["ans", "wer"])
    client = _make_client(tmp_path, fake)
    trace_id = _run_turn(client, "what is 2+2")

    spans = _spans_by_stage(client, trace_id)
    request = spans["llm_request"][0]
    assert "what is 2+2" in request["prompt"]
    assert request["messages"][-1]["content"] == "what is 2+2"
    assert spans["llm_stream"][0]["response"] == "answer"


def test_prompt_fields_dropped_when_store_prompts_is_false(
    tmp_path: Path, monkeypatch
) -> None:
    """The toggle has to actually gate the new fields — otherwise turning it
    off no longer means what it says."""
    import app.debug.trace as trace_mod

    cfg = _test_config(tmp_path / "app.db")
    cfg.debug.store_prompts = False
    # trace.py reads the *global* config, not app.state.config.
    monkeypatch.setattr(trace_mod, "get_config", lambda: cfg)

    fake = FakeLLMClient(chunks=["answer"])
    client = _make_client(tmp_path, fake)
    trace_id = _run_turn(client)

    spans = _spans_by_stage(client, trace_id)
    assert "prompt" not in spans["llm_request"][0]
    assert "messages" not in spans["llm_request"][0]
    assert "response" not in spans["llm_stream"][0]
    # non-prompt fields still recorded
    assert spans["llm_request"][0]["message_count"] == 1


def test_llm_stream_span_records_server_usage_and_timings(tmp_path: Path) -> None:
    """tok/s in the Debug view must come from llama.cpp's own numbers
    (PLAN.md §4.16) — so they have to reach the span at all."""
    timings = {"predicted_per_second": 42.5, "prompt_per_second": 900.0}
    fake = FakeLLMClient(
        chunks=["hi"],
        usage=Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        timings=timings,
    )
    client = _make_client(tmp_path, fake)
    trace_id = _run_turn(client)

    data = _spans_by_stage(client, trace_id)["llm_stream"][0]
    assert data["usage"]["completion_tokens"] == 3
    assert data["timings"]["predicted_per_second"] == 42.5


def test_route_span_records_why_not_just_what(tmp_path: Path, monkeypatch) -> None:
    """The route span must carry the winning layer and any fallback reason,
    not only the decision — a classifier timeout and a confident `chat`
    answer are otherwise identical in the Debug view."""
    import app.chat.orchestrator as orchestrator_mod
    from app.types import RouteResult

    async def fake_route(chat, text, attachments, **kwargs):
        details = kwargs.get("details")
        if details is not None:
            details.update(
                {
                    "layer": "classifier",
                    "fallback_reason": "timeout",
                    "classifier_error": "timeout after 6.0s",
                }
            )
        return RouteResult(
            model="chat-default", source="classifier", intent="chat",
            latency_ms=6000.0, confidence=None,
        )

    monkeypatch.setattr(orchestrator_mod, "_route", fake_route)
    client = _make_client(tmp_path, FakeLLMClient(chunks=["hi"]))
    trace_id = _run_turn(client)

    data = _spans_by_stage(client, trace_id)["route"][0]
    assert data["source"] == "classifier"
    assert data["layer"] == "classifier"
    assert data["fallback_reason"] == "timeout"


def test_slow_first_token_emits_swap_wait_span(tmp_path: Path, monkeypatch) -> None:
    """A model_loading wait is llama-swap loading the slot — it gets its own
    span so the Debug view can show the swap badge (docs/FEATURES.md F1)."""
    import app.chat.orchestrator as orchestrator_mod

    monkeypatch.setattr(orchestrator_mod, "FIRST_TOKEN_WARN_S", 0.02)
    fake = FakeLLMClient(chunks=["hi"], delay_before_first=0.15)
    client = _make_client(tmp_path, fake, first_token_timeout_s=5)
    trace_id = _run_turn(client)

    spans = _spans_by_stage(client, trace_id)
    assert "swap_wait" in spans
    assert spans["swap_wait"][0]["model"] == "chat-default"


def test_alias_model_sends_canonical_swap_name_to_llm(tmp_path: Path) -> None:
    """When a model alias shares a GGUF with a canonical entry (e.g.
    reasoner shares chat-default's file), the orchestrator must send the
    canonical name to llm_client.chat() — llama-swap only has the canonical
    entry.  Debug/done payload should still show the alias name."""
    fake = FakeLLMClient(chunks=["ok"])
    cfg = Config(
        llama_swap=LlamaSwapConfig(base_url="http://127.0.0.1:8080/v1"),
        db=DbConfig(path=str(tmp_path / "app.db")),
        models=[
            ModelEntry(
                name="chat-default",
                **{"class": "general"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/models/shared.gguf",
                quant="Q4_K_M",
                resident=True,
            ),
            ModelEntry(
                name="reasoner",
                **{"class": "reasoning"},
                ctx=4096,
                gpu=0,
                tool_call="none",
                thinking=True,
                max_tokens=4096,
                file="/models/shared.gguf",
                quant="Q4_K_M",
            ),
        ],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="chat-default",
            title_model="chat-default",
        ),
    )
    app = create_app(config=cfg)
    app.state.llm_client = fake
    client = TestClient(app)
    client.__enter__()

    chat_id = client.post("/api/chats", json={}).json()["id"]
    # Force model override to "reasoner" (the alias)
    client.post(f"/api/chats/{chat_id}/model", json={"model": "reasoner"})
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "think about this"})
    events = _parse_sse(resp.text)
    done_data = next(data for ev, data in events if ev == "done")

    # llm_client should have received the canonical name
    assert fake.last_model == "chat-default"
    # done payload should still show the alias for Debug visibility
    assert done_data["model"] == "reasoner"
