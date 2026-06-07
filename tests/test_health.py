"""Tests for the spec-shaped /health endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _patch_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    settings_file = tmp_path / "settings.json"
    monkeypatch.setenv("SETTINGS_JSON_PATH", str(settings_file))
    projects = tmp_path / "projects"
    projects.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    s = Settings(
        projects_dir=projects,
        data_dir=data,
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.main.get_settings", lambda: s)
    monkeypatch.setattr("app.config.get_settings", lambda: s)
    monkeypatch.setattr("app.settings_store.get_settings", lambda: s)

    async def _noop_validate(_settings: Settings) -> tuple[list[str], list[str]]:
        return [], []

    monkeypatch.setattr("app.main.validate_config", _noop_validate)
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _patch_health_checks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ollama: dict[str, Any] | None = None,
    qdrant: dict[str, Any] | None = None,
    litellm: dict[str, Any] | None = None,
    remote_provider: dict[str, Any] | None = None,
) -> None:
    async def _ollama(_settings: Settings) -> dict[str, Any]:
        return ollama or {"status": "up", "loaded_models": ["qwen3:8b"]}

    async def _qdrant(_settings: Settings) -> dict[str, Any]:
        return qdrant or {"status": "up", "collections": 2}

    def _litellm(_settings: Settings) -> dict[str, Any]:
        return litellm or {"status": "up"}

    async def _remote(_settings: Settings) -> dict[str, Any]:
        return remote_provider or {"status": "up"}

    monkeypatch.setattr("app.main._check_ollama_health", _ollama)
    monkeypatch.setattr("app.main._check_qdrant_health", _qdrant)
    monkeypatch.setattr("app.main._check_litellm_health", _litellm)
    monkeypatch.setattr("app.main._check_remote_provider_health", _remote)


def test_health_all_services_up(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_health_checks(monkeypatch)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["services"]["ollama"]["status"] == "up"
    assert body["services"]["qdrant"]["status"] == "up"
    assert body["services"]["litellm"]["status"] == "up"
    assert body["services"]["remote_provider"]["status"] == "up"


def test_health_ollama_down_is_degraded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_health_checks(
        monkeypatch,
        ollama={"status": "down", "error": "connection refused", "loaded_models": []},
    )
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["services"]["ollama"]["status"] == "down"
    assert "error" in body["services"]["ollama"]


def test_health_qdrant_down_is_degraded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_health_checks(
        monkeypatch,
        qdrant={"status": "down", "error": "connection refused", "collections": 0},
    )
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["services"]["qdrant"]["status"] == "down"
    assert "error" in body["services"]["qdrant"]


def test_health_remote_provider_down_is_degraded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_health_checks(
        monkeypatch,
        remote_provider={"status": "down", "error": "403 Forbidden"},
    )
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["services"]["remote_provider"]["status"] == "down"
    assert body["services"]["remote_provider"]["error"] == "403 Forbidden"


def test_health_down_when_no_model_path_available(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_health_checks(
        monkeypatch,
        ollama={"status": "down", "error": "down", "loaded_models": []},
        remote_provider={"status": "down", "error": "down"},
        litellm={"status": "down", "error": "missing aliases"},
    )
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "down"
