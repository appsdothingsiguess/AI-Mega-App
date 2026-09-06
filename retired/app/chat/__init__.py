"""Chat feature package (docs/FEATURES.md A4; PLAN.md §4.2).

The turn loop (orchestrator.py), the REST + SSE surface (api.py), and
message persistence (history.py). No routing/tool logic lives here — those
are Phase 2/3 modules the orchestrator will call through a marked seam.
"""

from __future__ import annotations
