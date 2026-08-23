from tests.chat_fixtures import *  # noqa: F401,F403
from tests.chat_fixtures import _make_client, _parse_sse, _test_config


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
    assert spans["llm_request"][0]["message_count"] == 2


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


def test_evidenced_cold_model_emits_swap_wait_span(tmp_path: Path, monkeypatch) -> None:
    """Only an explicit cold swap status gets the Debug swap badge."""
    import app.chat.orchestrator as orchestrator_mod

    monkeypatch.setattr(orchestrator_mod, "FIRST_TOKEN_WARN_S", 0.02)
    fake = FakeLLMClient(
        chunks=["hi"], delay_before_first=0.15, model_status={"chat-default": False},
    )
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
                max_tokens=1024,
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

    # The chat turn should send the canonical name to llm_client.
    # Filter out startup warm-up (classifier) and background jobs (dispatcher).
    chat_models = [m for m in fake.all_models if m not in ("classifier", "dispatcher")]
    assert chat_models[0] == "chat-default"
    # done payload should still show the alias for Debug visibility
    assert done_data["model"] == "reasoner"


def test_on_turn_complete_called_on_llm_error(tmp_path: Path, monkeypatch) -> None:
    """_on_turn_complete must fire when the LLM raises an LLMError."""
    import app.chat.orchestrator as orchestrator_mod

    captured: list[str] = []

    async def fake_on_turn_complete(chat_id: str) -> None:
        captured.append(chat_id)

    monkeypatch.setattr(orchestrator_mod, "_on_turn_complete", fake_on_turn_complete)

    fake = FakeLLMClient(raise_error=LLMError("connection", "refused"))
    client = _make_client(tmp_path, fake)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})

    events = _parse_sse(resp.text)
    assert events[-1][0] == "error"
    assert captured == [chat_id], f"_on_turn_complete not called on error path, captured={captured}"


def test_on_turn_complete_called_on_first_token_timeout(tmp_path: Path, monkeypatch) -> None:
    """_on_turn_complete must fire when the first token times out."""
    import app.chat.orchestrator as orchestrator_mod

    captured: list[str] = []

    async def fake_on_turn_complete(chat_id: str) -> None:
        captured.append(chat_id)

    monkeypatch.setattr(orchestrator_mod, "_on_turn_complete", fake_on_turn_complete)
    monkeypatch.setattr(orchestrator_mod, "FIRST_TOKEN_WARN_S", 0.01)

    # LLM delays forever — triggers first_token_timeout.
    fake = FakeLLMClient(chunks=["ok"], delay_before_first=99.0)
    client = _make_client(tmp_path, fake, first_token_timeout_s=0.05)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})

    events = _parse_sse(resp.text)
    assert events[-1][0] == "error"
    assert captured == [chat_id], f"_on_turn_complete not called on timeout path, captured={captured}"


def test_on_turn_complete_called_on_internal_error(tmp_path: Path, monkeypatch) -> None:
    """_on_turn_complete must fire on unexpected internal errors too."""
    import app.chat.orchestrator as orchestrator_mod
    import app.chat.history as history_mod

    captured: list[str] = []

    async def fake_on_turn_complete(chat_id: str) -> None:
        captured.append(chat_id)

    monkeypatch.setattr(orchestrator_mod, "_on_turn_complete", fake_on_turn_complete)

    # Cause an unexpected error mid-stream by breaking the persistence path.
    def broken_touch(*args, **kwargs):
        raise RuntimeError("db failure")

    monkeypatch.setattr(history_mod, "touch_chat", broken_touch)

    fake = FakeLLMClient(chunks=["ok"])
    client = _make_client(tmp_path, fake)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})

    events = _parse_sse(resp.text)
    assert events[-1][0] == "error"
    assert captured == [chat_id], f"_on_turn_complete not called on internal error path, captured={captured}"


# ---------------------------------------------------------------------------
# reasoning_content → llm_stream span (WS-B, 2026-08-11)
# ---------------------------------------------------------------------------


def test_reasoning_content_recorded_in_llm_stream_span(tmp_path: Path) -> None:
    """Reasoning/CoT content must appear in the llm_stream span's `reasoning`
    field so the Debug view can display it later (no new SSE events)."""
    fake = FakeLLMClient(
        reasoning_chunks=["Let me think", " about this step by step."],
        chunks=["The answer", " is 42."],
    )
    client = _make_client(tmp_path, fake)
    trace_id = _run_turn(client, "what is 2+2")

    spans = _spans_by_stage(client, trace_id)
    llm_stream = spans["llm_stream"][0]
    assert llm_stream["reasoning"] == "Let me think about this step by step."
    # Regular content still recorded as before.
    assert llm_stream["response"] == "The answer is 42."


def test_reasoning_content_none_when_no_reasoning(tmp_path: Path) -> None:
    """When no reasoning chunks are emitted, the span field is None."""
    fake = FakeLLMClient(chunks=["plain", " reply"])
    client = _make_client(tmp_path, fake)
    trace_id = _run_turn(client, "hi")

    spans = _spans_by_stage(client, trace_id)
    llm_stream = spans["llm_stream"][0]
    assert llm_stream.get("reasoning") is None
    assert llm_stream["response"] == "plain reply"
