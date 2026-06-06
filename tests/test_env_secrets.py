"""Tests for app/env_secrets.py."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.env_secrets import secret_is_set, write_env_vars


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nOPENCODE_API_KEY=old-key\nPROJECTS_DIR=./projects\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ENV_FILE_PATH", str(path))
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    return path


def test_write_env_vars_updates_existing_key(env_file: Path) -> None:
    write_env_vars({"OPENCODE_API_KEY": "new-secret"})
    text = env_file.read_text(encoding="utf-8")
    assert "OPENCODE_API_KEY=new-secret" in text
    assert "OPENCODE_API_KEY=old-key" not in text
    assert "# comment" in text
    assert "PROJECTS_DIR=./projects" in text
    assert os.environ["OPENCODE_API_KEY"] == "new-secret"


def test_write_env_vars_appends_missing_key(env_file: Path) -> None:
    write_env_vars({"TAVILY_API_KEY": "tvly-123"})
    text = env_file.read_text(encoding="utf-8")
    assert "TAVILY_API_KEY=tvly-123" in text
    assert os.environ["TAVILY_API_KEY"] == "tvly-123"


def test_secret_is_set_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "  abc  ")
    assert secret_is_set("TAVILY_API_KEY") is True
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert secret_is_set("TAVILY_API_KEY") is False
