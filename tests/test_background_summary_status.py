from tests.background_fixtures import *  # noqa: F401,F403


async def test_summary_status_below_threshold(bg_app) -> None:
    """Debug summary-status endpoint: below the trigger threshold reports
    will_trigger False with the real numbers, not just a boolean."""
    app, fake, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    seed_llm_stream_usage(conn, chat_id, prompt_tokens=100, model="chat-default")

    status = await summary_status(app, chat_id)
    assert status["will_trigger"] is False
    assert status["latest_tokens"] == 100
    assert status["latest_model"] == "chat-default"
    assert status["source"] == "token_ctx_fraction"
    # chat-default ctx=4096 (TEST_MODEL), fraction=0.5 default -> 2048
    assert status["threshold_tokens"] == 2048
    assert status["covered_message_count"] == 0
    assert status["last_summary"] is None
    assert status["in_flight"] is False


async def test_summary_status_above_threshold_matches_enqueue_decision(bg_app) -> None:
    """will_trigger must agree with what maybe_enqueue_summary actually
    does -- both read _trigger_state, so this pins that they can't drift."""
    app, fake, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    conn.execute("UPDATE chats SET title = 'preset' WHERE id = ?", (chat_id,))
    conn.commit()
    seed_exchange(conn, chat_id, "u1", "a1")
    seed_llm_stream_usage(conn, chat_id, prompt_tokens=3000, model="chat-default")

    status = await summary_status(app, chat_id)
    assert status["will_trigger"] is True
    assert status["threshold_tokens"] == 2048

    fake.script_chat(content_chunks=["Regen from status test."])
    await on_turn_complete(chat_id)
    await get_queue(app).drain()
    assert history.get_chat(conn, chat_id)["summary"] == "Regen from status test."


async def test_summary_status_reports_last_summary_after_regen(bg_app) -> None:
    """After a successful regen, summary_status surfaces which target ran
    (device) and what it covered -- what the Debug panel needs to show."""
    app, fake, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    conn.execute("UPDATE chats SET title = 'preset' WHERE id = ?", (chat_id,))
    conn.commit()
    seed_exchange(conn, chat_id, "u1", "a1")
    seed_llm_stream_usage(conn, chat_id, prompt_tokens=3000, model="chat-default")
    fake.script_chat(content_chunks=["Summary for status check."])
    await on_turn_complete(chat_id)
    await get_queue(app).drain()

    status = await summary_status(app, chat_id)
    last = status["last_summary"]
    assert last is not None
    assert last["model"] == "utility"
    assert last["device"] == "cpu"
    assert last["covered_message_count"] == status["covered_message_count"] == 2
    assert last["new_message_count"] == 2
    assert last["chars"] == len("Summary for status check.")


async def test_summary_status_turn_count_fallback_when_no_usage(bg_app) -> None:
    """No llm_stream usage yet (first turn) -> falls back to the turn-count
    cadence, same as maybe_enqueue_summary."""
    app, fake, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")  # 1 user turn, no usage span

    status = await summary_status(app, chat_id)
    assert status["source"] == "turn_count_fallback"
    assert status["latest_tokens"] is None
    assert status["threshold_tokens"] is None
    # make_config() default summary_every_n_turns=6; turn_count=1 -> not a multiple
    assert status["will_trigger"] is False
    assert status["summary_every_n_turns"] == 6
