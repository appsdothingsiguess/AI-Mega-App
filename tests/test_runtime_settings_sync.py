"""Tests for propagating reloaded settings to cached services."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import DebugSettings, Settings, get_settings
from app.main import _apply_runtime_settings, _build_services, app
from app.settings_store import init_settings_store, update_settings


def test_apply_runtime_settings_syncs_orchestrator_debug_flags() -> None:
    settings, projects, orchestrator, _vector_store = _build_services()
    orchestrator.settings = orchestrator.settings.model_copy(
        update={"debug": DebugSettings(sse_trace=False, router_decisions=False)}
    )
    app.state.settings = settings
    app.state.orchestrator = orchestrator
    app.state.projects = projects

    fresh = settings.model_copy(
        update={"debug": DebugSettings(sse_trace=True, router_decisions=True)}
    )
    _apply_runtime_settings(fresh)

    assert app.state.settings.debug.sse_trace is True
    assert orchestrator.settings.debug.sse_trace is True
    assert orchestrator.router.settings.debug.sse_trace is True


def test_settings_reload_from_disk_syncs_orchestrator_sse_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates PUT /settings: disk reload must reach cached orchestrator."""
    monkeypatch.chdir(tmp_path)
    settings_file = tmp_path / "settings.json"
    monkeypatch.setenv("SETTINGS_JSON_PATH", str(settings_file))
    projects = tmp_path / "projects"
    projects.mkdir()
    data = tmp_path / "data"
    data.mkdir()

    get_settings.cache_clear()
    init_settings_store()

    settings, projects_mgr, orchestrator, _vector_store = _build_services(
        get_settings()
    )
    orchestrator.settings = orchestrator.settings.model_copy(
        update={"debug": DebugSettings(sse_trace=False)}
    )
    app.state.settings = settings
    app.state.orchestrator = orchestrator
    app.state.projects = projects_mgr

    update_settings({"debug": {"sse_trace": True}})
    fresh = get_settings()
    _apply_runtime_settings(fresh)

    assert fresh.debug.sse_trace is True
    assert orchestrator.settings.debug.sse_trace is True
    assert orchestrator.settings is fresh
