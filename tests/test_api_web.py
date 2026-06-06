"""TestClient tests for new web API endpoints."""

from __future__ import annotations

import json
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.project_manager import ProjectManager


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


@pytest.fixture
def project_id(client: TestClient) -> str:
    resp = client.post("/projects/init", json={"name": "Test Project"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# /projects/init
# ---------------------------------------------------------------------------

def test_init_project(client: TestClient) -> None:
    resp = client.post("/projects/init", json={"name": "My Project"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Project"
    assert "id" in body


def test_init_project_conflict(client: TestClient, project_id: str) -> None:
    # Try to create with same name → same slug → 409
    resp = client.post("/projects/init", json={"name": "Test Project"})
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# /projects/{id}/instructions
# ---------------------------------------------------------------------------

def test_get_instructions(client: TestClient, project_id: str) -> None:
    resp = client.get(f"/projects/{project_id}/instructions")
    assert resp.status_code == 200
    assert "content" in resp.json()


def test_update_instructions(client: TestClient, project_id: str) -> None:
    resp = client.put(
        f"/projects/{project_id}/instructions",
        json={"content": "Be concise and helpful."},
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "Be concise and helpful."

    # Verify it persists
    resp2 = client.get(f"/projects/{project_id}/instructions")
    assert "concise" in resp2.json()["content"]


# ---------------------------------------------------------------------------
# /projects/{id}/sync
# ---------------------------------------------------------------------------

def test_sync_empty(client: TestClient, project_id: str) -> None:
    resp = client.post(f"/projects/{project_id}/sync")
    assert resp.status_code == 200
    assert resp.json()["chunk_count"] == 0


def test_sync_with_doc(client: TestClient, project_id: str, tmp_path: Path) -> None:
    # Put a doc in the project docs dir via sources upload
    content = b"The quick brown fox jumps over the lazy dog."
    resp = client.post(
        f"/projects/{project_id}/sources",
        files=[("files", ("fox.txt", io.BytesIO(content), "text/plain"))],
    )
    assert resp.status_code == 201
    # Now sync
    resp2 = client.post(f"/projects/{project_id}/sync")
    assert resp2.status_code == 200
    assert resp2.json()["chunk_count"] >= 0  # already ingested via upload


# ---------------------------------------------------------------------------
# /projects/{id}/sources
# ---------------------------------------------------------------------------

def test_get_sources_empty(client: TestClient, project_id: str) -> None:
    resp = client.get(f"/projects/{project_id}/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert "files" in body
    assert body["files"] == []
    assert body["default_new_enabled"] is True


def test_upload_and_list_sources(client: TestClient, project_id: str) -> None:
    content = b"Paris is the capital of France."
    resp = client.post(
        f"/projects/{project_id}/sources",
        files=[("files", ("paris.txt", io.BytesIO(content), "text/plain"))],
    )
    assert resp.status_code == 201
    body = resp.json()
    names = [f["name"] for f in body["files"]]
    assert "paris.txt" in names
    # File should be marked as ingested
    paris = next(f for f in body["files"] if f["name"] == "paris.txt")
    assert paris["ingested"] is True
    assert paris["enabled"] is True


def test_update_sources_enabled(client: TestClient, project_id: str) -> None:
    # Upload two files
    client.post(
        f"/projects/{project_id}/sources",
        files=[
            ("files", ("a.txt", io.BytesIO(b"aaa"), "text/plain")),
            ("files", ("b.txt", io.BytesIO(b"bbb"), "text/plain")),
        ],
    )
    # Enable only a.txt
    resp = client.put(
        f"/projects/{project_id}/sources",
        json={"enabled": ["a.txt"], "default_new_enabled": True},
    )
    assert resp.status_code == 200
    files = resp.json()["files"]
    a = next(f for f in files if f["name"] == "a.txt")
    b = next(f for f in files if f["name"] == "b.txt")
    assert a["enabled"] is True
    assert b["enabled"] is False


def test_delete_source(client: TestClient, project_id: str) -> None:
    client.post(
        f"/projects/{project_id}/sources",
        files=[("files", ("del.txt", io.BytesIO(b"delete me"), "text/plain"))],
    )
    resp = client.delete(f"/projects/{project_id}/sources/del.txt")
    assert resp.status_code == 204

    # File gone from list
    resp2 = client.get(f"/projects/{project_id}/sources")
    names = [f["name"] for f in resp2.json()["files"]]
    assert "del.txt" not in names


def test_delete_source_invalid_filename(client: TestClient, project_id: str) -> None:
    resp = client.delete(f"/projects/{project_id}/sources/..%2Fevil.txt")
    # %2F in path is treated as a path separator by the ASGI server, so FastAPI
    # may route it differently (405/404) or reject it (400/422). All are safe.
    assert resp.status_code in (400, 404, 405, 422)


# ---------------------------------------------------------------------------
# /projects/{id}/threads
# ---------------------------------------------------------------------------

def test_list_threads(client: TestClient, project_id: str) -> None:
    # Create a thread first
    client.post(f"/projects/{project_id}/threads", json={"title": "t1"})
    resp = client.get(f"/projects/{project_id}/threads")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert len(ids) >= 1


# ---------------------------------------------------------------------------
# /projects/{id}/threads/{thread_id}/messages
# ---------------------------------------------------------------------------

def test_get_messages_empty(client: TestClient, project_id: str) -> None:
    t = client.post(f"/projects/{project_id}/threads", json={}).json()
    resp = client.get(f"/projects/{project_id}/threads/{t['id']}/messages")
    assert resp.status_code == 200
    assert resp.json() == []


def test_clear_delete_rename_thread(client: TestClient, project_id: str) -> None:
    t = client.post(f"/projects/{project_id}/threads", json={"title": "Chat"}).json()
    thread_id = t["id"]

    resp = client.patch(
        f"/projects/{project_id}/threads/{thread_id}",
        json={"title": "Renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed"

    resp = client.delete(f"/projects/{project_id}/threads/{thread_id}/messages")
    assert resp.status_code == 200
    assert resp.json()["message_count"] == 0

    resp = client.delete(f"/projects/{project_id}/threads/{thread_id}")
    assert resp.status_code == 204

    listed = client.get(f"/projects/{project_id}/threads").json()
    assert thread_id not in [x["id"] for x in listed]


def test_upload_preserves_all_on_enabled(client: TestClient, project_id: str) -> None:
    """With enabled:[] (all on), uploading a third file must not disable the first two."""
    for name in ("a.txt", "b.txt"):
        client.post(
            f"/projects/{project_id}/sources",
            files=[("files", (name, io.BytesIO(b"x"), "text/plain"))],
        )
    resp = client.post(
        f"/projects/{project_id}/sources",
        files=[("files", ("c.txt", io.BytesIO(b"x"), "text/plain"))],
    )
    assert resp.status_code == 201
    files = resp.json()["files"]
    assert len(files) == 3
    assert all(f["enabled"] for f in files)

    settings = get_settings()
    pm = ProjectManager(settings)
    assert pm.get_enabled_sources(project_id) == []


# ---------------------------------------------------------------------------
# /settings
# ---------------------------------------------------------------------------

def test_get_settings(client: TestClient) -> None:
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["models"]["general_chat"] == "remote/deepseek-v4-pro"
    assert body["router"]["rules_enabled"] is True
    assert len(body["router"]["rules"]) == 7
    assert body["search"]["tavily_api_key"] == ""
    assert "tavily_api_key_set" in body["search"]
    assert body["opencode_go"]["api_key"] == ""
    assert "api_key_set" in body["opencode_go"]


def test_put_settings(client: TestClient) -> None:
    resp = client.put("/settings", json={"rag": {"top_k": 7}})
    assert resp.status_code == 200
    assert resp.json()["rag"]["top_k"] == 7

    resp2 = client.get("/settings")
    assert resp2.status_code == 200
    assert resp2.json()["rag"]["top_k"] == 7


def test_put_settings_validation_error(client: TestClient) -> None:
    resp = client.put("/settings", json={"rag": {"chunk_size": "not-a-number"}})
    assert resp.status_code == 422
    assert "chunk_size" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_returns_structure(client: TestClient) -> None:
    resp = client.get("/health")
    # May fail to reach LM Studio in test env but should return structured response
    assert resp.status_code in (200, 502)
