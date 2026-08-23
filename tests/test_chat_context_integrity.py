from tests.chat_fixtures import *  # noqa: F401,F403
from tests.chat_fixtures import _parse_sse


def test_long_history_refuses_instead_of_truncating_context(tmp_path: Path) -> None:
    """An oversized lossless prompt fails before dispatch instead of
    silently dropping the oldest persisted turns."""
    fake = FakeLLMClient(chunks=["ok"])
    cfg = Config(
        llama_swap=LlamaSwapConfig(base_url="http://127.0.0.1:8080/v1"),
        db=DbConfig(path=str(tmp_path / "app.db")),
        models=[
            ModelEntry(
                name="chat-default",
                **{"class": "general"},
                ctx=1024,
                gpu=0,
                tool_call="native",
                max_tokens=200,
                file="/models/chat-default.gguf",
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
    fake.all_models.clear()  # exclude startup warm-up from the dispatch assertion
    resp = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "word " * 1000, "model": "chat-default"},
    )

    events = _parse_sse(resp.text)
    assert [event for event, _ in events] == ["error"]
    assert events[0][1]["kind"] == "context_overflow"
    assert fake.all_models == []


def test_metadata_less_summary_falls_back_to_all_raw_history(
    tmp_path: Path,
) -> None:
    """A summary written without matching coverage metadata is untrusted."""
    fake = FakeLLMClient(chunks=["ok"])
    cfg = Config(
        llama_swap=LlamaSwapConfig(base_url="http://127.0.0.1:8080/v1"),
        db=DbConfig(path=str(tmp_path / "app.db")),
        models=[
            ModelEntry(
                name="chat-default",
                **{"class": "general"},
                ctx=32768,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/models/chat-default.gguf",
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
    for i in range(10):
        client.post(f"/api/chats/{chat_id}/messages", json={"content": f"turn {i}"})

    # Simulate the background summary job having run (it's async/queued in
    # production; write the summary directly to isolate the orchestrator's
    # compaction behavior from the background job).
    app.state.db.execute(
        "UPDATE chats SET summary = ? WHERE id = ?",
        ("Earlier turns covered X, Y, Z.", chat_id),
    )
    app.state.db.commit()

    client.post(f"/api/chats/{chat_id}/messages", json={"content": "one more turn"})

    sent = fake.seen_messages
    assert sent is not None
    assert sent[0]["role"] == "system"  # persistent-assistant stopgap prompt
    assert not any("Earlier turns covered X, Y, Z." in m["content"] for m in sent)
    assert any("turn 0" in m["content"] for m in sent)
    assert any("turn 9" in m["content"] for m in sent)
    assert any("one more turn" in m["content"] for m in sent)


def test_chat_summary_compaction_trims_tail_to_what_summary_doesnt_cover(
    tmp_path: Path,
) -> None:
    """2026-08-15 fix: when a `summary` span records exactly when the
    rolling summary was last regenerated, the compacted tail must exclude
    everything at or before that point instead of always keeping a fixed
    `summary_every_n_turns`-sized tail regardless of overlap -- otherwise
    the raw tail and the summary cover the same turns twice on every turn
    right after a regen."""
    fake = FakeLLMClient(chunks=["ok"])
    cfg = Config(
        llama_swap=LlamaSwapConfig(base_url="http://127.0.0.1:8080/v1"),
        db=DbConfig(path=str(tmp_path / "app.db")),
        models=[
            ModelEntry(
                name="chat-default",
                **{"class": "general"},
                ctx=32768,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/models/chat-default.gguf",
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
    for i in range(10):
        client.post(f"/api/chats/{chat_id}/messages", json={"content": f"turn {i}"})

    app.state.db.execute(
        "UPDATE chats SET summary = ? WHERE id = ?",
        ("Earlier turns covered X, Y, Z.", chat_id),
    )
    trace_id = "test-summary-trace"
    now_ms = int(time.time() * 1000)
    app.state.db.execute(
        "INSERT INTO traces (trace_id, chat_id, started_at) VALUES (?, ?, ?)",
        (trace_id, chat_id, now_ms),
    )
    summary = "Earlier turns covered X, Y, Z."
    raw_messages = history.list_messages(app.state.db, chat_id)
    metadata = coverage_fields(raw_messages, 20, summary)
    app.state.db.execute(
        "INSERT INTO spans (trace_id, stage, started_at, ended_at, data) "
        "VALUES (?, 'summary', ?, ?, ?)",
        (trace_id, now_ms, now_ms, json.dumps(metadata)),
    )
    app.state.db.commit()

    client.post(f"/api/chats/{chat_id}/messages", json={"content": "one more turn"})

    sent = fake.seen_messages
    assert sent is not None
    assert any("Earlier turns covered X, Y, Z." in m["content"] for m in sent)
    # Everything from before the summary regen must be gone from the raw
    # tail -- only the new turn (plus the two system messages) remains.
    assert not any("turn 0" in m["content"] for m in sent)
    assert not any("turn 9" in m["content"] for m in sent)
    assert any("one more turn" in m["content"] for m in sent)
    assert len(sent) <= 3


# ---------------------------------------------------------------------------
# Error-path _on_turn_complete — WS-B: titles/summaries must fire on
# error/timeout paths, not just the success path.
# ---------------------------------------------------------------------------
