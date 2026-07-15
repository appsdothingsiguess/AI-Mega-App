"""glob tool — filename pattern match under a sandboxed project root."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("prompter.mcp")

SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__"})
MAX_PATHS = 200

TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "glob",
        "description": (
            "Find files under the project root matching a glob pattern. "
            "Skips .git, node_modules, and __pycache__. "
            f"Returns at most {MAX_PATHS} paths."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. **/*.py)",
                },
                "path": {
                    "type": "string",
                    "description": "Directory relative to project root",
                    "default": ".",
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


def _has_ignored_segment(rel: Path) -> bool:
    return any(part in SKIP_DIRS for part in rel.parts)


async def execute(pattern: str, path: str, root: Path) -> str:
    """Glob under root/path; return JSON path list or an error object."""
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
    if not search_base.is_dir():
        return json.dumps({"error": f"Path is not a directory: {path}"})

    results: list[str] = []
    try:
        candidates = search_base.glob(pattern)
    except (OSError, ValueError) as exc:
        return json.dumps({"error": f"Invalid glob pattern: {exc}"})

    for candidate in candidates:
        resolved = _under_root(resolved_root, candidate)
        if resolved is None:
            continue
        if not resolved.is_file():
            continue
        rel = resolved.relative_to(resolved_root)
        if _has_ignored_segment(rel):
            continue
        results.append(rel.as_posix())
        if len(results) >= MAX_PATHS:
            break

    results.sort()
    logger.info(
        "glob pattern=%r path=%r matches=%d", pattern, path, len(results)
    )
    return json.dumps(results)
