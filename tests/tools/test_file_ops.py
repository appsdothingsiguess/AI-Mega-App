"""Tests for app/tools/file_ops.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.project_manager import INSTRUCTIONS_FILE, PROJECT_YAML
from app.tools import file_ops


def test_tool_schema_openai_format() -> None:
    schema = file_ops.TOOL_SCHEMA
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "file_ops"
    assert fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert params["required"] == ["operation", "path"]
    assert params["properties"]["operation"]["enum"] == [
        "read",
        "write",
        "list",
        "mkdir",
        "delete",
    ]
    assert params["properties"]["path"]["type"] == "string"
    assert params["properties"]["content"]["type"] == "string"


@pytest.mark.asyncio
async def test_write_and_read_happy_path(tmp_path: Path) -> None:
    raw = await file_ops.execute(
        "write",
        "docs/note.txt",
        "hello file_ops",
        tmp_path,
    )
    written = json.loads(raw)
    assert written["written"] == "docs/note.txt"
    assert written["bytes"] == len("hello file_ops".encode("utf-8"))
    assert (tmp_path / "docs" / "note.txt").read_text(encoding="utf-8") == (
        "hello file_ops"
    )

    raw = await file_ops.execute("read", "docs/note.txt", None, tmp_path)
    assert json.loads(raw) == {"content": "hello file_ops"}


@pytest.mark.asyncio
async def test_list_happy_path(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("aa", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("n", encoding="utf-8")

    raw = await file_ops.execute("list", ".", None, tmp_path)
    entries = json.loads(raw)
    by_path = {e["path"]: e for e in entries}
    assert by_path["a.txt"] == {"path": "a.txt", "is_dir": False, "size": 2}
    assert by_path["subdir"] == {"path": "subdir", "is_dir": True, "size": 0}
    assert "subdir/nested.txt" not in by_path


@pytest.mark.asyncio
async def test_mkdir_happy_path_idempotent(tmp_path: Path) -> None:
    raw = await file_ops.execute("mkdir", "docs/deep", None, tmp_path)
    assert json.loads(raw) == {"created": "docs/deep"}
    assert (tmp_path / "docs" / "deep").is_dir()

    raw = await file_ops.execute("mkdir", "docs/deep", None, tmp_path)
    assert json.loads(raw) == {"created": "docs/deep"}


@pytest.mark.asyncio
async def test_delete_happy_path(tmp_path: Path) -> None:
    target = tmp_path / "junk.txt"
    target.write_text("bye", encoding="utf-8")
    raw = await file_ops.execute("delete", "junk.txt", None, tmp_path)
    assert json.loads(raw) == {"deleted": "junk.txt"}
    assert not target.exists()


@pytest.mark.asyncio
async def test_write_rejects_reserved_project_yaml(tmp_path: Path) -> None:
    raw = await file_ops.execute("write", PROJECT_YAML, "x: 1", tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "reserved" in parsed["error"].lower()
    assert not (tmp_path / PROJECT_YAML).exists()


@pytest.mark.asyncio
async def test_write_rejects_reserved_instructions(tmp_path: Path) -> None:
    raw = await file_ops.execute(
        "write", INSTRUCTIONS_FILE, "# hijack", tmp_path
    )
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "reserved" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_delete_rejects_reserved_files(tmp_path: Path) -> None:
    (tmp_path / PROJECT_YAML).write_text("name: test\n", encoding="utf-8")
    (tmp_path / INSTRUCTIONS_FILE).write_text("# keep\n", encoding="utf-8")

    for name in (PROJECT_YAML, INSTRUCTIONS_FILE):
        raw = await file_ops.execute("delete", name, None, tmp_path)
        parsed = json.loads(raw)
        assert "error" in parsed
        assert "reserved" in parsed["error"].lower()
        assert (tmp_path / name).exists()


@pytest.mark.asyncio
async def test_write_allowed_under_nested_reserved_name(tmp_path: Path) -> None:
    raw = await file_ops.execute(
        "write", f"docs/{PROJECT_YAML}", "ok", tmp_path
    )
    assert json.loads(raw)["written"] == f"docs/{PROJECT_YAML}"


@pytest.mark.asyncio
async def test_read_rejects_missing(tmp_path: Path) -> None:
    raw = await file_ops.execute("read", "missing.txt", None, tmp_path)
    assert "error" in json.loads(raw)


@pytest.mark.asyncio
async def test_list_rejects_file_path(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    raw = await file_ops.execute("list", "f.txt", None, tmp_path)
    assert "error" in json.loads(raw)


@pytest.mark.asyncio
async def test_delete_rejects_directory(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    raw = await file_ops.execute("delete", "d", None, tmp_path)
    assert "error" in json.loads(raw)
    assert (tmp_path / "d").is_dir()


@pytest.mark.asyncio
async def test_write_rejects_missing_content(tmp_path: Path) -> None:
    raw = await file_ops.execute("write", "a.txt", None, tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "content" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_invalid_operation(tmp_path: Path) -> None:
    raw = await file_ops.execute("rename", "a.txt", None, tmp_path)
    assert "error" in json.loads(raw)


@pytest.mark.asyncio
async def test_path_traversal_dotdot(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    raw = await file_ops.execute(
        "read", "../outside.txt", None, tmp_path
    )
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "escape" in parsed["error"].lower() or "invalid" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_path_traversal_absolute(tmp_path: Path) -> None:
    outside = tmp_path.parent / "abs_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    raw = await file_ops.execute(
        "read", str(outside), None, tmp_path
    )
    parsed = json.loads(raw)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_path_traversal_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "symlink_target.txt"
    outside.write_text("leaked", encoding="utf-8")
    link = tmp_path / "escape_link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted in this environment")

    raw = await file_ops.execute("read", "escape_link", None, tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_read_rejects_oversized(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (file_ops.MAX_READ_BYTES + 1))
    raw = await file_ops.execute("read", "big.txt", None, tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "large" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_read_rejects_binary(tmp_path: Path) -> None:
    (tmp_path / "bin.dat").write_bytes(b"abc\0def")
    raw = await file_ops.execute("read", "bin.dat", None, tmp_path)
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "binary" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_empty_path_rejected(tmp_path: Path) -> None:
    raw = await file_ops.execute("list", "  ", None, tmp_path)
    assert "error" in json.loads(raw)
