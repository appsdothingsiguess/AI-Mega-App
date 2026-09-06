from tests.router_fixtures import *  # noqa: F401,F403
from tests.router_fixtures import _clf_json


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
