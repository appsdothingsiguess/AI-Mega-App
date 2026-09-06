from tests.background_fixtures import *  # noqa: F401,F403


async def test_summary_threshold_scales_with_turns_model_ctx(bg_app) -> None:
    """The trigger threshold is summary_context_fraction (0.5) of the
    *turn's own model's* ctx, not a flat number -- TEST_MODEL (chat-default)
    has ctx=4096, so 2048 is the real cutoff even though it's well under
    the flat summary_token_threshold (4000) fallback used when the model
    can't be resolved."""
    app, fake, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    conn.execute("UPDATE chats SET title = 'preset' WHERE id = ?", (chat_id,))
    conn.commit()

    # Below flat fallback (4000) but above 50% of chat-default's ctx (2048)
    # -- must trigger because chat-default resolves against the roster.
    seed_exchange(conn, chat_id, "u1", "a1")
    seed_llm_stream_usage(conn, chat_id, prompt_tokens=2500, model="chat-default")
    fake.script_chat(content_chunks=["Triggered by ctx-relative threshold."])
    await on_turn_complete(chat_id)
    await get_queue(app).drain()
    assert history.get_chat(conn, chat_id)["summary"] == "Triggered by ctx-relative threshold."


async def test_summary_threshold_uses_smallest_routable_ctx_as_floor(tmp_path: Path) -> None:
    """2026-08-15 fix: a chat sitting comfortably under 50% of chat-default's
    roomy ctx (8192) must still trigger if it's already past 50% of the
    tightest ctx the router could send the *next* turn to (coder-small,
    1024) -- otherwise a chat that's been calm on chat-default could get
    routed to code_task on the next message and blow straight past
    coder-small's real budget with no summarization warning."""
    fake = FakeLlamaSwap()
    conn = open_db(tmp_path / "floor.db")
    trace_mod.reset_connection(conn)
    roomy = ModelEntry(
        name="chat-default",
        **{"class": "general"},
        ctx=8192,
        gpu=0,
        tool_call="native",
        max_tokens=1024,
        file="/models/chat-default.gguf",
        quant="Q4_K_M",
    )
    tight = ModelEntry(
        name="coder-small",
        **{"class": "coding"},
        ctx=1024,
        gpu=0,
        tool_call="native",
        max_tokens=512,
        file="/models/coder-small.gguf",
        quant="Q4_K_M",
    )
    config = Config(
        llama_swap=LlamaSwapConfig(base_url=BASE_URL, timeout_s=5.0),
        db=DbConfig(path=":memory:"),
        models=[roomy, tight, TEST_UTILITY_MODEL],
        defaults=DefaultsConfig(
            chat_model="chat-default", utility_model="utility", title_model="dispatcher"
        ),
        background=BackgroundConfig(title_model="dispatcher", summary_model="utility"),
        routing=RoutingConfig(
            intents=RoutingIntents(code_task="coder-small"),
        ),
    )
    llm = make_llm(fake)
    app = SimpleNamespace(state=SimpleNamespace(config=config, db=conn, llm_client=llm, summary_llm_client=llm))
    await start(app)
    try:
        chat_id = history.create_chat(conn)["id"]
        conn.execute("UPDATE chats SET title = 'preset' WHERE id = ?", (chat_id,))
        conn.commit()

        # 600/8192 on chat-default = 7% -- nowhere near 50% of chat-default's
        # own ctx, but well past 50% of coder-small's 1024 (512).
        seed_exchange(conn, chat_id, "u1", "a1")
        seed_llm_stream_usage(conn, chat_id, prompt_tokens=600, model="chat-default")
        fake.script_chat(content_chunks=["Triggered by the smaller routable-model floor."])
        await on_turn_complete(chat_id)
        await get_queue(app).drain()
        assert (
            history.get_chat(conn, chat_id)["summary"]
            == "Triggered by the smaller routable-model floor."
        )
    finally:
        await stop(app)
        await llm.close()
        trace_mod.reset_connection(None)
        conn.close()


async def test_summary_delta_exceeding_summarizer_ctx_splits_across_regens(
    tmp_path: Path,
) -> None:
    """2026-08-15 fix, live incident traces fe85c4f7/85b0fbc8: a delta too
    big for the summarizer's OWN ctx must not 400 forever. It should
    summarize the oldest prefix that fits now and pick up exactly where it
    left off on the next regen -- no message ever silently dropped, none
    silently re-included beyond what's needed."""
    fake = FakeLlamaSwap()
    conn = open_db(tmp_path / "split.db")
    trace_mod.reset_connection(conn)
    chat_model = ModelEntry(
        name="chat-default", **{"class": "general"}, ctx=8192, gpu=0,
        tool_call="native", max_tokens=1024,
        file="/models/chat-default.gguf", quant="Q4_K_M",
    )
    tiny_summarizer = ModelEntry(
        name="utility", **{"class": "utility"}, ctx=2048, gpu="cpu",
        tool_call="none", max_tokens=256,
        file="/models/utility.gguf", quant="Q4_K_M",
    )
    config = Config(
        llama_swap=LlamaSwapConfig(base_url=BASE_URL, timeout_s=5.0),
        db=DbConfig(path=":memory:"),
        models=[chat_model, tiny_summarizer],
        defaults=DefaultsConfig(
            chat_model="chat-default", utility_model="utility", title_model="dispatcher"
        ),
        background=BackgroundConfig(
            title_model="dispatcher", summary_model="utility", summary_every_n_turns=1
        ),
    )
    llm = make_llm(fake)
    app = SimpleNamespace(state=SimpleNamespace(config=config, db=conn, llm_client=llm, summary_llm_client=llm))
    await start(app)
    try:
        chat_id = history.create_chat(conn)["id"]
        conn.execute("UPDATE chats SET title = 'preset' WHERE id = ?", (chat_id,))
        conn.commit()

        # Each message ~857 tokens (3000 chars / 3.5) -- the pair together
        # (~1714 tok) exceeds utility's ~1024-token budget (ctx 2048 minus
        # max_tokens 1024), so only the user half fits in the first regen.
        big = "x " * 1500
        seed_exchange(conn, chat_id, big, big)
        fake.script_chat(content_chunks=["Summary from partial first regen."])
        await on_turn_complete(chat_id)
        await get_queue(app).drain()
        assert history.get_chat(conn, chat_id)["summary"] == "Summary from partial first regen."
        first_call = fake.chat_requests[-1]
        first_sent = first_call["messages"][-1]["content"]
        assert "Conversation so far" in first_sent  # first-ever regen, no prior yet

        # Second, short exchange -- combined with the leftover assistant
        # half from turn 1, this now fits comfortably in one regen.
        seed_exchange(conn, chat_id, "short q", "short a")
        fake.script_chat(content_chunks=["Summary catches up on the rest."])
        await on_turn_complete(chat_id)
        await get_queue(app).drain()
        assert history.get_chat(conn, chat_id)["summary"] == "Summary catches up on the rest."
        second_call = fake.chat_requests[-1]
        second_sent = second_call["messages"][-1]["content"]
        # The leftover assistant half from turn 1 must show up here -- it
        # was never lost, just deferred to this regen.
        assert big.strip() in second_sent
        assert "short a" in second_sent
    finally:
        await stop(app)
        await llm.close()
        trace_mod.reset_connection(None)
        conn.close()


async def test_summary_triggers_on_token_threshold_not_turn_count(bg_app) -> None:
    """Once real llama.cpp usage data exists for a chat, the token
    threshold is authoritative -- summary fires even off the turn-count
    cadence (summary_every_n_turns=6 from make_config's default), and does
    NOT fire again just because turn count keeps climbing while tokens
    stay low."""
    app, fake, conn = bg_app
    chat = history.create_chat(conn)
    chat_id = chat["id"]
    # Pre-set the title so the concurrent title job (which always fires on
    # the first exchange) doesn't also consume a scripted response and
    # make this test racy against the summary job.
    conn.execute("UPDATE chats SET title = 'preset' WHERE id = ?", (chat_id,))
    conn.commit()

    # Turn 1 (not a multiple of 6): high token usage should still trigger.
    seed_exchange(conn, chat_id, "u1", "a1")
    seed_llm_stream_usage(conn, chat_id, prompt_tokens=5000)
    fake.script_chat(content_chunks=["Triggered by tokens, not turn count."])
    await on_turn_complete(chat_id)
    await get_queue(app).drain()
    assert history.get_chat(conn, chat_id)["summary"] == "Triggered by tokens, not turn count."

    # Turn 2: usage stays low -- must not re-fire even though a summary
    # now exists and turn count keeps climbing.
    n_before = len(fake.chat_requests)
    seed_exchange(conn, chat_id, "u2", "a2")
    seed_llm_stream_usage(conn, chat_id, prompt_tokens=100)
    await on_turn_complete(chat_id)
    await get_queue(app).drain()
    assert len(fake.chat_requests) == n_before


async def test_summary_sends_only_delta_since_last_regen(bg_app) -> None:
    """The prompt sent to the summarizer on a second regen must carry only
    the previous summary + new messages -- not the entire transcript again
    (the redundant full-resend this rewrite replaces)."""
    app, fake, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    conn.execute("UPDATE chats SET title = 'preset' WHERE id = ?", (chat_id,))
    conn.commit()

    seed_exchange(conn, chat_id, "first question", "first answer")
    # Backdate the first exchange well outside the same wall-clock second as
    # the summary span below -- otherwise the second-resolution cutoff
    # (app/background/summaries.py's inclusive same-second floor) can't
    # distinguish "before" from "after" and this test would be racing the
    # clock instead of testing the delta logic.
    conn.execute(
        "UPDATE messages SET created_at = created_at - 100 WHERE chat_id = ?",
        (chat_id,),
    )
    conn.commit()
    seed_llm_stream_usage(conn, chat_id, prompt_tokens=5000)
    fake.script_chat(content_chunks=["Summary one."])
    await on_turn_complete(chat_id)
    await get_queue(app).drain()
    assert history.get_chat(conn, chat_id)["summary"] == "Summary one."

    seed_exchange(conn, chat_id, "second question", "second answer")
    seed_llm_stream_usage(conn, chat_id, prompt_tokens=5000)
    fake.script_chat(content_chunks=["Summary two."])
    await on_turn_complete(chat_id)
    await get_queue(app).drain()
    assert history.get_chat(conn, chat_id)["summary"] == "Summary two."

    second_summary_request = fake.chat_requests[-1]
    sent_content = second_summary_request["messages"][-1]["content"]
    assert "Previous summary:\nSummary one." in sent_content
    assert "second question" in sent_content
    # The delta must not re-send the first turn's raw text -- only the
    # prior summary stands in for it now.
    assert "first question" not in sent_content
