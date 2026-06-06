"""Large-paste collapse/expand helpers for CLI input (Claude Code-style UX)."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field


BRACKETED_PASTE_START = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"

PASTE_COLLAPSE_MIN_LINES = 2
PASTE_COLLAPSE_MIN_CHARS = 150

PASTE_EXPAND_HINT = "paste again to expand"

PLACEHOLDER_RE = re.compile(
    r"\[Pasted text #(\d+) \+(\d+) (lines|chars)\]"
)

BRACKETED_PASTE_RE = re.compile(
    re.escape(BRACKETED_PASTE_START) + r"(.*?)" + re.escape(BRACKETED_PASTE_END),
    re.DOTALL,
)


def paste_debug(message: str) -> None:
    if os.environ.get("PROMPTER_PASTE_DEBUG"):
        print(f"[paste] {message}", file=sys.stderr)


def normalize_paste_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + 1


def should_collapse_paste(text: str) -> bool:
    normalized = normalize_paste_text(text)
    if len(normalized) >= PASTE_COLLAPSE_MIN_CHARS:
        return True
    return count_lines(normalized) >= PASTE_COLLAPSE_MIN_LINES


def placeholder_suffix(text: str) -> tuple[int, str]:
    """Return (+count, unit) for a collapsed placeholder."""
    normalized = normalize_paste_text(text)
    lines = count_lines(normalized)
    if lines >= PASTE_COLLAPSE_MIN_LINES:
        return max(0, lines - 1), "lines"
    return len(normalized), "chars"


def make_placeholder(paste_id: int, text: str) -> str:
    extra, unit = placeholder_suffix(text)
    return f"[Pasted text #{paste_id} +{extra} {unit}]"


def extract_bracketed_pastes(raw: str) -> list[str]:
    """Pull paste payloads from a raw stream containing bracketed-paste sequences."""
    return [normalize_paste_text(m.group(1)) for m in BRACKETED_PASTE_RE.finditer(raw)]


def buffer_has_collapsed_placeholder(text: str) -> bool:
    return PLACEHOLDER_RE.search(text) is not None


@dataclass
class PasteBufferRegistry:
    """Stores full paste bodies; the editable buffer shows placeholders only."""

    _pastes: dict[int, str] = field(default_factory=dict)
    _next_id: int = 1
    _expanded: set[int] = field(default_factory=set)

    def register(self, text: str) -> tuple[int, str]:
        normalized = normalize_paste_text(text)
        paste_id = self._next_id
        self._next_id += 1
        self._pastes[paste_id] = normalized
        return paste_id, make_placeholder(paste_id, normalized)

    def get_text(self, paste_id: int) -> str | None:
        return self._pastes.get(paste_id)

    def placeholder_for(self, paste_id: int) -> str:
        text = self._pastes[paste_id]
        return make_placeholder(paste_id, text)

    def mark_expanded(self, paste_id: int) -> None:
        self._expanded.add(paste_id)

    def mark_collapsed(self, paste_id: int) -> None:
        self._expanded.discard(paste_id)

    def resolve_message(self, buffer: str) -> str:
        """Replace placeholders with stored paste bodies before send."""
        result = buffer
        for paste_id, body in self._pastes.items():
            result = result.replace(make_placeholder(paste_id, body), body)
        return result

    def find_placeholder_at(self, text: str, cursor: int) -> re.Match[str] | None:
        for match in PLACEHOLDER_RE.finditer(text):
            if match.start() <= cursor <= match.end():
                return match
        return None

    def find_expanded_at(self, text: str, cursor: int) -> tuple[int, int, int] | None:
        for paste_id in self._expanded:
            body = self._pastes.get(paste_id)
            if not body:
                continue
            start = 0
            while True:
                idx = text.find(body, start)
                if idx < 0:
                    break
                end = idx + len(body)
                if idx <= cursor <= end:
                    return paste_id, idx, end
                start = idx + 1
        return None


def apply_paste_to_buffer(
    buffer_text: str,
    cursor: int,
    paste_text: str,
    registry: PasteBufferRegistry,
) -> tuple[str, int]:
    """
    Insert or toggle a paste at ``cursor``.

    When the cursor sits on a collapsed placeholder, paste expands it.
    When the cursor sits inside an expanded paste, paste collapses it again.
    Large pastes are stored in ``registry`` and shown as a placeholder token.
    """
    paste_text = normalize_paste_text(paste_text)

    placeholder = registry.find_placeholder_at(buffer_text, cursor)
    if placeholder is not None:
        paste_id = int(placeholder.group(1))
        body = registry.get_text(paste_id)
        if body is None:
            return buffer_text, cursor
        before = buffer_text[: placeholder.start()]
        after = buffer_text[placeholder.end() :]
        new_text = before + body + after
        registry.mark_expanded(paste_id)
        return new_text, placeholder.start() + len(body)

    expanded = registry.find_expanded_at(buffer_text, cursor)
    if expanded is not None:
        paste_id, start, end = expanded
        token = registry.placeholder_for(paste_id)
        new_text = buffer_text[:start] + token + buffer_text[end:]
        registry.mark_collapsed(paste_id)
        return new_text, start + len(token)

    if should_collapse_paste(paste_text):
        _paste_id, token = registry.register(paste_text)
        before = buffer_text[:cursor]
        after = buffer_text[cursor:]
        return before + token + after, cursor + len(token)

    before = buffer_text[:cursor]
    after = buffer_text[cursor:]
    return before + paste_text + after, cursor + len(paste_text)
