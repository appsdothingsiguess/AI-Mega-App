"""Tests for app/tools/bash.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.tools import bash


def test_tool_schema_openai_format() -> None:
    schema = bash.TOOL_SCHEMA
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "bash"
    assert fn["description"]
    assert "120" in fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "command" in params["properties"]
    assert params["properties"]["command"]["type"] == "string"
    assert "timeout_s" in params["properties"]
    assert params["properties"]["timeout_s"]["type"] == "integer"
    assert params["properties"]["timeout_s"]["default"] == 30
    assert params["required"] == ["command"]


@pytest.mark.asyncio
async def test_execute_success(tmp_path: Path) -> None:
    raw = await bash.execute(
        f'{sys.executable} -c "print(\'hello-sandbox\')"',
        30,
        tmp_path,
    )
    parsed = json.loads(raw)
    assert parsed["timed_out"] is False
    assert parsed["exit_code"] == 0
    assert "hello-sandbox" in parsed["stdout"]
    assert parsed["stderr"] == ""


@pytest.mark.asyncio
async def test_execute_nonzero_exit(tmp_path: Path) -> None:
    raw = await bash.execute(
        f'{sys.executable} -c "import sys; sys.exit(7)"',
        30,
        tmp_path,
    )
    parsed = json.loads(raw)
    assert parsed["timed_out"] is False
    assert parsed["exit_code"] == 7


@pytest.mark.asyncio
async def test_execute_timeout(tmp_path: Path) -> None:
    raw = await bash.execute(
        f'{sys.executable} -c "import time; time.sleep(30)"',
        1,
        tmp_path,
    )
    parsed = json.loads(raw)
    assert parsed["timed_out"] is True
    assert parsed["exit_code"] is None
    assert "stdout" in parsed
    assert "stderr" in parsed


@pytest.mark.asyncio
async def test_execute_empty_command(tmp_path: Path) -> None:
    for command in ("", "   ", "\t\n"):
        raw = await bash.execute(command, 30, tmp_path)
        parsed = json.loads(raw)
        assert parsed == {
            "error": "Missing or invalid required parameter: command"
        }


@pytest.mark.asyncio
async def test_execute_truncates_output(tmp_path: Path) -> None:
    raw = await bash.execute(
        f'{sys.executable} -c "print(\'x\' * 5000, end=\'\')"',
        30,
        tmp_path,
    )
    parsed = json.loads(raw)
    assert parsed["timed_out"] is False
    assert parsed["exit_code"] == 0
    assert len(parsed["stdout"]) == 4000
    assert parsed["stdout"] == "x" * 4000


@pytest.mark.asyncio
async def test_execute_uses_provided_cwd(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    marker.write_text("ok", encoding="utf-8")
    raw = await bash.execute(
        f'{sys.executable} -c "from pathlib import Path; '
        f'print(Path(\'marker.txt\').read_text())"',
        30,
        tmp_path,
    )
    parsed = json.loads(raw)
    assert parsed["exit_code"] == 0
    assert "ok" in parsed["stdout"]


@pytest.mark.asyncio
async def test_execute_clamps_timeout_above_max(tmp_path: Path) -> None:
    """timeout_s > 120 is clamped; still runs a short command successfully."""
    raw = await bash.execute(
        f'{sys.executable} -c "print(1)"',
        999,
        tmp_path,
    )
    parsed = json.loads(raw)
    assert parsed["timed_out"] is False
    assert parsed["exit_code"] == 0
