"""Tests for LM Studio models/server API proxies."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

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
        lmstudio_model="test-model",
        lmstudio_mode="llm",
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.main.get_settings", lambda: s)
    monkeypatch.setattr("app.config.get_settings", lambda: s)
    monkeypatch.setattr("app.settings_store.get_settings", lambda: s)
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_models(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    mock_lm = MagicMock()
    mock_lm.list_models_catalog.return_value = [
        MagicMock(
            key="publisher/model-a",
            display_name="Model A",
            type="llm",
            loaded=True,
            vision=False,
            params_string="7B",
        ),
        MagicMock(
            key="embed-model",
            display_name="Embed",
            type="embedding",
            loaded=False,
            vision=False,
            params_string=None,
        ),
    ]
    monkeypatch.setattr(
        "app.main._services",
        lambda: (settings, MagicMock(), MagicMock(), mock_lm),
    )
    resp = client.get("/lmstudio/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["selected_model"] == settings.lmstudio_model
    assert len(body["models"]) == 2
    assert body["models"][0]["loaded"] is True


def test_load_model(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_lm = MagicMock()
    mock_lm.load_model.return_value = MagicMock(
        ok=True,
        model="publisher/model-a",
        status="loaded",
        instance_id="publisher/model-a",
        load_time_seconds=1.2,
        message="ok",
    )
    monkeypatch.setattr(
        "app.main._services",
        lambda: (get_settings(), MagicMock(), MagicMock(), mock_lm),
    )
    resp = client.post(
        "/lmstudio/models/load",
        json={"model": "publisher/model-a"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "loaded"
    mock_lm.load_model.assert_called_once_with("publisher/model-a", context_length=None)


def test_settings_put_trailing_slash(client: TestClient) -> None:
    """Trailing slash must not hit StaticFiles (405)."""
    resp = client.put("/settings/", json={"rag": {"top_k": 6}})
    assert resp.status_code == 200
    assert resp.json()["rag"]["top_k"] == 6


def test_server_config_roundtrip(tmp_path: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "http-server-config.json"
    cfg_path.write_text(
        json.dumps({"port": 1234, "networkInterface": "127.0.0.1"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.lmstudio_server_config.find_config_path",
        lambda: cfg_path,
    )
    monkeypatch.setattr(
        "app.lmstudio_server_config.config_search_paths",
        lambda: [cfg_path],
    )

    get_resp = client.get("/lmstudio/server")
    assert get_resp.status_code == 200
    assert get_resp.json()["serve_on_local_network"] is False

    put_resp = client.put(
        "/lmstudio/server",
        json={"serve_on_local_network": True},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["serve_on_local_network"] is True
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["networkInterface"] == "0.0.0.0"
