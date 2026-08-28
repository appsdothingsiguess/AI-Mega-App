"""Rolling-summary coverage trust verdict (docs/FEATURES.md F19).

Pins the structured `coverage_verdict` reasons, the preserved
`trusted_covered_count` behaviour, newest-attempt authority, and the
Debug summary-status API payload's new `coverage` field.
"""

from __future__ import annotations

import json
import time
import uuid

from app.debug import api as debug_api
from app.background.summary_coverage import (
    COVERAGE_REASON_FAILED_SUMMARY,
    COVERAGE_REASON_MISSING_METADATA,
    COVERAGE_REASON_NO_SUMMARY,
    COVERAGE_REASON_OK,
    COVERAGE_REASON_COUNT_OUT_OF_RANGE,
    COVERAGE_REASON_PREFIX_MISMATCH,
    COVERAGE_REASON_SUMMARY_MISMATCH,
    coverage_fields,
    coverage_verdict,
    covered_prefix_sha256,
    summary_sha256,
    trusted_covered_count,
)
from tests.background_fixtures import *  # noqa: F401,F403


def _now_ms() -> int:
    return int(time.time() * 1000)


def _insert_summary_span(
    conn, chat_id: str, data: dict | str | list, started_at: int | None = None
) -> str:
    trace_id = f"trace-{chat_id}-{uuid.uuid4().hex}"
    now = started_at if started_at is not None else _now_ms()
    conn.execute(
        "INSERT INTO traces (trace_id, chat_id, started_at) VALUES (?, ?, ?)",
        (trace_id, chat_id, now),
    )
    conn.execute(
        "INSERT INTO spans (trace_id, stage, started_at, ended_at, data) "
        "VALUES (?, 'summary', ?, ?, ?)",
        (
            trace_id,
            now,
            now,
            json.dumps(data) if not isinstance(data, str) else data,
        ),
    )
    conn.commit()
    return trace_id


def _valid_data(messages: list[dict], count: int, summary: str) -> dict:
    return coverage_fields(messages, count, summary)


def _msgs(conn, chat_id: str) -> list[dict]:
    return history.list_messages(conn, chat_id)


# --- verdict reasons -------------------------------------------------------


async def test_verdict_no_summary_when_no_committed_summary(bg_app) -> None:
    """No committed chat summary is exactly `no_summary` (empty string too)."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)

    v = coverage_verdict(conn, chat_id, msgs, None)
    assert v.reason == COVERAGE_REASON_NO_SUMMARY
    assert v.trusted is False
    assert v.covered_count is None

    v_empty = coverage_verdict(conn, chat_id, msgs, "")
    assert v_empty.reason == COVERAGE_REASON_NO_SUMMARY


async def test_verdict_missing_metadata_when_no_summary_span(bg_app) -> None:
    """A chat with a committed summary but no summary span at all is
    `missing_metadata`, not a crash."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)
    _insert_summary_span(conn, chat_id, _valid_data(msgs, 2, "sum"))
    # Delete the summary span so there is no summary span for this chat.
    conn.execute("DELETE FROM spans WHERE stage = 'summary'")
    conn.commit()

    v = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v.reason == COVERAGE_REASON_MISSING_METADATA


async def test_verdict_failed_summary_when_newest_span_has_error(bg_app) -> None:
    """A newest span carrying `error` is `failed_summary`."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)
    _insert_summary_span(conn, chat_id, {"error": "model exploded"})

    v = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v.reason == COVERAGE_REASON_FAILED_SUMMARY


async def test_verdict_missing_metadata_on_malformed_span_data(bg_app) -> None:
    """Malformed span data (non-dict) is `missing_metadata`."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)
    _insert_summary_span(conn, chat_id, "this is not a dict")

    v = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v.reason == COVERAGE_REASON_MISSING_METADATA


async def test_verdict_missing_metadata_on_wrong_type_count(bg_app) -> None:
    """A coverage count that is not an int (string / bool / float) is
    `missing_metadata` -- same bucket as absent fields."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)
    data = _valid_data(msgs, 2, "sum")
    data["covered_message_count"] = "2"
    _insert_summary_span(conn, chat_id, data)

    v = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v.reason == COVERAGE_REASON_MISSING_METADATA


async def test_verdict_count_out_of_range(bg_app) -> None:
    """A count beyond the history length is `count_out_of_range`."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)
    data = _valid_data(msgs, 2, "sum")
    data["covered_message_count"] = len(msgs) + 1
    _insert_summary_span(conn, chat_id, data)

    v = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v.reason == COVERAGE_REASON_COUNT_OUT_OF_RANGE


async def test_verdict_prefix_mismatch(bg_app) -> None:
    """A valid count whose covered-prefix fingerprint no longer matches is
    `prefix_mismatch`."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)
    data = _valid_data(msgs, 2, "sum")
    data["covered_prefix_sha256"] = covered_prefix_sha256(msgs[:1])  # wrong prefix
    _insert_summary_span(conn, chat_id, data)

    v = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v.reason == COVERAGE_REASON_PREFIX_MISMATCH


async def test_verdict_summary_mismatch(bg_app) -> None:
    """A valid count whose summary fingerprint no longer matches is
    `summary_mismatch`."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)
    data = _valid_data(msgs, 2, "sum")
    data["summary_sha256"] = summary_sha256("a different summary")  # wrong summary
    _insert_summary_span(conn, chat_id, data)

    v = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v.reason == COVERAGE_REASON_SUMMARY_MISMATCH


async def test_verdict_ok_when_everything_matches(bg_app) -> None:
    """A fully valid, matching record is trusted with reason `ok`."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)
    _insert_summary_span(conn, chat_id, _valid_data(msgs, 2, "sum"))

    v = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v.reason == COVERAGE_REASON_OK
    assert v.trusted is True
    assert v.covered_count == 2


# --- trusted_covered_count preservation ------------------------------------


async def test_trusted_covered_count_delegates_to_verdict(bg_app) -> None:
    """The count helper returns the count only when trusted, else None --
    preserving its original public contract."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)

    # No summary committed -> None.
    assert trusted_covered_count(conn, chat_id, msgs, None) is None

    # A valid, matching record -> the covered count.
    _insert_summary_span(conn, chat_id, _valid_data(msgs, 2, "sum"))
    assert trusted_covered_count(conn, chat_id, msgs, "sum") == 2

    # A failed newest attempt -> None (conservative raw-history path).
    _insert_summary_span(conn, chat_id, {"error": "boom"})
    assert trusted_covered_count(conn, chat_id, msgs, "sum") is None


async def test_newest_failed_attempt_overrides_older_valid_one(bg_app) -> None:
    """A failed/malformed newest attempt stays authoritative over an older
    good one: the older count must NOT leak through as trusted."""
    app, _, conn = bg_app
    chat_id = history.create_chat(conn)["id"]
    seed_exchange(conn, chat_id, "u1", "a1")
    msgs = _msgs(conn, chat_id)

    older_trace = _insert_summary_span(
        conn, chat_id, _valid_data(msgs, 2, "sum"), started_at=_now_ms() - 10_000
    )
    assert trusted_covered_count(conn, chat_id, msgs, "sum") == 2

    # A newer, failed attempt supersedes the older valid one.
    _insert_summary_span(conn, chat_id, {"error": "boom"}, started_at=_now_ms())

    v = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v.reason == COVERAGE_REASON_FAILED_SUMMARY
    assert trusted_covered_count(conn, chat_id, msgs, "sum") is None

    # A malformed newer attempt is equally authoritative. This chat only has
    # summary spans, so clear them and insert the malformed one alone.
    conn.execute(
        "DELETE FROM spans WHERE trace_id IN "
        "(SELECT trace_id FROM traces WHERE chat_id = ?)",
        (chat_id,),
    )
    conn.execute("DELETE FROM traces WHERE chat_id = ?", (chat_id,))
    conn.commit()
    _insert_summary_span(conn, chat_id, "not a dict", started_at=_now_ms() + 10_000)
    v2 = coverage_verdict(conn, chat_id, msgs, "sum")
    assert v2.reason == COVERAGE_REASON_MISSING_METADATA
    assert trusted_covered_count(conn, chat_id, msgs, "sum") is None


# --- API payload -----------------------------------------------------------


async def test_summary_status_api_includes_coverage_payload(bg_app) -> None:
    """The Debug summary-status endpoint keeps its existing fields and adds
    `coverage` computed from the verdict (not a duplicate check)."""
    app, _, conn = bg_app
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.config import (
        BackgroundConfig,
        Config,
        DbConfig,
        DefaultsConfig,
        LlamaSwapConfig,
        ModelEntry,
    )

    chat_model = ModelEntry(
        name="chat-default", **{"class": "general"}, ctx=4096, gpu=0,
        tool_call="native", max_tokens=1024,
        file="/models/chat-default.gguf", quant="Q4_K_M",
    )
    config = Config(
        llama_swap=LlamaSwapConfig(base_url="http://fake/v1/", timeout_s=5.0),
        db=DbConfig(path=":memory:"),
        models=[chat_model],
        defaults=DefaultsConfig(
            chat_model="chat-default", utility_model="utility", title_model="dispatcher"
        ),
        background=BackgroundConfig(title_model="dispatcher", summary_model="utility"),
    )
    test_app = FastAPI()
    test_app.state.db = conn
    test_app.state.config = config
    test_app.include_router(debug_api.router)

    chat_id = history.create_chat(conn)["id"]

    with TestClient(test_app) as client:
        resp = client.get("/api/debug/summary-status", params={"chat_id": chat_id})
        assert resp.status_code == 200
        body = resp.json()

        # Existing fields preserved (the coverage addition is additive).
        assert body["source"] == "turn_count_fallback"
        assert body["covered_message_count"] == 0

        # New coverage payload, computed from the verdict -- not a duplicate
        # check: the three keys are exactly trusted / covered_message_count /
        # reason, and they agree with the standalone verdict helper.
        from app.background.summary_coverage import coverage_verdict

        expected = coverage_verdict(
            conn,
            chat_id,
            history.list_messages(conn, chat_id),
            None,
        )
        coverage = body["coverage"]
        assert set(coverage) == {"trusted", "covered_message_count", "reason"}
        assert coverage == {
            "trusted": expected.trusted,
            "covered_message_count": expected.covered_count,
            "reason": expected.reason,
        }
