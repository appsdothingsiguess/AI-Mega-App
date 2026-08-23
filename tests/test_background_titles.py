from tests.background_fixtures import *  # noqa: F401,F403


def test_clean_title_strips_fences_and_quotes() -> None:
    assert clean_title('```\nHello World Title\n```') == "Hello World Title"
    assert clean_title('"Quoted Title Here"') == "Quoted Title Here"
    assert clean_title("'Single Quoted Title'") == "Single Quoted Title"


def test_clean_title_truncates_over_six_words() -> None:
    raw = "One Two Three Four Five Six Seven Eight"
    assert clean_title(raw) == "One Two Three Four Five Six"
    assert len(clean_title(raw).split()) == 6


def test_clean_title_keeps_short() -> None:
    assert clean_title("Short Title") == "Short Title"


def test_clean_title_strips_chat_preamble() -> None:
    # Live Debug data (2026-08-15): dispatcher sometimes prefixes with
    # "Chat:" instead of the taught "Title:" -- both should be stripped.
    assert clean_title("Chat: Hello! It sounds like you") == "Hello! It sounds like you"


def test_is_echo_detects_verbatim_restatement() -> None:
    # Live Debug data (2026-08-15): raw "Meow! How are you doing? Meow!"
    # cleaned to a near-identical "title" instead of a summary.
    assistant = "Meow! I'm doing well, thank you for asking. How about yourself? Meow!"
    title = "Meow! How are you doing? Meow"
    assert is_echo(title, "meow", assistant) is False  # not from this exchange
    assert is_echo("Meow! I'm doing well thank you", "hi", assistant) is True


def test_is_echo_detects_leaked_user_tag() -> None:
    # Live Debug data (2026-08-15): a project-instructions block leaking
    # into the user turn produced the literal title "<project_instructions>".
    user = "<project_instructions>\nMandatory analysis..."
    assert is_echo("Project Instructions", user, "some assistant reply") is True
    assert is_echo("project", user, "some assistant reply") is False  # single word, ignored


def test_is_echo_allows_similar_but_distinct_titles() -> None:
    # A synthesized title can legitimately share a couple of opening words
    # with the source text without being a copy-paste echo.
    assistant = "The RTX 3090's 24GB VRAM and ~936 GB/s bandwidth sit in a sweet spot."
    title = "The RTX 3090's VRAM Budget for Local AI"
    assert is_echo(title, "user question text", assistant) is False


async def test_first_exchange_sets_title(bg_app) -> None:
    app, fake, conn = bg_app
    fake.script_chat(content_chunks=["Git Undo Soft Reset"])
    chat = history.create_chat(conn)
    seed_exchange(conn, chat["id"], "How do I undo a commit?", "Use git reset --soft.")

    await on_turn_complete(chat["id"])
    queue = get_queue(app)
    assert queue is not None
    await queue.drain()

    row = history.get_chat(conn, chat["id"])
    assert row is not None
    assert row["title"] == "Git Undo Soft Reset"
    assert len(fake.chat_requests) == 1
    assert fake.chat_requests[0]["model"] == app.state.config.background.title_model
    assert fake.chat_requests[0]["model"] == "dispatcher"


async def test_echoed_title_falls_back_to_deterministic(bg_app) -> None:
    # Live Debug data (2026-08-15): dispatcher echoed the assistant's own
    # greeting back as the "title" instead of summarizing. The job should
    # detect this and write a deterministic fallback instead of the echo.
    app, fake, conn = bg_app
    fake.script_chat(content_chunks=["Meow! How are you doing? Meow!"])
    chat = history.create_chat(conn)
    seed_exchange(
        conn,
        chat["id"],
        "meow",
        "Meow! How are you doing? Meow!",
    )

    await on_turn_complete(chat["id"])
    queue = get_queue(app)
    assert queue is not None
    await queue.drain()

    row = history.get_chat(conn, chat["id"])
    assert row is not None
    assert row["title"] == "meow"  # fallback built from the user's message
    assert row["title"] != "Meow! How are you doing? Meow"


async def test_title_not_regenerated_when_set(bg_app) -> None:
    app, fake, conn = bg_app
    chat = history.create_chat(conn)
    seed_exchange(conn, chat["id"], "hello", "world")
    conn.execute(
        "UPDATE chats SET title = ? WHERE id = ?",
        ("Existing Title", chat["id"]),
    )
    conn.commit()

    await on_turn_complete(chat["id"])
    queue = get_queue(app)
    assert queue is not None
    await queue.drain()

    row = history.get_chat(conn, chat["id"])
    assert row["title"] == "Existing Title"
    assert fake.chat_requests == []


async def test_summary_cadence_every_two_turns(tmp_path: Path) -> None:
    fake = FakeLlamaSwap()
    conn = open_db(tmp_path / "sum.db")
    trace_mod.reset_connection(conn)
    config = make_config(summary_every_n_turns=2)
    llm = make_llm(fake)
    app = SimpleNamespace(
        state=SimpleNamespace(config=config, db=conn, llm_client=llm, summary_llm_client=llm)
    )
    await start(app)
    try:
        chat = history.create_chat(conn)
        chat_id = chat["id"]

        # Turn 1: title only
        fake.script_chat(content_chunks=["First Exchange Title"])
        seed_exchange(conn, chat_id, "u1", "a1")
        await on_turn_complete(chat_id)
        await get_queue(app).drain()
        assert history.get_chat(conn, chat_id)["summary"] is None
        assert fake.chat_requests[-1]["model"] == "dispatcher"

        # Turn 2: summary
        fake.script_chat(content_chunks=["Summary after two turns."])
        seed_exchange(conn, chat_id, "u2", "a2")
        await on_turn_complete(chat_id)
        await get_queue(app).drain()
        summary_v1 = history.get_chat(conn, chat_id)["summary"]
        assert summary_v1 == "Summary after two turns."
        assert fake.chat_requests[-1]["model"] == config.background.summary_model
        assert fake.chat_requests[-1]["model"] == "utility"

        # Turn 3: neither
        n_before = len(fake.chat_requests)
        seed_exchange(conn, chat_id, "u3", "a3")
        await on_turn_complete(chat_id)
        await get_queue(app).drain()
        assert len(fake.chat_requests) == n_before
        assert history.get_chat(conn, chat_id)["summary"] == summary_v1

        # Turn 4: summary again
        fake.script_chat(content_chunks=["Summary after four turns."])
        seed_exchange(conn, chat_id, "u4", "a4")
        await on_turn_complete(chat_id)
        await get_queue(app).drain()
        assert history.get_chat(conn, chat_id)["summary"] == "Summary after four turns."
        assert fake.chat_requests[-1]["model"] == "utility"
    finally:
        await stop(app)
        await llm.close()
        trace_mod.reset_connection(None)
        conn.close()


async def test_utility_failure_leaves_chat_unaffected(bg_app) -> None:
    app, fake, conn = bg_app
    # Title job retries once → two 500s consume both attempts
    fake.script_chat(status_code=500, error_body="boom1")
    fake.script_chat(status_code=500, error_body="boom2")
    chat = history.create_chat(conn)
    seed_exchange(conn, chat["id"], "will this break?", "chat must survive.")

    await on_turn_complete(chat["id"])  # must not raise
    await get_queue(app).drain()

    row = history.get_chat(conn, chat["id"])
    assert row["title"] is None
    msgs = history.list_messages(conn, chat["id"])
    assert len(msgs) == 2
    assert msgs[0]["content"] == "will this break?"
    assert msgs[1]["content"] == "chat must survive."


async def test_retry_then_succeed_writes_title(bg_app) -> None:
    app, fake, conn = bg_app
    fake.script_chat(status_code=500, error_body="transient")
    fake.script_chat(content_chunks=["Recovered Title Ok"])
    chat = history.create_chat(conn)
    seed_exchange(conn, chat["id"], "retry me", "ok")

    await on_turn_complete(chat["id"])
    await get_queue(app).drain()

    row = history.get_chat(conn, chat["id"])
    assert row["title"] == "Recovered Title Ok"
    assert len(fake.chat_requests) == 2
