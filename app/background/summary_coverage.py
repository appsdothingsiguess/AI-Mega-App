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
from typing import Any


COVERED_COUNT_FIELD = "covered_message_count"
COVERED_PREFIX_SHA256_FIELD = "covered_prefix_sha256"
SUMMARY_SHA256_FIELD = "summary_sha256"

# Stable, human-facing verdict reasons. Kept string-valued (not an enum) so
# the Debug panel and any external consumer can render them verbatim without
# coupling to an internal enum.
COVERAGE_REASON_OK = "ok"
COVERAGE_REASON_NO_SUMMARY = "no_summary"
COVERAGE_REASON_FAILED_SUMMARY = "failed_summary"
COVERAGE_REASON_MISSING_METADATA = "missing_metadata"
COVERAGE_REASON_COUNT_OUT_OF_RANGE = "count_out_of_range"
COVERAGE_REASON_PREFIX_MISMATCH = "prefix_mismatch"
COVERAGE_REASON_SUMMARY_MISMATCH = "summary_mismatch"

COVERAGE_REASONS: tuple[str, ...] = (
    COVERAGE_REASON_OK,
    COVERAGE_REASON_NO_SUMMARY,
    COVERAGE_REASON_FAILED_SUMMARY,
    COVERAGE_REASON_MISSING_METADATA,
    COVERAGE_REASON_COUNT_OUT_OF_RANGE,
    COVERAGE_REASON_PREFIX_MISMATCH,
    COVERAGE_REASON_SUMMARY_MISMATCH,
)


@dataclass(frozen=True)
class CoverageVerdict:
    """Structured, conservative trust decision for rolling-summary coverage.

    ``trusted`` is True only while both the covered-prefix and the summary
    fingerprints still match the current persisted chat state. ``reason`` is
    one of the stable ``COVERAGE_REASON_*`` strings, which is exactly what the
    Debug panel renders when coverage is not trusted.
    """

    trusted: bool
    covered_count: int | None
    reason: str


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
    """Structured, conservative trust decision for the rolling summary.

    The newest summary attempt is authoritative, so a failed, partial,
    malformed, or metadata-less newest attempt forces the conservative
    "unavailable" verdict even when an older record once matched.

    Classification is precise:

    * no committed chat summary                          -> ``no_summary``
    * newest span carries an ``error``                    -> ``failed_summary``
    * no summary span, malformed span data, or absent /
      wrong-type coverage fields                          -> ``missing_metadata``
    * an out-of-range covered count                       -> ``count_out_of_range``
    * covered-prefix fingerprint mismatch                 -> ``prefix_mismatch``
    * committed-summary fingerprint mismatch              -> ``summary_mismatch``
    * both fingerprints still match the persisted state   -> ``ok`` (trusted)
    """
    if not summary:
        return CoverageVerdict(
            trusted=False,
            covered_count=None,
            reason=COVERAGE_REASON_NO_SUMMARY,
        )

    data = _latest_summary_data(conn, chat_id)
    if data is None:
        return CoverageVerdict(
            trusted=False,
            covered_count=None,
            reason=COVERAGE_REASON_MISSING_METADATA,
        )
    if data.get("error"):
        return CoverageVerdict(
            trusted=False,
            covered_count=None,
            reason=COVERAGE_REASON_FAILED_SUMMARY,
        )

    count = data.get(COVERED_COUNT_FIELD)
    if isinstance(count, bool) or not isinstance(count, int):
        return CoverageVerdict(
            trusted=False,
            covered_count=None,
            reason=COVERAGE_REASON_MISSING_METADATA,
        )
    if count < 0 or count > len(history):
        return CoverageVerdict(
            trusted=False,
            covered_count=None,
            reason=COVERAGE_REASON_COUNT_OUT_OF_RANGE,
        )

    prefix_fingerprint = data.get(COVERED_PREFIX_SHA256_FIELD)
    committed_summary_fingerprint = data.get(SUMMARY_SHA256_FIELD)
    if not isinstance(prefix_fingerprint, str) or not isinstance(
        committed_summary_fingerprint, str
    ):
        return CoverageVerdict(
            trusted=False,
            covered_count=None,
            reason=COVERAGE_REASON_MISSING_METADATA,
        )
    if prefix_fingerprint != covered_prefix_sha256(history[:count]):
        return CoverageVerdict(
            trusted=False,
            covered_count=None,
            reason=COVERAGE_REASON_PREFIX_MISMATCH,
        )
    if committed_summary_fingerprint != summary_sha256(summary):
        return CoverageVerdict(
            trusted=False,
            covered_count=None,
            reason=COVERAGE_REASON_SUMMARY_MISMATCH,
        )
    return CoverageVerdict(
        trusted=True,
        covered_count=count,
        reason=COVERAGE_REASON_OK,
    )


def trusted_covered_count(
    conn: sqlite3.Connection,
    chat_id: str,
    history: list[dict[str, Any]],
    summary: str | None,
) -> int | None:
    """Return the current trusted prefix count, or no trusted coverage.

    Delegates to :func:`coverage_verdict`: the count is returned only when the
    verdict is trusted, otherwise ``None`` (which forces the conservative
    raw-history path). This preserves the original fingerprint and newest-
    summary safety checks exactly -- the verdict only adds a reason.
    """
    verdict = coverage_verdict(conn, chat_id, history, summary)
    return verdict.covered_count if verdict.trusted else None


__all__ = [
    "COVERED_COUNT_FIELD",
    "COVERED_PREFIX_SHA256_FIELD",
    "SUMMARY_SHA256_FIELD",
    "COVERAGE_REASON_OK",
    "COVERAGE_REASON_NO_SUMMARY",
    "COVERAGE_REASON_FAILED_SUMMARY",
    "COVERAGE_REASON_MISSING_METADATA",
    "COVERAGE_REASON_COUNT_OUT_OF_RANGE",
    "COVERAGE_REASON_PREFIX_MISMATCH",
    "COVERAGE_REASON_SUMMARY_MISMATCH",
    "COVERAGE_REASONS",
    "CoverageVerdict",
    "coverage_fields",
    "covered_prefix_sha256",
    "summary_sha256",
    "coverage_verdict",
    "trusted_covered_count",
]
