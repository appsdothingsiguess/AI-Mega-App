"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from app.config import Settings
from app.project_manager import ProjectManager


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.chdir(tmp_path)
    projects = tmp_path / "projects"
    projects.mkdir()
    return Settings(projects_dir=projects, data_dir=tmp_path / "data", lmstudio_model="test-model")


@pytest.fixture
def manager(settings: Settings) -> ProjectManager:
    return ProjectManager(settings)
