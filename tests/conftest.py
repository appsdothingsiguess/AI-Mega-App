"""Fixtures for the surviving configuration and GPU-generation checks."""

from pathlib import Path

import pytest

from app import config as config_mod


@pytest.fixture(autouse=True)
def isolate_settings_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent tests from reading the developer's local settings overlay."""
    monkeypatch.setattr(config_mod, "OVERLAY_PATH", tmp_path / "no-overlay.yaml")
    config_mod.reset_config_cache()
