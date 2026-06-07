"""Tests for app/logging_setup.py."""

from __future__ import annotations

import logging
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.logging_setup import configure_logging, _NOISY_THIRD_PARTY, _SUBSYSTEM_LOGGER_MAP


def _make_settings(
    level: str = "INFO",
    file_enabled: bool = False,
    subsystems: dict[str, bool] | None = None,
    log_dir: str = "logs",
) -> MagicMock:
    """Build a minimal Settings-like mock."""
    if subsystems is None:
        subsystems = {k: True for k in _SUBSYSTEM_LOGGER_MAP}

    mock_subsystems = MagicMock()
    for key, val in subsystems.items():
        setattr(mock_subsystems, key, val)

    mock_logging = MagicMock()
    mock_logging.level = level
    mock_logging.file_enabled = file_enabled
    mock_logging.subsystems = mock_subsystems

    settings = MagicMock()
    settings.logging = mock_logging
    return settings


def _reset_prompter_logger() -> None:
    """Remove all handlers from the prompter root logger between tests."""
    root = logging.getLogger("prompter")
    root.handlers.clear()
    root.propagate = True


@pytest.fixture(autouse=True)
def clean_logger():
    _reset_prompter_logger()
    yield
    _reset_prompter_logger()


class TestConfigureLoggingHandlers:
    def test_stderr_handler_always_added(self):
        settings = _make_settings(level="INFO", file_enabled=False)
        configure_logging(settings)

        root = logging.getLogger("prompter")
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)
                           and not isinstance(h, RotatingFileHandler)]
        assert len(stream_handlers) >= 1

    def test_file_handler_added_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = _make_settings(level="INFO", file_enabled=True)
        configure_logging(settings)

        root = logging.getLogger("prompter")
        file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert (tmp_path / "logs" / "prompter.log").parent.exists()

    def test_file_handler_absent_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = _make_settings(level="INFO", file_enabled=False)
        configure_logging(settings)

        root = logging.getLogger("prompter")
        file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 0

    def test_root_logger_level_set(self):
        settings = _make_settings(level="WARNING", file_enabled=False)
        configure_logging(settings)

        root = logging.getLogger("prompter")
        assert root.level == logging.WARNING

    def test_root_logger_does_not_propagate(self):
        settings = _make_settings(file_enabled=False)
        configure_logging(settings)

        root = logging.getLogger("prompter")
        assert root.propagate is False


class TestSubsystemOverrides:
    def test_subsystem_true_sets_debug(self):
        settings = _make_settings(file_enabled=False, subsystems={"router": True, "rag": True,
                                                                    "scheduler": True, "embedding": True,
                                                                    "search": True, "orchestrator": True})
        configure_logging(settings)

        assert logging.getLogger("prompter.router").level == logging.DEBUG
        assert logging.getLogger("prompter.rag").level == logging.DEBUG

    def test_subsystem_false_sets_warning(self):
        subs = {k: True for k in _SUBSYSTEM_LOGGER_MAP}
        subs["search"] = False
        subs["scheduler"] = False
        settings = _make_settings(file_enabled=False, subsystems=subs)
        configure_logging(settings)

        assert logging.getLogger("prompter.search").level == logging.WARNING
        assert logging.getLogger("prompter.scheduler").level == logging.WARNING

    def test_all_subsystems_mapped(self):
        settings = _make_settings(file_enabled=False)
        configure_logging(settings)
        for logger_name in _SUBSYSTEM_LOGGER_MAP.values():
            logger = logging.getLogger(logger_name)
            assert logger.level in (logging.DEBUG, logging.WARNING)


class TestThirdPartySupression:
    def test_noisy_libs_suppressed(self):
        settings = _make_settings(file_enabled=False)
        configure_logging(settings)

        for lib in _NOISY_THIRD_PARTY:
            assert logging.getLogger(lib).level == logging.WARNING, (
                f"{lib} should be WARNING, got {logging.getLogger(lib).level}"
            )
