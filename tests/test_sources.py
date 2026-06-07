"""Tests for sources.json round-trip and RAG filter with enabled sources."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.project_manager import ProjectManager
from app.rag import PROMPTER_DIRNAME, ChunkStore


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.chdir(tmp_path)
    projects = tmp_path / "projects"
    projects.mkdir()
    return Settings(projects_dir=projects, data_dir=tmp_path / "data")


@pytest.fixture
def manager(settings: Settings) -> ProjectManager:
    return ProjectManager(settings)


@pytest.fixture
def project_id(manager: ProjectManager) -> str:
    detail = manager.init_project("Source Test")
    return detail.id


# ---------------------------------------------------------------------------
# sources.json round-trip
# ---------------------------------------------------------------------------

def test_get_enabled_sources_default_empty(manager: ProjectManager, project_id: str) -> None:
    """Fresh project has empty enabled list (= all docs)."""
    enabled = manager.get_enabled_sources(project_id)
    assert enabled == []


def test_set_and_get_enabled_sources(manager: ProjectManager, project_id: str) -> None:
    manager.set_enabled_sources(project_id, ["a.pdf", "b.md"])
    assert manager.get_enabled_sources(project_id) == ["a.pdf", "b.md"]


def test_set_enabled_sources_persists(manager: ProjectManager, project_id: str, settings: Settings) -> None:
    """Enabled list survives creating a new ProjectManager instance."""
    manager.set_enabled_sources(project_id, ["notes.txt"])
    new_manager = ProjectManager(settings)
    assert new_manager.get_enabled_sources(project_id) == ["notes.txt"]


def test_list_doc_files_empty(manager: ProjectManager, project_id: str) -> None:
    state = manager.list_doc_files(project_id)
    assert state.files == []
    assert state.default_new_enabled is True


def test_list_doc_files_shows_ingested(manager: ProjectManager, project_id: str, settings: Settings) -> None:
    """After ingesting a file it appears in list_doc_files with ingested=True."""
    root = manager._project_dir(project_id)
    store = ChunkStore(root, settings)
    doc = store.docs_dir / "facts.txt"
    doc.write_text("France is in Europe.", encoding="utf-8")
    store.ingest_file(doc, copy_to_docs=False)

    state = manager.list_doc_files(project_id)
    assert len(state.files) == 1
    assert state.files[0].name == "facts.txt"
    assert state.files[0].ingested is True


def test_list_doc_files_enabled_flag(manager: ProjectManager, project_id: str, settings: Settings) -> None:
    """Enabled flag respects sources.json."""
    root = manager._project_dir(project_id)
    store = ChunkStore(root, settings)

    for name in ("a.txt", "b.txt"):
        doc = store.docs_dir / name
        doc.write_text("content", encoding="utf-8")
        store.ingest_file(doc, copy_to_docs=False)

    # Enable only a.txt
    manager.set_enabled_sources(project_id, ["a.txt"])
    state = manager.list_doc_files(project_id)
    by_name = {f.name: f for f in state.files}
    assert by_name["a.txt"].enabled is True
    assert by_name["b.txt"].enabled is False


def test_delete_doc_file(manager: ProjectManager, project_id: str, settings: Settings) -> None:
    root = manager._project_dir(project_id)
    store = ChunkStore(root, settings)
    doc = store.docs_dir / "del.txt"
    doc.write_text("delete me", encoding="utf-8")
    store.ingest_file(doc, copy_to_docs=False)

    manager.set_enabled_sources(project_id, ["del.txt"])
    manager.delete_doc_file(project_id, "del.txt")

    # File gone
    assert not doc.exists()
    # No chunks for deleted file
    remaining = [c for c in store.load_chunks() if c.source_file == "del.txt"]
    assert remaining == []
    # Removed from enabled list
    assert "del.txt" not in manager.get_enabled_sources(project_id)


# ---------------------------------------------------------------------------
# RAG filter with enabled sources
# ---------------------------------------------------------------------------

def test_retrieve_with_source_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(projects_dir=tmp_path / "projects", data_dir=tmp_path / "data")
    project_dir = tmp_path / "projects" / "rag-filter"
    (project_dir / "docs").mkdir(parents=True)
    (project_dir / PROMPTER_DIRNAME).mkdir(parents=True)
    store = ChunkStore(project_dir, settings)

    doc_a = project_dir / "docs" / "france.txt"
    doc_a.write_text("The capital of France is Paris.", encoding="utf-8")
    store.ingest_file(doc_a, copy_to_docs=False)

    doc_b = project_dir / "docs" / "germany.txt"
    doc_b.write_text("The capital of Germany is Berlin.", encoding="utf-8")
    store.ingest_file(doc_b, copy_to_docs=False)

    # Without filter — both files searched
    hits_all = store.retrieve("capital", source_files=None)
    sources_all = {h["source_file"] for h in hits_all}
    assert "france.txt" in sources_all or "germany.txt" in sources_all

    # Filter to only germany.txt
    hits_de = store.retrieve("capital", source_files={"germany.txt"})
    assert all(h["source_file"] == "germany.txt" for h in hits_de)

    # Filter to file that doesn't exist → empty result
    hits_empty = store.retrieve("capital", source_files={"nonexistent.txt"})
    assert hits_empty == []


def test_retrieve_empty_source_set_returns_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """source_files=None means all chunks (backward compat)."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(projects_dir=tmp_path / "projects", data_dir=tmp_path / "data")
    project_dir = tmp_path / "projects" / "rag-all"
    (project_dir / "docs").mkdir(parents=True)
    (project_dir / PROMPTER_DIRNAME).mkdir(parents=True)
    store = ChunkStore(project_dir, settings)

    doc = project_dir / "docs" / "doc.txt"
    doc.write_text("hello world test content", encoding="utf-8")
    store.ingest_file(doc, copy_to_docs=False)

    # None → all docs searched
    hits = store.retrieve("hello", source_files=None)
    assert len(hits) >= 1
