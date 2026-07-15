"""file_ops tool — sandboxed filesystem operations under a project root."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.project_manager import INSTRUCTIONS_FILE, PROJECT_YAML

logger = logging.getLogger("prompter.mcp")

MAX_READ_BYTES = 500 * 1024
BINARY_SAMPLE_BYTES = 1024
RESERVED_ROOT_NAMES = frozenset({PROJECT_YAML, INSTRUCTIONS_FILE})
VALID_OPERATIONS = frozenset({"read", "write", "list", "mkdir", "delete"})

TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "file_ops",
        "description": (
            "Sandboxed file operations under the current project root. "
            "Supports read, write, list, mkdir, and delete. "
            f"Reads are capped at {MAX_READ_BYTES} bytes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["read", "write", "list", "mkdir", "delete"],
                    "description": "Filesystem operation to perform",
                },
                "path": {
                    "type": "string",
                    "description": "Path relative to the project root",
                },
                "content": {
                    "type": "string",
                    "description": "File content (required for write)",
                },
            },
            "required": ["operation", "path"],
        },
    },
}


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _resolve_under_root(project_root: Path, path: str) -> Path | None:
    """Resolve path under project_root; return None if it escapes the sandbox."""
    if not isinstance(path, str) or not path.strip():
        return None
    if Path(path).is_absolute():
        return None
    try:
        resolved_root = project_root.resolve()
        resolved = (resolved_root / path).resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(resolved_root):
        return None
    return resolved


def _rel_path(resolved: Path, project_root: Path) -> str:
    return resolved.relative_to(project_root.resolve()).as_posix()


def _is_reserved_at_root(resolved: Path, project_root: Path) -> bool:
    rel = resolved.relative_to(project_root.resolve())
    return len(rel.parts) == 1 and rel.name in RESERVED_ROOT_NAMES


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            sample = fh.read(BINARY_SAMPLE_BYTES)
    except OSError:
        return True
    return b"\0" in sample


def _op_list(resolved: Path, project_root: Path) -> str:
    if not resolved.exists():
        return _error(f"Path not found: {_rel_path(resolved, project_root)}")
    if not resolved.is_dir():
        return _error(f"Not a directory: {_rel_path(resolved, project_root)}")
    entries: list[dict[str, Any]] = []
    for child in sorted(resolved.iterdir(), key=lambda p: p.name.lower()):
        try:
            size = child.stat().st_size if child.is_file() else 0
        except OSError:
            size = 0
        entries.append(
            {
                "path": _rel_path(child, project_root),
                "is_dir": child.is_dir(),
                "size": size,
            }
        )
    return json.dumps(entries)


def _op_read(resolved: Path, project_root: Path) -> str:
    if not resolved.exists():
        return _error(f"Path not found: {_rel_path(resolved, project_root)}")
    if not resolved.is_file():
        return _error(f"Not a file: {_rel_path(resolved, project_root)}")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return _error(f"Cannot read file: {exc}")
    if size > MAX_READ_BYTES:
        return _error(
            f"File too large ({size} bytes); max is {MAX_READ_BYTES} bytes"
        )
    if _is_binary(resolved):
        return _error(f"Binary file not supported: {_rel_path(resolved, project_root)}")
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _error(f"Binary file not supported: {_rel_path(resolved, project_root)}")
    except OSError as exc:
        return _error(f"Cannot read file: {exc}")
    return json.dumps({"content": content})


def _op_write(resolved: Path, project_root: Path, content: str | None) -> str:
    if content is None or not isinstance(content, str):
        return _error("Missing or invalid required parameter: content")
    if _is_reserved_at_root(resolved, project_root):
        return _error(
            f"Write refused: reserved path {_rel_path(resolved, project_root)}"
        )
    if resolved.exists() and resolved.is_dir():
        return _error(f"Cannot write to directory: {_rel_path(resolved, project_root)}")
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        resolved.write_bytes(data)
    except OSError as exc:
        return _error(f"Cannot write file: {exc}")
    return json.dumps(
        {"written": _rel_path(resolved, project_root), "bytes": len(data)}
    )


def _op_mkdir(resolved: Path, project_root: Path) -> str:
    if resolved.exists() and not resolved.is_dir():
        return _error(
            f"Path exists and is not a directory: {_rel_path(resolved, project_root)}"
        )
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _error(f"Cannot create directory: {exc}")
    return json.dumps({"created": _rel_path(resolved, project_root)})


def _op_delete(resolved: Path, project_root: Path) -> str:
    if _is_reserved_at_root(resolved, project_root):
        return _error(
            f"Delete refused: reserved path {_rel_path(resolved, project_root)}"
        )
    if not resolved.exists():
        return _error(f"Path not found: {_rel_path(resolved, project_root)}")
    if resolved.is_dir():
        return _error(
            f"Cannot delete directory: {_rel_path(resolved, project_root)}"
        )
    try:
        resolved.unlink()
    except OSError as exc:
        return _error(f"Cannot delete file: {exc}")
    return json.dumps({"deleted": _rel_path(resolved, project_root)})


async def execute(
    operation: str,
    path: str,
    content: str | None,
    project_root: Path,
) -> str:
    """Perform a sandboxed filesystem operation; return JSON result or error."""
    if not isinstance(operation, str) or operation not in VALID_OPERATIONS:
        return _error(
            "Missing or invalid required parameter: operation "
            "(must be read|write|list|mkdir|delete)"
        )
    if not isinstance(path, str) or not path.strip():
        return _error("Missing or invalid required parameter: path")

    resolved = _resolve_under_root(project_root, path)
    if resolved is None:
        return _error("Path escapes project root or is invalid")

    logger.info(
        "file_ops operation=%s path=%r",
        operation,
        path,
    )

    if operation == "list":
        return _op_list(resolved, project_root)
    if operation == "read":
        return _op_read(resolved, project_root)
    if operation == "write":
        return _op_write(resolved, project_root, content)
    if operation == "mkdir":
        return _op_mkdir(resolved, project_root)
    return _op_delete(resolved, project_root)
