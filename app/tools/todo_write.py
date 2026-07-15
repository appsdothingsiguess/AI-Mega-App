"""todo_write tool — persist a structured per-thread task list."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("prompter.mcp")

VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})
REQUIRED_FIELDS = ("content", "status", "id")

TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "todo_write",
        "description": (
            "Replace the per-thread todo list with a structured task list. "
            "Exactly one item may be in_progress at a time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "Full todo list (overwrites previous)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Todo item text",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "Item status",
                            },
                            "id": {
                                "type": "string",
                                "description": "Stable item identifier",
                            },
                        },
                        "required": ["content", "status", "id"],
                    },
                },
            },
            "required": ["todos"],
        },
    },
}


def _resolve_thread_dir(project_root: Path, thread_id: str) -> Path:
    """Resolve thread directory: legacy if present, else `.prompter/threads`."""
    legacy = project_root / "threads" / thread_id
    if legacy.exists():
        return legacy
    return project_root / ".prompter" / "threads" / thread_id


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _validate_todos(todos: Any) -> tuple[list[dict[str, str]] | None, str | None]:
    if not isinstance(todos, list):
        return None, "todos must be an array"

    validated: list[dict[str, str]] = []
    in_progress_count = 0

    for i, item in enumerate(todos):
        if not isinstance(item, dict):
            return None, f"todos[{i}] must be an object"

        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            return None, (
                f"todos[{i}] missing required field(s): {', '.join(missing)}"
            )

        content = item["content"]
        status = item["status"]
        item_id = item["id"]

        if not isinstance(content, str):
            return None, f"todos[{i}].content must be a string"
        if not isinstance(item_id, str):
            return None, f"todos[{i}].id must be a string"
        if not isinstance(status, str) or status not in VALID_STATUSES:
            return None, (
                f"todos[{i}].status must be one of: "
                f"{', '.join(sorted(VALID_STATUSES))}"
            )

        if status == "in_progress":
            in_progress_count += 1

        validated.append(
            {"content": content, "status": status, "id": item_id}
        )

    if in_progress_count > 1:
        return None, (
            "Exactly one item may be in_progress at a time "
            f"(got {in_progress_count})"
        )

    return validated, None


async def execute(
    todos: list[dict],
    project_root: Path,
    thread_id: str,
) -> str:
    """Validate and overwrite ``todos.json`` for ``thread_id`` under ``project_root``."""
    validated, err = _validate_todos(todos)
    if err is not None:
        logger.info("todo_write rejected thread_id=%r: %s", thread_id, err)
        return _error(err)

    thread_dir = _resolve_thread_dir(project_root, thread_id)
    thread_dir.mkdir(parents=True, exist_ok=True)
    path = thread_dir / "todos.json"
    payload = {"todos": validated}
    path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "todo_write wrote %d item(s) to %s",
        len(validated),
        path,
    )
    return json.dumps({"todos": validated, "written": True})
