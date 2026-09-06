"""Surviving GPU service helpers.

The retired FastAPI GPU control route and resident-model rewarm lifecycle are
preserved under ``retired/app/gpu``. Active operational code uses inventory
and swap generation directly.
"""

from app.gpu.swapgen import generate

__all__ = ["generate"]
