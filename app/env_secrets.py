"""Read/write API secrets in ``.env`` only (never ``settings.json``)."""

from __future__ import annotations

import os
import re
from pathlib import Path

_DEFAULT_ENV_PATH = Path(".env")
_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

MANAGED_SECRET_VARS = frozenset({"OPENCODE_API_KEY", "TAVILY_API_KEY"})


def env_file_path() -> Path:
    return Path(os.environ.get("ENV_FILE_PATH", _DEFAULT_ENV_PATH))


def _parse_env_lines(text: str) -> list[tuple[str, str | None]]:
    """Return ``(line, key)`` pairs; ``key`` is set when the line is ``KEY=...``."""
    rows: list[tuple[str, str | None]] = []
    for line in text.splitlines():
        match = _ENV_LINE.match(line)
        rows.append((line, match.group(1) if match else None))
    return rows


def write_env_vars(updates: dict[str, str]) -> None:
    """Merge secret *updates* into ``.env`` and apply them to ``os.environ``."""
    filtered = {
        key: value
        for key, value in updates.items()
        if key in MANAGED_SECRET_VARS and value.strip()
    }
    if not filtered:
        return

    path = env_file_path()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    rows = _parse_env_lines(existing)
    replaced: set[str] = set()

    new_lines: list[str] = []
    for line, key in rows:
        if key is not None and key in filtered:
            new_lines.append(f"{key}={filtered[key]}")
            replaced.add(key)
        else:
            new_lines.append(line)

    for key, value in filtered.items():
        if key not in replaced:
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append(f"{key}={value}")

    trailing_newline = "\n" if existing.endswith("\n") or not existing else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(new_lines) + trailing_newline, encoding="utf-8")

    for key, value in filtered.items():
        os.environ[key] = value


def secret_is_set(env_key: str) -> bool:
    return bool(os.environ.get(env_key, "").strip())
