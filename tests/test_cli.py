"""CLI argument parsing tests."""

from app.main import _join_project_ref, _resolve_cli_project, build_cli_parser
from app.project_manager import ProjectManager, ProjectNotFoundError


def test_chat_parses_multi_word_project_ref() -> None:
    parser = build_cli_parser()
    args = parser.parse_args(["chat", "Claude", "Prompter"])
    assert args.project_ref == ["Claude", "Prompter"]
    assert _join_project_ref(args.project_ref) == "Claude Prompter"


def test_add_file_parses_multi_word_project_and_path() -> None:
    parser = build_cli_parser()
    args = parser.parse_args(["add-file", "Claude", "Prompter", "notes.pdf"])
    assert args.path_args == ["Claude", "Prompter", "notes.pdf"]


def test_resolve_cli_project_multi_word(
    manager: ProjectManager,
) -> None:
    manager.init_project("Claude Prompter", project_id="claude-prompter")
    assert (
        _resolve_cli_project(manager, ["Claude", "Prompter"]) == "claude-prompter"
    )


def test_resolve_cli_project_slug(manager: ProjectManager) -> None:
    manager.init_project("Claude Prompter", project_id="claude-prompter")
    assert _resolve_cli_project(manager, ["claude-prompter"]) == "claude-prompter"
