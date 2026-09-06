"""Settings overlay store + API package (docs/FEATURES.md A1 / F4).

Store is sync; API layer wraps writes with ``settings_write`` debug spans.
"""

from __future__ import annotations

from app.settings.store import (
    get_effective,
    read_overlay,
    update_model,
    update_routing,
)

__all__ = [
    "get_effective",
    "read_overlay",
    "update_model",
    "update_routing",
]
