"""Focused tests for conservative rolling-summary coverage diagnostics."""

from __future__ import annotations

import json
import time
import uuid

import pytest

from app.background.summary_coverage import (
    COVERED_COUNT_FIELD,
    COVERED_PREFIX_SHA256_FIELD,
    SUMMARY_SHA256_FIELD,
    coverage_fields,
    coverage_verdict,
    trusted_covered_count,
)
from app.chat import history
from app.db import open_db
from app.debug import trace as trace_mod


def messages(count: int) -> list[dict]:
    return [{"id": str(i), "role": "user", "content": f"m{i}"} for i in range(count)]


def write_span(conn, chat_id: str, data: str | dict, offset: int = 0) -> None:
    trace_id = uuid.uuid4().hex
    started = int(time.time() * 1000) + offset
    conn.execute("INSERT INTO traces (trace_id, chat_id, started_at) VALUES (?, ?, ?)", (trace_id, chat_id, started))
    conn.execute(
        "INSERT INTO spans (trace_id, stage, started_at, ended_at, data) VALUES (?, 'summary', ?, ?, ?)",
        (trace_id, started, started, data if isinstance(data, str) else json.dumps(data)),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "coverage.db")
    trace_mod.reset_connection(conn)
    try:
        yield conn
    finally:
        trace_mod.reset_connection(None)
        conn.close()


def valid_data(history_rows: list[dict], summary: str = "summary") -> dict:
    return coverage_fields(history_rows, 2, summary)


def test_ok_and_legacy_helper(db):
    chat_id = history.create_chat(db)["id"]
    rows, summary = messages(4), "summary"
    write_span(db, chat_id, valid_data(rows, summary))

    verdict = coverage_verdict(db, chat_id, rows, summary)

    assert (verdict.trusted, verdict.covered_count, verdict.reason) == (True, 2, "ok")
    assert trusted_covered_count(db, chat_id, rows, summary) == 2


def test_no_summary(db):
    chat_id = history.create_chat(db)["id"]
    assert coverage_verdict(db, chat_id, messages(4), None).reason == "no_summary"


def test_missing_span_or_malformed_metadata(db):
    rows, summary = messages(4), "summary"
    chat_id = history.create_chat(db)["id"]
    assert coverage_verdict(db, chat_id, rows, summary).reason == "missing_metadata"

    write_span(db, chat_id, "not-json", offset=1)
    assert coverage_verdict(db, chat_id, rows, summary).reason == "missing_metadata"


def test_failed_summary(db):
    chat_id = history.create_chat(db)["id"]
    write_span(db, chat_id, {"error": "failed"})
    assert coverage_verdict(db, chat_id, messages(4), "summary").reason == "failed_summary"


@pytest.mark.parametrize("count", [-1, 5, True, "2"])
def test_count_classification(db, count):
    chat_id = history.create_chat(db)["id"]
    data = valid_data(messages(4))
    data[COVERED_COUNT_FIELD] = count
    write_span(db, chat_id, data)
    expected = "missing_metadata" if isinstance(count, (bool, str)) else "count_out_of_range"
    assert coverage_verdict(db, chat_id, messages(4), "summary").reason == expected


def test_fingerprint_mismatches(db):
    rows, summary = messages(4), "summary"
    for field, expected in ((COVERED_PREFIX_SHA256_FIELD, "prefix_mismatch"), (SUMMARY_SHA256_FIELD, "summary_mismatch")):
        chat_id = history.create_chat(db)["id"]
        data = valid_data(rows)
        data[field] = "wrong"
        write_span(db, chat_id, data)
        assert coverage_verdict(db, chat_id, rows, summary).reason == expected


def test_newer_failed_attempt_overrides_older_valid(db):
    chat_id = history.create_chat(db)["id"]
    rows, summary = messages(4), "summary"
    write_span(db, chat_id, valid_data(rows, summary), offset=0)
    write_span(db, chat_id, {"error": "latest failed"}, offset=1)
    verdict = coverage_verdict(db, chat_id, rows, summary)
    assert verdict.reason == "failed_summary"
    assert trusted_covered_count(db, chat_id, rows, summary) is None


def test_newer_malformed_attempt_overrides_older_valid(db):
    chat_id = history.create_chat(db)["id"]
    rows, summary = messages(4), "summary"
    write_span(db, chat_id, valid_data(rows, summary), offset=0)
    write_span(db, chat_id, "not-json", offset=1)
    assert coverage_verdict(db, chat_id, rows, summary).reason == "missing_metadata"
