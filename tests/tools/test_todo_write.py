"""Tests for app/tools/todo_write.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.tools import todo_write


def test_tool_schema_openai_format() -> None:
    schema = todo_write.TOOL_SCHEMA
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "todo_write"
    assert fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "todos" in params["properties"]
    todos = params["properties"]["todos"]
    assert todos["type"] == "array"
    assert todos["items"]["type"] == "object"
    item_props = todos["items"]["properties"]
    assert set(item_props) == {"content", "status", "id"}
    assert item_props["status"]["enum"] == [
        "pending",
        "in_progress",
        "completed",
    ]
    assert todos["items"]["required"] == ["content", "status", "id"]
    assert params["required"] == ["todos"]


@pytest.mark.asyncio
async def test_execute_happy_path_writes_prompter_thread(tmp_path: Path) -> None:
    todos = [
        {"content": "First", "status": "completed", "id": "1"},
        {"content": "Second", "status": "in_progress", "id": "2"},
        {"content": "Third", "status": "pending", "id": "3"},
    ]
    raw = await todo_write.execute(todos, tmp_path, "thread-a")
    parsed = json.loads(raw)
    assert parsed["written"] is True
    assert parsed["todos"] == todos

    path = tmp_path / ".prompter" / "threads" / "thread-a" / "todos.json"
    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {"todos": todos}


@pytest.mark.asyncio
async def test_execute_uses_legacy_thread_dir_when_present(tmp_path: Path) -> None:
    legacy = tmp_path / "threads" / "legacy-thread"
    legacy.mkdir(parents=True)
    todos = [{"content": "A", "status": "pending", "id": "a"}]
    raw = await todo_write.execute(todos, tmp_path, "legacy-thread")
    parsed = json.loads(raw)
    assert parsed["written"] is True

    legacy_path = legacy / "todos.json"
    assert legacy_path.is_file()
    assert not (tmp_path / ".prompter" / "threads" / "legacy-thread").exists()
    assert json.loads(legacy_path.read_text(encoding="utf-8")) == {
        "todos": todos
    }


@pytest.mark.asyncio
async def test_execute_overwrites_existing(tmp_path: Path) -> None:
    first = [{"content": "Old", "status": "pending", "id": "1"}]
    second = [
        {"content": "New", "status": "in_progress", "id": "2"},
        {"content": "Also", "status": "pending", "id": "3"},
    ]
    await todo_write.execute(first, tmp_path, "t1")
    raw = await todo_write.execute(second, tmp_path, "t1")
    parsed = json.loads(raw)
    assert parsed["todos"] == second

    path = tmp_path / ".prompter" / "threads" / "t1" / "todos.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"todos": second}


@pytest.mark.asyncio
async def test_reject_multiple_in_progress(tmp_path: Path) -> None:
    todos = [
        {"content": "A", "status": "in_progress", "id": "1"},
        {"content": "B", "status": "in_progress", "id": "2"},
    ]
    raw = await todo_write.execute(todos, tmp_path, "t1")
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "in_progress" in parsed["error"]
    assert not (
        tmp_path / ".prompter" / "threads" / "t1" / "todos.json"
    ).exists()


@pytest.mark.asyncio
async def test_reject_malformed_missing_field(tmp_path: Path) -> None:
    raw = await todo_write.execute(
        [{"content": "A", "status": "pending"}],
        tmp_path,
        "t1",
    )
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "id" in parsed["error"]
    assert not (
        tmp_path / ".prompter" / "threads" / "t1" / "todos.json"
    ).exists()


@pytest.mark.asyncio
async def test_reject_invalid_status(tmp_path: Path) -> None:
    raw = await todo_write.execute(
        [{"content": "A", "status": "done", "id": "1"}],
        tmp_path,
        "t1",
    )
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "status" in parsed["error"]
    assert not (
        tmp_path / ".prompter" / "threads" / "t1" / "todos.json"
    ).exists()


@pytest.mark.asyncio
async def test_reject_non_list_todos(tmp_path: Path) -> None:
    raw = await todo_write.execute({"not": "a list"}, tmp_path, "t1")  # type: ignore[arg-type]
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "array" in parsed["error"]
