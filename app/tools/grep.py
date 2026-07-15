"""grep tool — regex search under a sandboxed project root."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger("prompter.mcp")

SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__"})
MAX_MATCHES = 100
MAX_TEXT_LEN = 500
BINARY_SAMPLE_BYTES = 1024

TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": (
            "Search files under the project root with a regular expression. "
            "Skips .git, node_modules, __pycache__, and binary files. "
            f"Returns at most {MAX_MATCHES} matches."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file relative to project root",
                    "default": ".",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether the search is case-sensitive",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
    },
}


def _under_root(root: Path, candidate: Path) -> Path | None:
    try:
        resolved_root = root.resolve()
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(resolved_root):
        return None
    return resolved


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            sample = fh.read(BINARY_SAMPLE_BYTES)
    except OSError:
        return True
    return b"\0" in sample


def _iter_files(base: Path) -> Iterator[Path]:
    if base.is_file():
        yield base
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


async def execute(
    pattern: str,
    path: str,
    case_sensitive: bool,
    root: Path,
) -> str:
    """Search files under root/path; return JSON matches or an error object."""
    if not isinstance(pattern, str) or not pattern.strip():
        return json.dumps(
            {"error": "Missing or invalid required parameter: pattern"}
        )

    if not isinstance(path, str) or not path.strip():
        path = "."

    resolved_root = root.resolve()
    search_base = _under_root(resolved_root, resolved_root / path)
    if search_base is None:
        return json.dumps({"error": "Path escapes project root"})
    if not search_base.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return json.dumps({"error": f"Invalid regular expression: {exc}"})

    matches: list[dict[str, Any]] = []
    for file_path in _iter_files(search_base):
        if not file_path.is_file():
            continue
        if any(part in SKIP_DIRS for part in file_path.relative_to(resolved_root).parts):
            continue
        if _is_binary(file_path):
            continue
        try:
            with file_path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line_no, line in enumerate(fh, start=1):
                    if regex.search(line):
                        text = line.rstrip("\r\n")
                        if len(text) > MAX_TEXT_LEN:
                            text = text[:MAX_TEXT_LEN]
                        matches.append(
                            {
                                "file": file_path.relative_to(
                                    resolved_root
                                ).as_posix(),
                                "line": line_no,
                                "text": text,
                            }
                        )
                        if len(matches) >= MAX_MATCHES:
                            return json.dumps(matches)
        except OSError:
            continue

    logger.info(
        "grep pattern=%r path=%r matches=%d", pattern, path, len(matches)
    )
    return json.dumps(matches)
