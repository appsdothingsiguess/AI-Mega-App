"""Tests for CLI chat input helpers."""

from pathlib import Path

import pytest

from app.terminal_input import (
    SlashAction,
    _register_chat_submit_bindings,
    default_editor_argv,
    parse_paste_mode_text,
    parse_slash_command,
    read_message_from_editor,
    read_message_from_file,
)


def test_prompt_session_accepts_message_not_prompt_kwarg() -> None:
    """prompt_toolkit 3.x uses ``message=``; ``prompt=`` raises TypeError at startup."""
    import inspect

    from prompt_toolkit import PromptSession

    params = inspect.signature(PromptSession.__init__).parameters
    assert "message" in params
    assert "prompt" not in params


def test_register_chat_submit_bindings_skips_invalid_keys() -> None:
    from prompt_toolkit.key_binding import KeyBindings

    bindings = KeyBindings()
    _register_chat_submit_bindings(bindings)
    assert len(bindings.bindings) == 2

    with pytest.raises(ValueError, match="Invalid key"):
        bindings.add("c-enter")(lambda event: None)


def test_parse_paste_mode_stops_at_send_marker() -> None:
    body = "line one\nline two\n.send\nignored"
    assert parse_paste_mode_text(body) == "line one\nline two"


def test_parse_paste_mode_custom_marker() -> None:
    text = "hello\n---\nmore"
    assert parse_paste_mode_text(text, end_marker="---") == "hello"


def test_parse_paste_mode_empty() -> None:
    assert parse_paste_mode_text("") == ""
    assert parse_paste_mode_text(".send") == ""


def test_default_editor_argv_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr("app.terminal_input.sys.platform", "win32")
    assert default_editor_argv() == ["notepad"]


def test_default_editor_argv_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", "code --wait")
    assert default_editor_argv() == ["code", "--wait"]


def test_read_message_from_editor_uses_runner(tmp_path: Path) -> None:
    captured: list[Path] = []

    def fake_editor(argv: list[str], path: Path) -> None:
        captured.append(path)
        path.write_text("edited body\n", encoding="utf-8")

    assert (
        read_message_from_editor(
            initial="seed",
            editor_argv=["fake-editor"],
            run_editor=fake_editor,
        )
        == "edited body"
    )
    assert len(captured) == 1


def test_read_message_from_file(tmp_path: Path) -> None:
    doc = tmp_path / "note.txt"
    doc.write_text("file contents", encoding="utf-8")
    assert read_message_from_file(str(doc)) == "file contents"


def test_read_message_from_file_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_message_from_file(str(tmp_path / "missing.txt"))


def test_parse_slash_command_exit() -> None:
    result = parse_slash_command("/exit")
    assert result.action == SlashAction.EXIT


def test_parse_slash_command_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tmp_path / "payload.md"
    doc.write_text("from disk", encoding="utf-8")
    result = parse_slash_command(f"/file {doc}")
    assert result.action == SlashAction.MESSAGE
    assert result.message == "from disk"


def test_parse_slash_command_file_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    result = parse_slash_command("/file")
    assert result.action == SlashAction.NOT_COMMAND
    assert "usage" in capsys.readouterr().err


def test_parse_slash_command_paste(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.terminal_input._read_paste_mode_interactive",
        lambda: "pasted\nblock",
    )
    result = parse_slash_command("/paste")
    assert result.action == SlashAction.MESSAGE
    assert result.message == "pasted\nblock"


def test_parse_slash_command_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.terminal_input.read_message_from_editor",
        lambda: "from editor",
    )
    result = parse_slash_command("/edit")
    assert result.action == SlashAction.MESSAGE
    assert result.message == "from editor"


