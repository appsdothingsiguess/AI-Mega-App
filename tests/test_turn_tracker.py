"""Tests for app/turn_tracker.py."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from app.turn_tracker import TurnRecord, TurnTracker


def _make_record(turn_id: str = "t1", project_id: str = "p1", thread_id: str = "th1") -> TurnRecord:
    return TurnRecord(
        turn_id=turn_id,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        project_id=project_id,
        thread_id=thread_id,
        user_input="hello world",
    )


class TestTurnTrackerBasic:
    def test_last_returns_none_when_empty(self):
        tracker = TurnTracker()
        assert tracker.last() is None

    def test_record_and_last(self):
        tracker = TurnTracker()
        rec = _make_record("t1")
        tracker.record(rec)
        result = tracker.last()
        assert result is not None
        assert result.turn_id == "t1"

    def test_all_returns_empty_list_initially(self):
        tracker = TurnTracker()
        assert tracker.all() == []

    def test_all_returns_most_recent_first(self):
        tracker = TurnTracker()
        for i in range(3):
            tracker.record(_make_record(turn_id=f"t{i}"))
        result = tracker.all()
        assert result[0].turn_id == "t2"
        assert result[1].turn_id == "t1"
        assert result[2].turn_id == "t0"

    def test_last_returns_most_recently_added(self):
        tracker = TurnTracker()
        tracker.record(_make_record("first"))
        tracker.record(_make_record("second"))
        assert tracker.last().turn_id == "second"  # type: ignore[union-attr]


class TestRingBufferEviction:
    def test_ring_buffer_evicts_oldest_at_maxlen(self):
        tracker = TurnTracker(max_entries=3)
        for i in range(4):
            tracker.record(_make_record(turn_id=f"t{i}"))
        result = tracker.all()
        assert len(result) == 3
        ids = [r.turn_id for r in result]
        assert "t0" not in ids
        assert "t1" in ids
        assert "t3" in ids

    def test_default_max_entries_is_10(self):
        tracker = TurnTracker()
        for i in range(11):
            tracker.record(_make_record(turn_id=f"t{i}"))
        result = tracker.all()
        assert len(result) == 10
        assert result[0].turn_id == "t10"
        assert all(r.turn_id != "t0" for r in result)


class TestToJson:
    def test_to_json_returns_dict(self):
        tracker = TurnTracker()
        rec = _make_record("tj1")
        result = tracker.to_json(rec)
        assert isinstance(result, dict)
        assert result["turn_id"] == "tj1"

    def test_to_json_includes_all_fields(self):
        tracker = TurnTracker()
        rec = _make_record("tj2")
        rec.intent = "general_chat"
        rec.route_source = "keyword"
        rec.total_elapsed_ms = 123.4
        result = tracker.to_json(rec)
        assert result["intent"] == "general_chat"
        assert result["route_source"] == "keyword"
        assert result["total_elapsed_ms"] == 123.4

    def test_to_json_nested_lists_serializable(self):
        import json
        tracker = TurnTracker()
        rec = _make_record("tj3")
        rec.tools_invoked = [{"name": "web_search", "elapsed_ms": 50.0}]
        rec.rag_sources = [{"source": "doc.md", "score": 0.9}]
        result = tracker.to_json(rec)
        serialized = json.dumps(result)
        assert "web_search" in serialized
        assert "doc.md" in serialized


class TestThreadSafety:
    def test_concurrent_records_do_not_corrupt_buffer(self):
        tracker = TurnTracker(max_entries=50)
        errors: list[Exception] = []

        def write_records(prefix: str, count: int) -> None:
            try:
                for i in range(count):
                    tracker.record(_make_record(turn_id=f"{prefix}-{i}"))
                    time.sleep(0)  # yield to other threads
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=write_records, args=(f"thread{t}", 10))
            for t in range(5)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"Exceptions in threads: {errors}"
        result = tracker.all()
        assert len(result) == 50
        for rec in result:
            assert rec.turn_id != ""
            assert rec.project_id == "p1"
