"""Tests for project creation, init scaffold, instructions, and docs sync."""



from pathlib import Path

import pytest

from app.project_manager import (
    INSTRUCTIONS_FILE,
    PROJECT_YAML,
    ProjectManager,
    ProjectNotFoundError,
    ThreadNotFoundError,
)


def test_init_scaffolds_folder(manager: ProjectManager, settings) -> None:

    detail = manager.init_project("My Research", project_id="my-research")

    root = settings.projects_dir / "my-research"

    assert detail.id == "my-research"

    assert (root / PROJECT_YAML).is_file()

    assert (root / INSTRUCTIONS_FILE).is_file()

    assert (root / "docs").is_dir()

    assert (root / ".prompter" / "chunks.json").is_file()

    assert (root / ".prompter" / "threads").is_dir()





def test_instructions_loaded_as_system_prompt(manager: ProjectManager) -> None:

    manager.init_project("Demo", project_id="demo")

    instructions = manager.projects_root / "demo" / INSTRUCTIONS_FILE

    instructions.write_text("Always cite sources.", encoding="utf-8")

    detail = manager.get_project("demo")

    assert detail.system_prompt == "Always cite sources."





def test_list_and_get_project(manager: ProjectManager) -> None:

    manager.init_project("Alpha", project_id="alpha")

    listed = manager.list_projects()

    assert len(listed) == 1

    assert listed[0].id == "alpha"

    detail = manager.get_project("alpha")

    assert detail.name == "Alpha"

    assert detail.instructions_path.endswith(INSTRUCTIONS_FILE)





def test_docs_scan_ingests_files(manager: ProjectManager) -> None:
    manager.init_project("Docs", project_id="doc-proj")
    doc = manager.projects_root / "doc-proj" / "docs" / "notes.md"
    doc.write_text("# Notes\nPrompter stores chunks for retrieval.", encoding="utf-8")
    ingested = manager.sync_docs("doc-proj")
    assert len(ingested) >= 1
    detail = manager.get_project("doc-proj")
    assert detail.file_count == 1
    again = manager.sync_docs("doc-proj")
    assert again == []





def test_resolve_project_ref_by_slug(manager: ProjectManager) -> None:
    manager.init_project("Claude Prompter", project_id="claude-prompter")
    assert manager.resolve_project_ref("claude-prompter") == "claude-prompter"


def test_resolve_project_ref_by_display_name(manager: ProjectManager) -> None:
    manager.init_project("Claude Prompter", project_id="claude-prompter")
    assert manager.resolve_project_ref("Claude Prompter") == "claude-prompter"


def test_resolve_project_ref_case_insensitive_folder(manager: ProjectManager) -> None:
    manager.init_project("Demo", project_id="demo")
    assert manager.resolve_project_ref("DEMO") == "demo"


def test_resolve_project_ref_suggests_close_match(manager: ProjectManager) -> None:
    manager.init_project("Claude Prompter", project_id="claude-prompter")
    with pytest.raises(ProjectNotFoundError, match="Did you mean: claude-prompter"):
        manager.resolve_project_ref("claude-promptr")


def test_thread_delete_clear_rename(manager: ProjectManager) -> None:
    manager.init_project("Threads", project_id="threads-proj")
    thread = manager.create_thread("threads-proj", title="First")
    manager.append_message("threads-proj", thread.id, "user", "hello")
    manager.append_message("threads-proj", thread.id, "assistant", "hi")

    renamed = manager.rename_thread("threads-proj", thread.id, "Renamed")
    assert renamed.title == "Renamed"
    assert renamed.message_count == 2

    cleared = manager.clear_thread_messages("threads-proj", thread.id)
    assert cleared.message_count == 0
    assert manager.get_thread_messages("threads-proj", thread.id) == []

    manager.delete_thread("threads-proj", thread.id)
    with pytest.raises(ThreadNotFoundError):
        manager.get_thread_messages("threads-proj", thread.id)


def test_append_message_persists_model_on_assistant(
    manager: ProjectManager,
) -> None:
    manager.init_project("Model Label", project_id="model-label")
    thread = manager.create_thread("model-label", title="main")
    manager.append_message(
        "model-label",
        thread.id,
        "assistant",
        "Hello from DeepSeek",
        model="remote/deepseek-v4-pro",
    )

    messages = manager.get_thread_messages("model-label", thread.id)
    assert messages[-1]["model"] == "remote/deepseek-v4-pro"


def test_add_file_copies_to_docs(manager: ProjectManager, tmp_path: Path) -> None:

    manager.init_project("Upload", project_id="upload")

    external = tmp_path / "external.md"

    external.write_text("External content for RAG.", encoding="utf-8")

    chunks = manager.add_file("upload", external)

    assert len(chunks) >= 1

    assert (manager.projects_root / "upload" / "docs" / "external.md").is_file()


