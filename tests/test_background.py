"""Background queue titles + summaries (PLAN.md §4.15, FEATURES F18)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.background import on_turn_complete, start, stop
from app.background.queue import get_queue
from app.background.summaries import summary_status
from app.background.titles import clean_title, is_echo
from app.chat import history
from app.config import (
    BackgroundConfig,
    Config,
    DbConfig,
    DefaultsConfig,
    LlamaSwapConfig,
    ModelEntry,
    RoutingConfig,
    RoutingIntents,
)
from app.db import open_db
from app.debug import trace as trace_mod
from app.llm_client import LLMClient
from tests.fakes import FakeLlamaSwap

BASE_URL = "http://fake-llama-swap/v1/"

TEST_MODEL = ModelEntry(
    name="chat-default",
    **{"class": "general"},
    ctx=4096,
    gpu=0,
    tool_call="native",
    max_tokens=1024,
    file="/models/chat-default.gguf",
    quant="Q4_K_M",
)

TEST_UTILITY_MODEL = ModelEntry(
    name="utility",
    **{"class": "utility"},
    ctx=4096,
    gpu="cpu",
    tool_call="none",
    max_tokens=512,
    file="/models/utility.gguf",
    quant="Q4_K_M",
)


def make_llm(fake: FakeLlamaSwap, timeout_s: float = 5.0) -> LLMClient:
    client = LLMClient(base_url=BASE_URL, timeout_s=timeout_s)
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app),
        base_url=BASE_URL,
        timeout=timeout_s,
    )
    return client


def make_config(*, summary_every_n_turns: int = 6) -> Config:
    return Config(
        llama_swap=LlamaSwapConfig(base_url=BASE_URL, timeout_s=5.0),
        db=DbConfig(path=":memory:"),
        models=[TEST_MODEL, TEST_UTILITY_MODEL],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="utility",
            title_model="dispatcher",
        ),
        background=BackgroundConfig(
            title_model="dispatcher",
            summary_model="utility",
            summary_every_n_turns=summary_every_n_turns,
        ),
    )


def seed_exchange(conn, chat_id: str, user: str, asst: str) -> None:
    history.insert_message(conn, chat_id, "user", user, None)
    history.insert_message(conn, chat_id, "assistant", asst, "chat-default")


@pytest.fixture
async def bg_app(tmp_path: Path):
    """SimpleNamespace app with config/db/llm + started background queue."""
    fake = FakeLlamaSwap()
    conn = open_db(tmp_path / "bg.db")
    trace_mod.reset_connection(conn)
    config = make_config()
    llm = make_llm(fake)
    app = SimpleNamespace(
        state=SimpleNamespace(config=config, db=conn, llm_client=llm, summary_llm_client=llm)
    )
    await start(app)
    try:
        yield app, fake, conn
    finally:
        await stop(app)
        await llm.close()
        trace_mod.reset_connection(None)
        conn.close()


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


def seed_llm_stream_usage(
    conn, chat_id: str, prompt_tokens: int, model: str | None = None
) -> None:
    """Insert a trace + llm_stream span carrying real usage.prompt_tokens
    (+ optionally the model that turn ran on), as the orchestrator would
    after a real chat turn -- this is the signal maybe_enqueue_summary
    reads to trigger on token pressure."""
    trace_id = f"trace-{chat_id}-{uuid.uuid4().hex}"
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO traces (trace_id, chat_id, started_at) VALUES (?, ?, ?)",
        (trace_id, chat_id, now_ms),
    )
    payload: dict = {"usage": {"prompt_tokens": prompt_tokens}}
    if model is not None:
        payload["model"] = model
    data = json.dumps(payload)
    conn.execute(
        "INSERT INTO spans (trace_id, stage, started_at, ended_at, data) "
        "VALUES (?, 'llm_stream', ?, ?, ?)",
        (trace_id, now_ms, now_ms, data),
    )
    conn.commit()


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
