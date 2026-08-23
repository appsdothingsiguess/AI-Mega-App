"""Trust boundary for rolling-summary coverage metadata.

Coverage lives in the existing ``summary`` span data.  A count alone is
not evidence that the committed chat summary represents that prefix, so a
record is usable only while both its covered-prefix and summary fingerprints
still match the current persisted chat state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


COVERED_COUNT_FIELD = "covered_message_count"
COVERED_PREFIX_SHA256_FIELD = "covered_prefix_sha256"
SUMMARY_SHA256_FIELD = "summary_sha256"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def covered_prefix_sha256(messages: list[dict[str, Any]]) -> str:
    """Fingerprint an ordered message prefix deterministically.

    Sorting mapping keys makes the encoding independent of dict insertion
    order while preserving the message-list order that defines coverage.
    ``default=str`` keeps the helper safe if a future history field uses a
    non-JSON scalar; such a shape change invalidates old records safely.
    """
    payload = json.dumps(
        messages,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return _sha256(payload)


def summary_sha256(summary: str) -> str:
    return _sha256(summary.encode("utf-8"))


def coverage_fields(
    history: list[dict[str, Any]], covered_count: int, summary: str
) -> dict[str, Any]:
    """Fields written into a successful existing ``summary`` span."""
    if covered_count < 0 or covered_count > len(history):
        raise ValueError("covered_count is outside the current history")
    return {
        COVERED_COUNT_FIELD: covered_count,
        COVERED_PREFIX_SHA256_FIELD: covered_prefix_sha256(history[:covered_count]),
        SUMMARY_SHA256_FIELD: summary_sha256(summary),
    }


def _latest_summary_data(
    conn: sqlite3.Connection, chat_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT s.data AS data FROM spans s "
        "JOIN traces t ON t.trace_id = s.trace_id "
        "WHERE t.chat_id = ? AND s.stage = 'summary' "
        "ORDER BY s.started_at DESC, s.id DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    if row is None or row["data"] is None:
        return None
    try:
        data = json.loads(row["data"])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def trusted_covered_count(
    conn: sqlite3.Connection,
    chat_id: str,
    history: list[dict[str, Any]],
    summary: str | None,
) -> int | None:
    """Return the current trusted prefix count, or no trusted coverage.

    The newest summary attempt is authoritative.  Failed, partial, malformed,
    or metadata-less attempts therefore force the conservative raw-history
    path even when an older record once matched.
    """
    if not summary:
        return None
    data = _latest_summary_data(conn, chat_id)
    if data is None or data.get("error"):
        return None

    count = data.get(COVERED_COUNT_FIELD)
    if isinstance(count, bool) or not isinstance(count, int):
        return None
    if count < 0 or count > len(history):
        return None

    prefix_fingerprint = data.get(COVERED_PREFIX_SHA256_FIELD)
    committed_summary_fingerprint = data.get(SUMMARY_SHA256_FIELD)
    if not isinstance(prefix_fingerprint, str) or not isinstance(
        committed_summary_fingerprint, str
    ):
        return None
    if prefix_fingerprint != covered_prefix_sha256(history[:count]):
        return None
    if committed_summary_fingerprint != summary_sha256(summary):
        return None
    return count


__all__ = [
    "COVERED_COUNT_FIELD",
    "COVERED_PREFIX_SHA256_FIELD",
    "SUMMARY_SHA256_FIELD",
    "coverage_fields",
    "covered_prefix_sha256",
    "summary_sha256",
    "trusted_covered_count",
]
