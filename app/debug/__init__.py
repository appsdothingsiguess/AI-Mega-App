"""Debug tracing package (PLAN.md §4.16, docs/FEATURES.md A3). Critical
infrastructure built FIRST — every later pipeline stage writes spans through
`new_trace`/`span`, re-exported here for convenience:

    from app.debug import new_trace, span

See app/debug/trace.py for the full API doc and the Phase-1 stage-name
vocabulary (route, llm_request, llm_stream, sse_emit, swap_wait, db).
"""

from __future__ import annotations

from app.debug.trace import SpanHandle, new_trace, span

__all__ = ["new_trace", "span", "SpanHandle"]
