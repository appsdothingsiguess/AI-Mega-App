"""GPU delegation package (PLAN.md §4.1, docs/FEATURES.md F14).

Re-exports the public surface that settings-api wires at startup:
  - router (APIRouter, mounted at /api/gpu)
  - generate (Config → YAML str)
  - start_rewarm (app → None, creates background task)
"""

from app.gpu.api import router
from app.gpu.rewarm import start_rewarm
from app.gpu.swapgen import generate

__all__ = ["generate", "router", "start_rewarm"]
