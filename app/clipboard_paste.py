"""OS clipboard access for Ctrl+V (text, images, file paths)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.paste_input import paste_debug


@dataclass
class ClipboardPayload:
    text: str | None = None
    image_bytes: bytes | None = None
    image_format: str = "png"
    file_paths: list[str] = field(default_factory=list)


ClipboardReader = Callable[[], ClipboardPayload]


def read_clipboard(*, reader: ClipboardReader | None = None) -> ClipboardPayload:
    """Read the OS clipboard; priority is image, then files, then text."""
    if reader is not None:
        payload = reader()
        paste_debug(f"clipboard (injected reader): text={bool(payload.text)} "
                    f"image={bool(payload.image_bytes)} files={len(payload.file_paths)}")
        return payload

    if sys.platform == "win32":
        payload = _read_clipboard_windows()
    elif sys.platform == "darwin":
        payload = _read_clipboard_macos()
    else:
        payload = _read_clipboard_fallback()

    paste_debug(
        f"clipboard: text={bool(payload.text)} "
        f"image={bool(payload.image_bytes)} files={len(payload.file_paths)}"
    )
    return payload


def _read_clipboard_windows() -> ClipboardPayload:
    image_bytes: bytes | None = None
    image_format = "png"
    file_paths: list[str] = []
    text: str | None = None

    try:
        import win32clipboard  # type: ignore[import-untyped]
        from PIL import ImageGrab

        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
                try:
                    raw = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
                    file_paths = _parse_hdrop(raw)
                except Exception:
                    pass

            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                try:
                    text = str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT))
                except Exception:
                    pass
        finally:
            win32clipboard.CloseClipboard()

        img = ImageGrab.grabclipboard()
        if img is not None and not isinstance(img, list):
            from io import BytesIO

            buf = BytesIO()
            img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
            image_format = "png"
        elif isinstance(img, list) and img:
            first = Path(str(img[0]))
            if first.is_file() and not file_paths:
                file_paths = [str(first.resolve())]
    except ImportError:
        return _read_clipboard_fallback()

    if image_bytes:
        return ClipboardPayload(image_bytes=image_bytes, image_format=image_format)
    if file_paths:
        return ClipboardPayload(file_paths=file_paths)
    if text and text.strip():
        return ClipboardPayload(text=text)
    return ClipboardPayload()


def _parse_hdrop(raw: object) -> list[str]:
    """Parse CF_HDROP payload from pywin32."""
    paths: list[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            p = Path(str(item))
            if p.exists():
                paths.append(str(p.resolve()))
        return paths
    if isinstance(raw, str):
        for line in raw.replace("\x00", "\n").splitlines():
            line = line.strip()
            if line and Path(line).exists():
                paths.append(str(Path(line).resolve()))
    return paths


def _read_clipboard_macos() -> ClipboardPayload:
    try:
        from PIL import ImageGrab

        img = ImageGrab.grabclipboard()
        if img is not None and not isinstance(img, list):
            from io import BytesIO

            buf = BytesIO()
            img.save(buf, format="PNG")
            return ClipboardPayload(image_bytes=buf.getvalue(), image_format="png")
        if isinstance(img, list):
            paths = [str(Path(p).resolve()) for p in img if Path(p).exists()]
            if paths:
                return ClipboardPayload(file_paths=paths)
    except ImportError:
        pass
    return _read_clipboard_fallback()


def _read_clipboard_fallback() -> ClipboardPayload:
    text: str | None = None
    try:
        import pyperclip

        raw = pyperclip.paste()
        if isinstance(raw, str) and raw.strip():
            text = raw
    except Exception:
        pass
    if text:
        return ClipboardPayload(text=text)
    return ClipboardPayload()


def clipboard_has_content(payload: ClipboardPayload) -> bool:
    return bool(
        payload.image_bytes
        or payload.file_paths
        or (payload.text and payload.text.strip())
    )
