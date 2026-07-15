"""Tests for app/tools/grep.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.tools import grep


def test_tool_schema_openai_format() -> None:
    schema = grep.TOOL_SCHEMA
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "grep"
    assert fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "pattern" in params["properties"]
    assert params["properties"]["pattern"]["type"] == "string"
    assert params["properties"]["path"]["default"] == "."
    assert params["properties"]["case_sensitive"]["default"] is False
    assert params["required"] == ["pattern"]


@pytest.mark.asyncio
async def test_execute_match_found(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("print('Hello World')\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("nope\n", encoding="utf-8")

    raw = await grep.execute("Hello", ".", False, tmp_path)
    parsed = json.loads(raw)

    assert len(parsed) == 1
    assert parsed[0]["file"] == "hello.py"
    assert parsed[0]["line"] == 1
    assert "Hello World" in parsed[0]["text"]


@pytest.mark.asyncio
async def test_execute_no_match(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha beta\n", encoding="utf-8")

    raw = await grep.execute("zzz_not_present", ".", False, tmp_path)
    assert json.loads(raw) == []


@pytest.mark.asyncio
async def test_execute_rejects_path_traversal(tmp_path: Path) -> None:
    raw = await grep.execute(".", "../../etc/passwd", False, tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_execute_skips_binary(tmp_path: Path) -> None:
    (tmp_path / "bin.dat").write_bytes(b"secret\x00payload")
    (tmp_path / "ok.txt").write_text("secret visible\n", encoding="utf-8")

    raw = await grep.execute("secret", ".", False, tmp_path)
    parsed = json.loads(raw)

    assert len(parsed) == 1
    assert parsed[0]["file"] == "ok.txt"


@pytest.mark.asyncio
async def test_execute_caps_matches(tmp_path: Path) -> None:
    lines = "\n".join(f"hit {i}" for i in range(150)) + "\n"
    (tmp_path / "many.txt").write_text(lines, encoding="utf-8")

    raw = await grep.execute(r"hit \d+", ".", False, tmp_path)
    parsed = json.loads(raw)

    assert len(parsed) == grep.MAX_MATCHES


@pytest.mark.asyncio
async def test_execute_invalid_regex(tmp_path: Path) -> None:
    raw = await grep.execute("[unclosed", ".", False, tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "regular expression" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_execute_empty_pattern(tmp_path: Path) -> None:
    raw = await grep.execute("   ", ".", False, tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_execute_skips_ignored_dirs(tmp_path: Path) -> None:
    ignored = tmp_path / "node_modules" / "pkg"
    ignored.mkdir(parents=True)
    (ignored / "lib.js").write_text("findme\n", encoding="utf-8")
    (tmp_path / "src.py").write_text("findme\n", encoding="utf-8")

    raw = await grep.execute("findme", ".", False, tmp_path)
    parsed = json.loads(raw)

    assert len(parsed) == 1
    assert parsed[0]["file"] == "src.py"


@pytest.mark.asyncio
async def test_execute_case_sensitive(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("Hello\nhello\n", encoding="utf-8")

    raw = await grep.execute("Hello", ".", True, tmp_path)
    parsed = json.loads(raw)

    assert len(parsed) == 1
    assert parsed[0]["line"] == 1
