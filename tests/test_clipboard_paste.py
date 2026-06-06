"""Tests for OS clipboard helpers (mocked; no real clipboard in CI)."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit.document import Document

from app.clipboard_paste import ClipboardPayload, clipboard_has_content, read_clipboard
from app.message_parts import AttachmentRegistry, UserTurn, build_user_turn, should_auto_ingest
from app.paste_input import PasteBufferRegistry, apply_paste_to_buffer
from app.terminal_input import _apply_clipboard_to_buffer


def test_read_clipboard_injected_reader() -> None:
    payload = ClipboardPayload(text="hello\nworld")
    result = read_clipboard(reader=lambda: payload)
    assert result.text == "hello\nworld"
    assert not result.image_bytes
    assert not result.file_paths


def test_clipboard_has_content() -> None:
    assert clipboard_has_content(ClipboardPayload(text="x"))
    assert clipboard_has_content(ClipboardPayload(image_bytes=b"\x89PNG"))
    assert clipboard_has_content(ClipboardPayload(file_paths=["/tmp/a.txt"]))
    assert not clipboard_has_content(ClipboardPayload())


def test_apply_clipboard_text_collapses_four_lines() -> None:
    registry = PasteBufferRegistry()
    applied: list[tuple[str, str]] = []

    class FakeBuffer:
        text = "helo "
        cursor_position = 5

        @property
        def document(self) -> Document:
            return Document(self.text, self.cursor_position)

        @document.setter
        def document(self, doc: Document) -> None:
            self.text = doc.text
            self.cursor_position = doc.cursor_position

    class FakeEvent:
        current_buffer = FakeBuffer()

    def fake_apply(event, data: str, *, path: str) -> None:
        applied.append((data, path))
        text, cursor = apply_paste_to_buffer(
            event.current_buffer.text,
            event.current_buffer.cursor_position,
            data,
            registry,
        )
        event.current_buffer.document = Document(text, cursor)

    payload = ClipboardPayload(text="helo\nline two\nline three\nline four")
    handled = _apply_clipboard_to_buffer(
        FakeEvent(),
        payload,
        apply_paste=fake_apply,
        attachment_registry=None,
        project_manager=None,
        project_id=None,
        docs_dir=None,
    )
    assert handled is True
    assert applied[0][1] == "ctrl_v_clipboard"
    assert "[Pasted text #1 +3 lines]" in FakeEvent.current_buffer.text


def test_apply_clipboard_image_placeholder(tmp_path: Path) -> None:
    attachments = AttachmentRegistry(tmp_path)

    class FakeBuffer:
        text = ""
        cursor_position = 0

        @property
        def document(self) -> Document:
            return Document(self.text, self.cursor_position)

        @document.setter
        def document(self, doc: Document) -> None:
            self.text = doc.text
            self.cursor_position = doc.cursor_position

    class FakeEvent:
        current_buffer = FakeBuffer()

    def fake_apply(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("text apply should not run")

    payload = ClipboardPayload(image_bytes=b"\x89PNG\r\n\x1a\n", image_format="png")
    handled = _apply_clipboard_to_buffer(
        FakeEvent(),
        payload,
        apply_paste=fake_apply,
        attachment_registry=attachments,
        project_manager=None,
        project_id=None,
        docs_dir=None,
    )
    assert handled is True
    assert FakeEvent.current_buffer.text == "[Image #1]"
    assert len(list(attachments.attachments_dir.glob("*.png"))) == 1


def test_build_user_turn_with_image_part(tmp_path: Path) -> None:
    paste_registry = PasteBufferRegistry()
    attachments = AttachmentRegistry(tmp_path)
    placeholder, _ = attachments.register_image(b"fake", fmt="png")
    buffer = f"see {placeholder} please"
    turn = build_user_turn(buffer, paste_registry, attachments)
    assert isinstance(turn, UserTurn)
    assert turn.has_images()
    assert turn.text_for_model() == buffer


def test_should_auto_ingest_txt(tmp_path: Path) -> None:
    doc = tmp_path / "note.txt"
    doc.write_text("hi", encoding="utf-8")
    assert should_auto_ingest(doc)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 6_000_000)
    assert not should_auto_ingest(big)
