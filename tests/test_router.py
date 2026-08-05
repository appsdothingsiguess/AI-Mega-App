"""Tests for the three-layer smart router (PLAN.md §4.3, docs/FEATURES.md F5).

All tests run against FakeLlamaSwap — no real GPU or network needed.

Coverage:
  - override wins (classifier scripted but never called)
  - keyword rule wins over classifier
  - canned classifier JSON routes to correct model
  - classifier timeout → fallback_model, confidence=None, fallback_reason=timeout
  - low confidence → fallback_model, confidence preserved, fallback_reason=low_confidence
  - attachment forcing: image → vision_task / vision
  - word-boundary: "scode" does not fire a keyword rule for "write code"
  - span emitted with correct fields when trace_id provided
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import (
    Config,
    DbConfig,
    DefaultsConfig,
    LlamaSwapConfig,
    ModelEntry,
    RoutingClassifierConfig,
    RoutingConfig,
    RoutingIntents,
    RoutingRule,
)
from app.db import open_db
from app.debug import trace as trace_mod
from app.llm_client import LLMClient
from app.router import route
from tests.fakes.fake_llama_swap import FakeLlamaSwap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_URL = "http://fake-router/v1/"


def make_client(fake: FakeLlamaSwap, timeout_s: float = 5.0) -> LLMClient:
    """Build an LLMClient wired to an in-process FakeLlamaSwap."""
    client = LLMClient(base_url=BASE_URL, timeout_s=timeout_s)
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app),
        base_url=BASE_URL,
        timeout=timeout_s,
    )
    return client


def _clf_json(cls: str, confidence: float) -> str:
    return json.dumps({"class": cls, "confidence": confidence})


def make_config(
    *,
    rules: list[RoutingRule] | None = None,
    attachments: dict[str, str] | None = None,
    timeout_s: float = 2.0,
    confidence_threshold: float = 0.5,
    fallback_model: str = "chat-default",
) -> Config:
    """Minimal Config with a controlled routing section."""
    return Config(
        llama_swap=LlamaSwapConfig(base_url="http://fake/v1/"),
        db=DbConfig(path=":memory:"),
        models=[
            ModelEntry(
                name="chat-default",
                **{"class": "general"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/m/chat-default.gguf",
                quant="Q4_K_M",
            ),
            ModelEntry(
                name="coder",
                **{"class": "coding"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/m/coder.gguf",
                quant="Q4_K_M",
            ),
            ModelEntry(
                name="vision",
                **{"class": "vision"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/m/vision.gguf",
                quant="Q4_K_M",
            ),
            ModelEntry(
                name="reasoner",
                **{"class": "reasoning"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/m/reasoner.gguf",
                quant="Q4_K_M",
            ),
        ],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="chat-default",
            title_model="chat-default",
        ),
        routing=RoutingConfig(
            rules=rules or [],
            attachments=attachments or {},
            intents=RoutingIntents(
                chat="chat-default",
                chit_chat="chat-default",
                code_task="coder",
                reasoning_task="reasoner",
                vision_task="vision",
                tool_call_needed="chat-default",
            ),
            classifier=RoutingClassifierConfig(
                model="classifier",
                timeout_s=timeout_s,
                confidence_threshold=confidence_threshold,
                fallback_model=fallback_model,
            ),
        ),
    )


def chat(model_override: str | None = None) -> dict[str, Any]:
    return {"model_override": model_override, "id": "chat-1"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_conn(tmp_path: Path):
    conn = open_db(tmp_path / "router_test.db")
    trace_mod.reset_connection(conn)
    try:
        yield conn
    finally:
        trace_mod.reset_connection(None)
        conn.close()


# ---------------------------------------------------------------------------
# Layer 1: manual override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_wins_and_classifier_never_called() -> None:
    """model_override bypasses both rules and classifier entirely."""
    fake = FakeLlamaSwap()
    # Script a response — it must NOT be consumed if override fires first
    fake.script_chat(content_chunks=[_clf_json("code_task", 0.9)])
    client = make_client(fake)

    cfg = make_config()
    result = await route(chat("my-special-model"), "write some code", [], llm_client=client, config=cfg)
    try:
        assert result.source == "override"
        assert result.model == "my-special-model"
        assert result.confidence is None
        # Classifier was never called
        assert len(fake.chat_requests) == 0
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Layer 2: keyword rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_wins_over_classifier() -> None:
    """Keyword rule match returns before the classifier is invoked."""
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=[_clf_json("chat", 0.9)])
    client = make_client(fake)

    cfg = make_config(rules=[RoutingRule(keywords=["write code"], intent="code_task")])
    result = await route(chat(), "Please write code for me", [], llm_client=client, config=cfg)
    try:
        assert result.source == "rule"
        assert result.intent == "code_task"
        assert result.model == "coder"
        # Classifier was never called
        assert len(fake.chat_requests) == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rule_word_boundary_no_false_positive() -> None:
    """'scode' must not fire a rule that matches 'write code'."""
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=[_clf_json("code_task", 0.95)])
    client = make_client(fake)

    cfg = make_config(rules=[RoutingRule(keywords=["write code"], intent="code_task")])
    result = await route(chat(), "scode is a great tool", [], llm_client=client, config=cfg)
    try:
        # Rule should NOT match — falls through to classifier
        assert result.source == "classifier"
        assert len(fake.chat_requests) == 1
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Layer 2a: attachment forcing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachment_image_forces_vision_task() -> None:
    """image attachment → vision_task / vision model, no LLM call."""
    fake = FakeLlamaSwap()
    client = make_client(fake)

    cfg = make_config(attachments={"image": "vision_task"})
    attachments = [{"type": "image", "url": "data:image/png;base64,..."}]
    result = await route(chat(), "What is in this image?", attachments, llm_client=client, config=cfg)
    try:
        assert result.source == "rule"
        assert result.intent == "vision_task"
        assert result.model == "vision"
        assert len(fake.chat_requests) == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_attachment_code_file_forces_code_task() -> None:
    """code_file attachment → code_task / coder model, no LLM call."""
    fake = FakeLlamaSwap()
    client = make_client(fake)

    cfg = make_config(attachments={"code_file": "code_task"})
    attachments = [{"type": "code_file", "path": "main.py"}]
    result = await route(chat(), "review this", attachments, llm_client=client, config=cfg)
    try:
        assert result.source == "rule"
        assert result.intent == "code_task"
        assert result.model == "coder"
        assert len(fake.chat_requests) == 0
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Layer 3: classifier — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canned_classifier_json_routes_to_coder() -> None:
    """Canned JSON {"class":"code_task","confidence":0.9} → model=coder."""
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=[_clf_json("code_task", 0.9)])
    client = make_client(fake)

    cfg = make_config()
    result = await route(chat(), "help me with something", [], llm_client=client, config=cfg)
    try:
        assert result.source == "classifier"
        assert result.intent == "code_task"
        assert result.model == "coder"
        assert result.confidence == pytest.approx(0.9)
        assert len(fake.chat_requests) == 1
        # Verify thinking=False was sent (reasoning off per-request)
        assert fake.chat_requests[0].get("reasoning") == "off"
        # Verify stream=False
        assert fake.chat_requests[0].get("stream") is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_canned_classifier_chat_routes_to_default() -> None:
    """chat class → chat-default model."""
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=[_clf_json("chat", 0.95)])
    client = make_client(fake)

    cfg = make_config()
    result = await route(chat(), "what's up?", [], llm_client=client, config=cfg)
    try:
        assert result.source == "classifier"
        assert result.intent == "chat"
        assert result.model == "chat-default"
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Layer 3: classifier — fallback paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classifier_timeout_returns_fallback() -> None:
    """Classifier timeout → fallback_model, confidence=None, source=classifier."""
    fake = FakeLlamaSwap()
    # delay_s > timeout_s so the client times out
    fake.script_chat(content_chunks=[_clf_json("code_task", 0.9)], delay_s=5.0)
    client = make_client(fake, timeout_s=0.05)

    cfg = make_config(timeout_s=0.05, fallback_model="chat-default")
    result = await route(chat(), "some text", [], llm_client=client, config=cfg)
    try:
        assert result.source == "classifier"
        assert result.model == "chat-default"
        assert result.confidence is None
    finally:
        await client.close()


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
