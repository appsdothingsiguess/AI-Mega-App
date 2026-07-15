"""bash tool — run a shell command in a caller-provided sandbox cwd."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("prompter.mcp")

DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 120
MAX_OUTPUT_CHARS = 4000


TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Run a shell command in the project sandbox. "
            f"timeout_s defaults to {DEFAULT_TIMEOUT_S} and is capped at {MAX_TIMEOUT_S}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": (
                        f"Timeout in seconds (default {DEFAULT_TIMEOUT_S}, max {MAX_TIMEOUT_S})"
                    ),
                    "default": DEFAULT_TIMEOUT_S,
                },
            },
            "required": ["command"],
        },
    },
}


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS]


def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP: kill the whole tree best-effort.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
            )
            if process.returncode is None:
                process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass


async def execute(command: str, timeout_s: int, cwd: Path) -> str:
    """Run ``command`` under ``cwd`` and return JSON-encoded result."""
    if not isinstance(command, str) or not command.strip():
        return json.dumps(
            {"error": "Missing or invalid required parameter: command"}
        )

    timeout_s = max(1, min(int(timeout_s), MAX_TIMEOUT_S))
    workdir = str(cwd.resolve())
    logger.info("bash command=%r timeout_s=%d cwd=%s", command, timeout_s, workdir)

    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "cwd": workdir,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = await asyncio.create_subprocess_shell(command, **kwargs)

    timed_out = False
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        timed_out = True
        _kill_process_tree(process)
        try:
            stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=5)
        except asyncio.TimeoutError:
            stdout_b, stderr_b = b"", b""

    stdout = _truncate(stdout_b.decode("utf-8", errors="replace"))
    stderr = _truncate(stderr_b.decode("utf-8", errors="replace"))

    if timed_out:
        return json.dumps(
            {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": None,
                "timed_out": True,
            }
        )

    return json.dumps(
        {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": process.returncode,
            "timed_out": False,
        }
    )
