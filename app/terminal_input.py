"""Interactive chat input for the CLI (multiline, paste, editor, file)."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from app.clipboard_paste import ClipboardPayload, clipboard_has_content, read_clipboard
from app.message_parts import (
    AttachmentRegistry,
    UserTurn,
    build_user_turn,
    should_auto_ingest,
)
from app.paste_input import paste_debug

if TYPE_CHECKING:
    from app.project_manager import ProjectManager

PASTE_END_MARKER = ".send"
EXIT_COMMANDS = frozenset({"/exit", "/quit", "exit", "quit"})

CHAT_HINT = """\
Chat input:
  Enter          send message
  Alt+Enter      new line (Escape, then Enter)
  Ctrl+V         paste from OS clipboard (text, images, files)
  /paste         paste fallback (.send on its own line to finish)
  /edit          open $EDITOR (notepad on Windows) for long input
  /file <path>   send a file's contents as your message
  /exit, /quit   leave chat"""


class SlashAction(Enum):
    EXIT = "exit"
    MESSAGE = "message"
    NOT_COMMAND = "not_command"


@dataclass(frozen=True)
class SlashCommandResult:
    action: SlashAction
    message: str = ""


def parse_paste_mode_text(text: str, end_marker: str = PASTE_END_MARKER) -> str:
    """Extract message body from paste-mode text (stops at end_marker line)."""
    lines: list[str] = []
    for line in text.splitlines():
        if line.strip() == end_marker:
            break
        lines.append(line)
    return "\n".join(lines)


def default_editor_argv() -> list[str]:
    """Editor command argv; honors EDITOR/VISUAL, else platform default."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor:
        return shlex.split(editor)
    if sys.platform == "win32":
        return ["notepad"]
    return ["nano"]


def read_message_from_editor(
    initial: str = "",
    *,
    editor_argv: list[str] | None = None,
    run_editor: Callable[[list[str], Path], None] | None = None,
) -> str:
    """Open a temp file in the system editor and return its contents."""
    editor_argv = editor_argv if editor_argv is not None else default_editor_argv()
    run_editor = run_editor if run_editor is not None else _run_editor_subprocess

    fd, tmp_name = tempfile.mkstemp(suffix=".md", prefix="prompter-chat-")
    path = Path(tmp_name)
    try:
        os.close(fd)
        path.write_text(initial, encoding="utf-8")
        run_editor(editor_argv, path)
        return path.read_text(encoding="utf-8").strip()
    finally:
        path.unlink(missing_ok=True)


def _run_editor_subprocess(editor_argv: list[str], path: Path) -> None:
    cmd = [*editor_argv, str(path)]
    subprocess.run(cmd, check=False)


def read_message_from_file(path_str: str) -> str:
    """Read file contents as a chat message."""
    raw = path_str.strip().strip('"').strip("'")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"not a file: {path}")
    return path.read_text(encoding="utf-8")


def is_exit_command(text: str) -> bool:
    return text.strip().lower() in EXIT_COMMANDS


def parse_slash_command(line: str) -> SlashCommandResult:
    """Parse a single-line slash command (first line only)."""
    stripped = line.strip()
    if not stripped:
        return SlashCommandResult(SlashAction.NOT_COMMAND)

    lower = stripped.lower()
    if lower in EXIT_COMMANDS:
        return SlashCommandResult(SlashAction.EXIT)

    if lower == "/paste":
        return SlashCommandResult(SlashAction.MESSAGE, message=_read_paste_mode_interactive())

    if lower == "/edit":
        return SlashCommandResult(SlashAction.MESSAGE, message=read_message_from_editor())

    if lower.startswith("/file"):
        parts = stripped.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            print("usage: /file <path>", file=sys.stderr)
            return SlashCommandResult(SlashAction.NOT_COMMAND)
        try:
            content = read_message_from_file(parts[1])
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return SlashCommandResult(SlashAction.NOT_COMMAND)
        return SlashCommandResult(SlashAction.MESSAGE, message=content)

    return SlashCommandResult(SlashAction.NOT_COMMAND)


def _read_paste_mode_interactive() -> str:
    print(f"Paste mode: paste your message, then type {PASTE_END_MARKER!r} on its own line.")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == PASTE_END_MARKER:
            break
        lines.append(line)
    return "\n".join(lines)


def _register_chat_submit_bindings(bindings) -> None:
    """Register submit keys (Enter, Ctrl+J); skip sequences prompt_toolkit rejects."""
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    for keys in (("enter",), ("c-j",)):
        try:
            bindings.add(*keys)(_submit)
        except ValueError:
            pass


def _prompt_toolkit_available() -> bool:
    if os.environ.get("PROMPTER_SIMPLE_INPUT"):
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import prompt_toolkit  # noqa: F401

        return True
    except ImportError:
        return False


def _insert_at_cursor(buffer, cursor: int, fragment: str) -> int:
    from prompt_toolkit.document import Document

    new_text = buffer.text[:cursor] + fragment + buffer.text[cursor:]
    new_cursor = cursor + len(fragment)
    buffer.document = Document(new_text, new_cursor)
    return new_cursor


def _apply_clipboard_to_buffer(
    event,
    payload: ClipboardPayload,
    *,
    apply_paste,
    attachment_registry: AttachmentRegistry | None,
    project_manager: ProjectManager | None,
    project_id: str | None,
    docs_dir: Path | None,
) -> bool:
    """Apply clipboard payload with image > files > text priority. Returns True if handled."""
    buf = event.current_buffer
    cursor = buf.cursor_position

    if payload.image_bytes and attachment_registry is not None:
        placeholder, _ = attachment_registry.register_image(
            payload.image_bytes, fmt=payload.image_format
        )
        paste_debug("ctrl_v: branch=image")
        _insert_at_cursor(buf, cursor, placeholder)
        return True

    if payload.file_paths and attachment_registry is not None:
        paste_debug(f"ctrl_v: branch=files ({len(payload.file_paths)})")
        offset = cursor
        inserted = False
        for raw_path in payload.file_paths[:8]:
            path = Path(raw_path)
            if not path.is_file():
                continue
            placeholder, part = attachment_registry.register_file(
                path, docs_dir=docs_dir, copy_into_docs=docs_dir is not None
            )
            if (
                project_manager is not None
                and project_id
                and should_auto_ingest(part.path)
            ):
                try:
                    project_manager.add_file(project_id, part.path)
                except (ValueError, OSError) as exc:
                    paste_debug(f"auto-ingest skipped: {exc}")
            offset = _insert_at_cursor(buf, offset, placeholder)
            inserted = True
        if inserted:
            return True

    if payload.text:
        paste_debug("ctrl_v: branch=text")
        apply_paste(event, payload.text, path="ctrl_v_clipboard")
        return True

    return False


def _read_multiline_prompt_toolkit(
    *,
    project_root: Path | None = None,
    project_id: str | None = None,
    project_manager: ProjectManager | None = None,
) -> UserTurn:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import get_app
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    from app.paste_input import (
        PASTE_EXPAND_HINT,
        PasteBufferRegistry,
        apply_paste_to_buffer,
        buffer_has_collapsed_placeholder,
        normalize_paste_text,
        paste_debug,
    )

    registry = PasteBufferRegistry()
    attachment_registry: AttachmentRegistry | None = None
    docs_dir: Path | None = None
    if project_root is not None:
        attachment_registry = AttachmentRegistry(project_root)
        docs_dir = project_root / "docs"

    def _apply_paste(event, data: str, *, path: str) -> None:
        normalized = normalize_paste_text(data)
        paste_debug(f"{path}: received {len(normalized)} chars")
        buf = event.current_buffer
        new_text, new_cursor = apply_paste_to_buffer(
            buf.text, buf.cursor_position, normalized, registry
        )
        buf.document = Document(new_text, new_cursor)

    @Condition
    def _has_collapsed_paste() -> bool:
        try:
            return buffer_has_collapsed_placeholder(get_app().current_buffer.text)
        except Exception:
            return False

    def _bottom_toolbar() -> "HTML | None":
        if _has_collapsed_paste():
            return HTML(f'<style fg="ansibrightblack">{PASTE_EXPAND_HINT}</style>')
        return None

    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _insert_newline(event) -> None:
        event.current_buffer.insert_text("\n")

    _register_chat_submit_bindings(bindings)

    @bindings.add(Keys.BracketedPaste, eager=True)
    def _paste_bracketed(event) -> None:
        _apply_paste(event, event.data, path="bracketed")

    @bindings.add("c-v", eager=True)
    def _paste_ctrl_v(event) -> None:
        payload = read_clipboard()
        if clipboard_has_content(payload):
            if _apply_clipboard_to_buffer(
                event,
                payload,
                apply_paste=_apply_paste,
                attachment_registry=attachment_registry,
                project_manager=project_manager,
                project_id=project_id,
                docs_dir=docs_dir,
            ):
                return
        data = event.app.clipboard.get_data()
        if data.text:
            paste_debug("ctrl_v: fallback prompt_toolkit clipboard")
            _apply_paste(event, data.text, path="ctrl_v")

    session = PromptSession(
        multiline=Condition(lambda: True),
        key_bindings=bindings,
        message="> ",
        bottom_toolbar=_bottom_toolbar,
    )

    paste_debug(
        "prompt_toolkit session ready "
        "(clipboard-first Ctrl+V; bracketed paste when supported)"
    )
    raw = session.prompt()
    return build_user_turn(raw, registry, attachment_registry)


def _read_simple_line() -> UserTurn:
    raw = input("> ")
    return UserTurn.from_text(raw)


class ChatInputSession:
    """Reads chat messages with multiline / slash-command support."""

    def __init__(
        self,
        *,
        use_rich_input: bool | None = None,
        project_id: str | None = None,
        project_root: Path | None = None,
        project_manager: ProjectManager | None = None,
    ) -> None:
        if use_rich_input is None:
            use_rich_input = _prompt_toolkit_available()
        self._use_rich_input = use_rich_input
        self._project_id = project_id
        self._project_root = project_root
        self._project_manager = project_manager

    @staticmethod
    def print_hints() -> None:
        print(CHAT_HINT)

    def read_message(self) -> UserTurn | None:
        """
        Read one user message.

        Returns None on exit; empty UserTurn if the user submitted blank content.
        """
        try:
            if self._use_rich_input:
                turn = _read_multiline_prompt_toolkit(
                    project_root=self._project_root,
                    project_id=self._project_id,
                    project_manager=self._project_manager,
                )
            else:
                turn = _read_simple_line()
        except (EOFError, KeyboardInterrupt):
            raise

        text = turn.text.strip()
        if not text:
            return UserTurn()

        if is_exit_command(text):
            return None

        first_line = text.split("\n", 1)[0].strip()
        if first_line.startswith("/"):
            result = parse_slash_command(first_line)
            if result.action == SlashAction.EXIT:
                return None
            if result.action == SlashAction.MESSAGE:
                return UserTurn.from_text(result.message)
            if "\n" not in text:
                print(f"unknown command: {first_line}", file=sys.stderr)
                return UserTurn()
            return turn

        return turn
