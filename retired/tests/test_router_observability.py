from tests.router_fixtures import *  # noqa: F401,F403
from tests.router_fixtures import _clf_json


@pytest.mark.asyncio
async def test_classifier_timeout_span_has_fallback_reason(db_conn) -> None:
    """Timeout: route span should record fallback_reason=timeout."""
    from app.debug import new_trace

    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=[_clf_json("code_task", 0.9)], delay_s=5.0)
    client = make_client(fake, timeout_s=0.05)

    trace_id = new_trace(chat_id="chat-1")
    cfg = make_config(timeout_s=0.05)
    await route(chat(), "some text", [], llm_client=client, config=cfg, trace_id=trace_id)

    spans = db_conn.execute(
        "SELECT data FROM spans WHERE trace_id = ? AND stage = 'route'", (trace_id,)
    ).fetchall()
    try:
        assert len(spans) == 1
        data = json.loads(spans[0]["data"])
        assert data["fallback_reason"] == "timeout"
        assert data["source"] == "classifier"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_low_confidence_returns_fallback_with_confidence_preserved() -> None:
    """Confidence below threshold → fallback_model, confidence value kept, source=classifier."""
    fake = FakeLlamaSwap()
    # confidence=0.2 < threshold=0.5
    fake.script_chat(content_chunks=[_clf_json("code_task", 0.2)])
    client = make_client(fake)

    cfg = make_config(confidence_threshold=0.5, fallback_model="chat-default")
    result = await route(chat(), "some text", [], llm_client=client, config=cfg)
    try:
        assert result.source == "classifier"
        assert result.model == "chat-default"
        assert result.confidence == pytest.approx(0.2)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_low_confidence_span_has_fallback_reason(db_conn) -> None:
    """Low confidence: route span should record fallback_reason=low_confidence."""
    from app.debug import new_trace

    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=[_clf_json("code_task", 0.2)])
    client = make_client(fake)

    trace_id = new_trace(chat_id="chat-1")
    cfg = make_config(confidence_threshold=0.5)
    await route(chat(), "some text", [], llm_client=client, config=cfg, trace_id=trace_id)

    spans = db_conn.execute(
        "SELECT data FROM spans WHERE trace_id = ? AND stage = 'route'", (trace_id,)
    ).fetchall()
    try:
        assert len(spans) == 1
        data = json.loads(spans[0]["data"])
        assert data["fallback_reason"] == "low_confidence"
        assert data["source"] == "classifier"
        # Confidence value is preserved
        assert data["confidence"] == pytest.approx(0.2)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Span fields — happy-path classifier route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_span_emitted_with_correct_fields(db_conn) -> None:
    """Successful classifier route emits span with source, intent, model, confidence."""
    from app.debug import new_trace

    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=[_clf_json("reasoning_task", 0.88)])
    client = make_client(fake)

    trace_id = new_trace(chat_id="chat-1")
    cfg = make_config()
    result = await route(chat(), "prove the halting problem", [], llm_client=client, config=cfg, trace_id=trace_id)

    spans = db_conn.execute(
        "SELECT data FROM spans WHERE trace_id = ? AND stage = 'route'", (trace_id,)
    ).fetchall()
    try:
        assert result.model == "reasoner"
        assert len(spans) == 1
        data = json.loads(spans[0]["data"])
        assert data["source"] == "classifier"
        assert data["intent"] == "reasoning_task"
        assert data["model"] == "reasoner"
        assert data["confidence"] == pytest.approx(0.88)
        assert "latency_ms" in data
        assert "fallback_reason" not in data
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# No llm_client → fallback gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_llm_client_degrades_to_fallback() -> None:
    """If llm_client is None and no rule matches, degrade to fallback — never raise."""
    cfg = make_config()
    result = await route(chat(), "some question", [], llm_client=None, config=cfg)
    assert result.source == "classifier"
    assert result.model == "chat-default"
    assert result.confidence is None


# ---------------------------------------------------------------------------
# The `details` out-dict: how a caller that owns its own route span learns
# *why* a turn routed the way it did (RouteResult's shape is frozen).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_details_carries_layer_and_classifier_prompt() -> None:
    """A successful classifier decision reports its layer plus what the
    classifier was actually sent and returned."""
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=[_clf_json("code_task", 0.9)])
    client = make_client(fake)

    details: dict = {}
    cfg = make_config()
    try:
        result = await route(
            chat(), "make me a thing", [], llm_client=client, config=cfg, details=details
        )
        assert result.source == "classifier"
        assert details["layer"] == "classifier"
        assert "fallback_reason" not in details
        assert "make me a thing" in details["prompt"]
        assert details["raw_response"] == _clf_json("code_task", 0.9)
        assert details["classifier_model"] == cfg.routing.classifier.model
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_details_distinguishes_timeout_from_a_real_chat_decision() -> None:
    """The live bug this guards: a classifier timeout used to be
    indistinguishable from a confident `chat` answer — same source, same
    intent, same null confidence. `details` has to say which it was."""
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=[_clf_json("code_task", 0.9)], delay_s=5.0)
    client = make_client(fake, timeout_s=0.05)

    details: dict = {}
    cfg = make_config(timeout_s=0.05)
    try:
        result = await route(
            chat(), "some text", [], llm_client=client, config=cfg, details=details
        )
        assert result.intent == "chat" and result.confidence is None
        assert details["fallback_reason"] == "timeout"
        assert details["classifier_error"].startswith("timeout")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_details_reports_missing_llm_client_distinctly() -> None:
    """no_llm_client is its own fallback_reason — the Phase-2 wiring bug
    (orchestrator never passed llm_client) looked like a generic error."""
    details: dict = {}
    cfg = make_config()
    await route(chat(), "some question", [], llm_client=None, config=cfg, details=details)
    assert details["fallback_reason"] == "no_llm_client"


@pytest.mark.asyncio
async def test_override_details_report_the_override_layer() -> None:
    details: dict = {}
    cfg = make_config()
    await route(
        chat(model_override="coder"), "anything", [], llm_client=None,
        config=cfg, details=details,
    )
    assert details["layer"] == "override"


@pytest.mark.asyncio
async def test_sticky_routing_prefers_loaded_model_when_confidence_low() -> None:
    """When classifier confidence < 0.8, prefer the currently-loaded GPU0 model
    to avoid unnecessary swaps (HANDOFF 2026-08-06)."""
    fake = FakeLlamaSwap()
    # Classifier returns "code_task" with confidence 0.65 (below 0.8 threshold)
    fake.script_chat(
        content_chunks=['{"class": "code_task", "confidence": 0.65}'],
        finish_reason="stop",
    )
    client = make_client(fake)
    cfg = make_config()
    try:
        # preferred_model is "chat-default" (currently loaded on GPU0)
        result = await route(
            chat(), "write a script", [], llm_client=client, config=cfg,
            preferred_model="chat-default",
        )
        # Should use preferred model instead of coder (which code_task maps to)
        assert result.model == "chat-default"
        assert result.intent == "code_task"
        assert result.confidence == 0.65
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sticky_routing_yields_when_confidence_high() -> None:
    """When classifier confidence >= 0.8, use the classifier's choice even if
    it differs from the preferred model."""
    fake = FakeLlamaSwap()
    # Classifier returns "code_task" with confidence 0.92 (above 0.8 threshold)
    fake.script_chat(
        content_chunks=['{"class": "code_task", "confidence": 0.92}'],
        finish_reason="stop",
    )
    client = make_client(fake)
    cfg = make_config()
    try:
        result = await route(
            chat(), "write a script", [], llm_client=client, config=cfg,
            preferred_model="chat-default",
        )
        # Should use coder (code_task maps to coder) despite preferred_model
        assert result.model == "coder"
        assert result.intent == "code_task"
        assert result.confidence == 0.92
    finally:
        await client.close()
