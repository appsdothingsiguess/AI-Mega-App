"""Shared utilities."""

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(value: str) -> str:
    """Create a filesystem-safe project id from a display name."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug or "project"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def supported_doc_suffix(path: Path) -> bool:
    return path.suffix.lower() in {".txt", ".md", ".pdf"}
