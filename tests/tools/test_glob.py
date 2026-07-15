"""Tests for app/tools/glob.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.tools import glob as glob_tool


def test_tool_schema_openai_format() -> None:
    schema = glob_tool.TOOL_SCHEMA
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "glob"
    assert fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "pattern" in params["properties"]
    assert params["properties"]["pattern"]["type"] == "string"
    assert params["properties"]["path"]["default"] == "."
    assert params["required"] == ["pattern"]


@pytest.mark.asyncio
async def test_execute_match_found(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "src" / "b.txt").write_text("y\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("z\n", encoding="utf-8")

    raw = await glob_tool.execute("**/*.py", ".", tmp_path)
    parsed = json.loads(raw)

    assert parsed == ["c.py", "src/a.py"]


@pytest.mark.asyncio
async def test_execute_no_match(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")

    raw = await glob_tool.execute("**/*.py", ".", tmp_path)
    assert json.loads(raw) == []


@pytest.mark.asyncio
async def test_execute_rejects_path_traversal(tmp_path: Path) -> None:
    raw = await glob_tool.execute("*.txt", "../../etc/passwd", tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_execute_caps_results(tmp_path: Path) -> None:
    for i in range(250):
        (tmp_path / f"f{i:03d}.txt").write_text("x\n", encoding="utf-8")

    raw = await glob_tool.execute("*.txt", ".", tmp_path)
    parsed = json.loads(raw)

    assert len(parsed) == glob_tool.MAX_PATHS
    assert parsed == sorted(parsed)


@pytest.mark.asyncio
async def test_execute_skips_ignored_dirs(tmp_path: Path) -> None:
    ignored = tmp_path / ".git" / "objects"
    ignored.mkdir(parents=True)
    (ignored / "pack").write_text("x\n", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("y\n", encoding="utf-8")

    raw = await glob_tool.execute("**/*", ".", tmp_path)
    parsed = json.loads(raw)

    assert parsed == ["keep.txt"]


@pytest.mark.asyncio
async def test_execute_empty_pattern(tmp_path: Path) -> None:
    raw = await glob_tool.execute("  ", ".", tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_execute_subdir_path(tmp_path: Path) -> None:
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "root.py").write_text("y\n", encoding="utf-8")

    raw = await glob_tool.execute("*.py", "pkg", tmp_path)
    parsed = json.loads(raw)

    assert parsed == ["pkg/mod.py"]
