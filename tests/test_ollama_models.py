"""Tests for the GET /ollama/models endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
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
    s = Settings(projects_dir=projects, data_dir=data)
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


def test_ollama_models_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "models": [
                    {"name": "qwen3:8b-32k"},
                    {"name": "qwen3-coder:30b-16k"},
                ]
            }

    class _FakeAsyncClient:
        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, _url: str) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(
        "app.main.httpx.AsyncClient", lambda *args, **kwargs: _FakeAsyncClient()
    )

    resp = client.get("/ollama/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is True
    assert body["models"] == ["qwen3:8b-32k", "qwen3-coder:30b-16k"]


def test_ollama_models_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeAsyncClient:
        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, _url: str) -> None:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        "app.main.httpx.AsyncClient", lambda *args, **kwargs: _FakeAsyncClient()
    )

    resp = client.get("/ollama/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert body["models"] == []
