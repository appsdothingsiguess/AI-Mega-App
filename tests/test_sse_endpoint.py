"""Tests for the SSE chat endpoint."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import _build_services, app
from app.project_manager import ProjectManager


class _FakeOrchestrator:
    def __init__(
        self,
        events: list[str] | None = None,
        *,
        raise_cancelled: bool = False,
    ) -> None:
        self.events = events or [
            json.dumps({"type": "chunk", "content": "Hello"}),
            json.dumps({"type": "done"}),
        ]
        self.raise_cancelled = raise_cancelled
        self.handle_calls: list[tuple[str, str, str]] = []

    async def handle_message(
        self,
        project_id: str,
        thread_id: str,
        user_content: str | Any,
        *,
        model_override: str | None = None,
        enabled_tools: list[str] | None = None,
        disconnect_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        self.handle_calls.append((project_id, thread_id, str(user_content)))
        for event in self.events:
            if self.raise_cancelled:
                raise asyncio.CancelledError()
            yield event


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


@pytest.fixture
def fake_orchestrator(client: TestClient) -> _FakeOrchestrator:
    fake = _FakeOrchestrator()
    settings, projects, _orchestrator, vector_store = _build_services()
    app.state.settings = settings
    app.state.projects = projects
    app.state.orchestrator = fake
    app.state.vector_store = vector_store
    return fake


@pytest.fixture
def project_and_thread(client: TestClient) -> tuple[str, str]:
    project_id = client.post("/projects/init", json={"name": "SSE Project"}).json()["id"]
    thread_id = client.post(f"/projects/{project_id}/threads", json={"title": "Main"}).json()["id"]
    return project_id, thread_id


def test_build_services_wires_search_service() -> None:
    settings, _, orchestrator, _ = _build_services()
    assert orchestrator.search_service is not None


def test_sse_returns_event_stream_content_type(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    project_and_thread: tuple[str, str],
) -> None:
    project_id, thread_id = project_and_thread
    with client.stream(
        "POST",
        f"/api/chat/{project_id}/{thread_id}",
        json={"content": "hi"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")


def test_sse_frames_match_data_json_format(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    project_and_thread: tuple[str, str],
) -> None:
    project_id, thread_id = project_and_thread
    with client.stream(
        "POST",
        f"/api/chat/{project_id}/{thread_id}",
        json={"content": "hello"},
    ) as resp:
        body = resp.read().decode("utf-8")

    frames = [frame for frame in body.split("\n\n") if frame]
    assert len(frames) == len(fake_orchestrator.events)
    for frame, event in zip(frames, fake_orchestrator.events, strict=True):
        assert frame == f"data: {event}"


def test_legacy_post_messages_removed(
    client: TestClient,
    project_and_thread: tuple[str, str],
) -> None:
    project_id, thread_id = project_and_thread
    resp = client.post(
        f"/projects/{project_id}/threads/{thread_id}/messages",
        json={"content": "legacy"},
    )
    assert resp.status_code == 405


def test_get_messages_still_available(
    client: TestClient,
    project_and_thread: tuple[str, str],
) -> None:
    project_id, thread_id = project_and_thread
    resp = client.get(f"/projects/{project_id}/threads/{thread_id}/messages")
    assert resp.status_code == 200
    assert resp.json() == []


def test_sse_unknown_project_returns_404(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    project_and_thread: tuple[str, str],
) -> None:
    _project_id, thread_id = project_and_thread
    resp = client.post(
        f"/api/chat/missing-project/{thread_id}",
        json={"content": "hi"},
    )
    assert resp.status_code == 404
    assert fake_orchestrator.handle_calls == []


def test_sse_unknown_thread_returns_404(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    project_and_thread: tuple[str, str],
) -> None:
    project_id, _thread_id = project_and_thread
    resp = client.post(
        f"/api/chat/{project_id}/missing-thread",
        json={"content": "hi"},
    )
    assert resp.status_code == 404
    assert fake_orchestrator.handle_calls == []


def test_sse_handles_client_disconnect(
    client: TestClient,
    project_and_thread: tuple[str, str],
) -> None:
    project_id, thread_id = project_and_thread
    fake = _FakeOrchestrator(raise_cancelled=True)
    settings, projects, _orchestrator, vector_store = _build_services()
    app.state.settings = settings
    app.state.projects = projects
    app.state.orchestrator = fake
    app.state.vector_store = vector_store

    with client.stream(
        "POST",
        f"/api/chat/{project_id}/{thread_id}",
        json={"content": "disconnect"},
    ) as resp:
        try:
            resp.read()
        except Exception:
            pass

    assert fake.handle_calls == [(project_id, thread_id, "disconnect")]


def test_sse_does_not_persist_via_endpoint(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    project_and_thread: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, thread_id = project_and_thread
    settings = get_settings()
    projects = ProjectManager(settings)
    original_append = projects.append_message

    def _fail_append(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("endpoint must not append messages")

    monkeypatch.setattr(projects, "append_message", _fail_append)
    app.state.projects = projects

    with client.stream(
        "POST",
        f"/api/chat/{project_id}/{thread_id}",
        json={"content": "stream only"},
    ) as resp:
        resp.read()

    assert fake_orchestrator.handle_calls
    monkeypatch.setattr(projects, "append_message", original_append)
