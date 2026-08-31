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
from dataclasses import dataclass
from typing import Any, Literal


COVERED_COUNT_FIELD = "covered_message_count"
COVERED_PREFIX_SHA256_FIELD = "covered_prefix_sha256"
SUMMARY_SHA256_FIELD = "summary_sha256"
CoverageReason = Literal[
    "ok",
    "no_summary",
    "failed_summary",
    "missing_metadata",
    "count_out_of_range",
    "prefix_mismatch",
    "summary_mismatch",
]


@dataclass(frozen=True)
class CoverageVerdict:
    """Conservative decision about whether a committed summary covers history."""

    trusted: bool
    covered_count: int | None
    reason: CoverageReason


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


def coverage_verdict(
    conn: sqlite3.Connection,
    chat_id: str,
    history: list[dict[str, Any]],
    summary: str | None,
) -> CoverageVerdict:
    """Classify the newest summary span against the committed summary.

    The newest summary attempt is authoritative.  Failed, partial, malformed,
    or metadata-less attempts therefore force the conservative raw-history
    path even when an older record once matched.
    """
    if not summary:
        return CoverageVerdict(False, None, "no_summary")
    data = _latest_summary_data(conn, chat_id)
    if data is None:
        return CoverageVerdict(False, None, "missing_metadata")
    error = data.get("error")
    if error:
        if isinstance(error, str):
            return CoverageVerdict(False, None, "failed_summary")
        return CoverageVerdict(False, None, "missing_metadata")

    count = data.get(COVERED_COUNT_FIELD)
    if isinstance(count, bool) or not isinstance(count, int):
        return CoverageVerdict(False, None, "missing_metadata")
    if count < 0 or count > len(history):
        return CoverageVerdict(False, None, "count_out_of_range")

    prefix_fingerprint = data.get(COVERED_PREFIX_SHA256_FIELD)
    committed_summary_fingerprint = data.get(SUMMARY_SHA256_FIELD)
    if not isinstance(prefix_fingerprint, str) or not isinstance(
        committed_summary_fingerprint, str
    ):
        return CoverageVerdict(False, None, "missing_metadata")
    if prefix_fingerprint != covered_prefix_sha256(history[:count]):
        return CoverageVerdict(False, None, "prefix_mismatch")
    if committed_summary_fingerprint != summary_sha256(summary):
        return CoverageVerdict(False, None, "summary_mismatch")
    return CoverageVerdict(True, count, "ok")


def trusted_covered_count(
    conn: sqlite3.Connection,
    chat_id: str,
    history: list[dict[str, Any]],
    summary: str | None,
) -> int | None:
    """Return the trusted prefix count, preserving the legacy API."""
    verdict = coverage_verdict(conn, chat_id, history, summary)
    return verdict.covered_count if verdict.trusted else None


__all__ = [
    "COVERED_COUNT_FIELD",
    "COVERED_PREFIX_SHA256_FIELD",
    "SUMMARY_SHA256_FIELD",
    "CoverageReason",
    "CoverageVerdict",
    "coverage_fields",
    "covered_prefix_sha256",
    "coverage_verdict",
    "summary_sha256",
    "trusted_covered_count",
]
