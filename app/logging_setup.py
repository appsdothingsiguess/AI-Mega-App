"""Logging configuration for Prompter X.

Call configure_logging(settings) once during FastAPI lifespan startup,
before any other initialisation.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from app.config import Settings

_logger = logging.getLogger("prompter")

_SUBSYSTEM_LOGGER_MAP: dict[str, str] = {
    "router": "prompter.router",
    "scheduler": "prompter.scheduler",
    "embedding": "prompter.embedding",
    "search": "prompter.search",
    "rag": "prompter.rag",
    "orchestrator": "prompter.orchestrator",
}

_NOISY_THIRD_PARTY = [
    "httpx",
    "litellm",
    "httpcore",
    "qdrant_client",
]

_STDERR_FORMAT = "[%(asctime)s] %(name)s %(levelname)s %(message)s"
_FILE_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def configure_logging(settings: Settings) -> None:
    """Configure all prompter.* loggers with handlers, formatters, and level overrides.

    Must be called exactly once, at the very start of the FastAPI lifespan,
    before validate_config() or any service construction.
    """
    level_name: str = (settings.logging.level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    file_enabled: bool = settings.logging.file_enabled

    root = logging.getLogger("prompter")
    root.setLevel(level)
    # Remove any handlers that may have been added by earlier basicConfig calls.
    root.handlers.clear()
    root.propagate = False

    # --- stderr handler (always active) ---
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(
        logging.Formatter(fmt=_STDERR_FORMAT, datefmt=_DATE_FORMAT)
    )
    root.addHandler(stderr_handler)

    # --- rotating file handler (conditional) ---
    if file_enabled:
        os.makedirs("logs", exist_ok=True)
        file_handler = RotatingFileHandler(
            "logs/prompter.log",
            maxBytes=10_485_760,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(fmt=_FILE_FORMAT))
        root.addHandler(file_handler)

    # --- per-subsystem level overrides ---
    subsystems = settings.logging.subsystems
    for key, logger_name in _SUBSYSTEM_LOGGER_MAP.items():
        enabled: bool = getattr(subsystems, key, True)
        sub_logger = logging.getLogger(logger_name)
        sub_logger.setLevel(logging.DEBUG if enabled else logging.WARNING)

    # --- suppress noisy third-party libraries ---
    for lib in _NOISY_THIRD_PARTY:
        logging.getLogger(lib).setLevel(logging.WARNING)

    _logger.info("Logging configured: level=%s file=%s", level_name, file_enabled)
