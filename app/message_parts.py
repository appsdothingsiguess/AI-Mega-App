"""User message parts, attachment registry, and resolve-on-submit."""

from __future__ import annotations

import base64
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.paste_input import PasteBufferRegistry, normalize_paste_text, paste_debug
from app.rag import PROMPTER_DIRNAME
from app.utils import supported_doc_suffix

AUTO_INGEST_MAX_BYTES = 5_000_000

IMAGE_PLACEHOLDER_RE = re.compile(r"\[Image #(\d+)\]")
FILE_PLACEHOLDER_RE = re.compile(r"\[File: ([^\]]+)\]")

MessagePartKind = Literal["text", "pasted_text", "image", "file"]


@dataclass
class TextPart:
    text: str

    @property
    def kind(self) -> MessagePartKind:
        return "text"


@dataclass
class PastedTextPart:
    paste_id: int
    placeholder: str
    body: str

    @property
    def kind(self) -> MessagePartKind:
        return "pasted_text"


@dataclass
class ImagePart:
    attachment_id: str
    placeholder: str
    path: Path
    mime: str = "image/png"

    @property
    def kind(self) -> MessagePartKind:
        return "image"


@dataclass
class FilePart:
    attachment_id: str
    placeholder: str
    path: Path
    display_name: str

    @property
    def kind(self) -> MessagePartKind:
        return "file"


MessagePart = TextPart | PastedTextPart | ImagePart | FilePart


@dataclass
class UserTurn:
    """Resolved user message for chat and persistence."""

    parts: list[MessagePart] = field(default_factory=list)

    @classmethod
    def from_text(cls, text: str) -> UserTurn:
        if not text:
            return cls()
        return cls(parts=[TextPart(text=text)])

    @property
    def is_empty(self) -> bool:
        return not self.parts or not self.text.strip()

    @property
    def text(self) -> str:
        return self.text_for_model()

    def text_for_retrieval(self) -> str:
        """Plain text for RAG retrieval."""
        return self._join_text_parts()

    def text_for_model(self) -> str:
        """Text sent to the model (placeholders expanded)."""
        return self._join_text_parts()

    def has_images(self) -> bool:
        return any(isinstance(p, ImagePart) for p in self.parts)

    def images(self) -> list[ImagePart]:
        return [p for p in self.parts if isinstance(p, ImagePart)]

    def files(self) -> list[FilePart]:
        return [p for p in self.parts if isinstance(p, FilePart)]

    def attachment_metadata(self) -> list[dict[str, Any]]:
        meta: list[dict[str, Any]] = []
        for part in self.parts:
            if isinstance(part, ImagePart):
                meta.append(
                    {
                        "type": "image",
                        "id": part.attachment_id,
                        "path": str(part.path),
                        "placeholder": part.placeholder,
                        "mime": part.mime,
                    }
                )
            elif isinstance(part, FilePart):
                meta.append(
                    {
                        "type": "file",
                        "id": part.attachment_id,
                        "path": str(part.path),
                        "placeholder": part.placeholder,
                        "name": part.display_name,
                    }
                )
            elif isinstance(part, PastedTextPart):
                meta.append(
                    {
                        "type": "pasted_text",
                        "paste_id": part.paste_id,
                        "placeholder": part.placeholder,
                    }
                )
        return meta

    def _join_text_parts(self) -> str:
        chunks: list[str] = []
        for part in self.parts:
            if isinstance(part, TextPart):
                chunks.append(part.text)
            elif isinstance(part, PastedTextPart):
                chunks.append(part.body)
            elif isinstance(part, ImagePart):
                chunks.append(part.placeholder)
            elif isinstance(part, FilePart):
                chunks.append(part.placeholder)
        return "".join(chunks)

    def image_data_urls(self) -> list[str]:
        urls: list[str] = []
        for img in self.images():
            raw = img.path.read_bytes()
            b64 = base64.standard_b64encode(raw).decode("ascii")
            urls.append(f"data:{img.mime};base64,{b64}")
        return urls


@dataclass
class AttachmentRegistry:
    """Session attachments under ``projects/{id}/.prompter/attachments/``."""

    project_root: Path
    _next_image: int = 1
    _images: dict[int, ImagePart] = field(default_factory=dict)
    _files: dict[str, FilePart] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.attachments_dir = self.project_root / PROMPTER_DIRNAME / "attachments"
        self.attachments_dir.mkdir(parents=True, exist_ok=True)

    def register_image(self, image_bytes: bytes, *, fmt: str = "png") -> tuple[str, ImagePart]:
        image_id = self._next_image
        self._next_image += 1
        ext = "png" if fmt == "png" else fmt.lstrip(".")
        name = f"paste-{image_id}-{uuid.uuid4().hex[:8]}.{ext}"
        path = self.attachments_dir / name
        path.write_bytes(image_bytes)
        placeholder = f"[Image #{image_id}]"
        attachment_id = f"img-{image_id}"
        part = ImagePart(
            attachment_id=attachment_id,
            placeholder=placeholder,
            path=path,
            mime=f"image/{ext}",
        )
        self._images[image_id] = part
        paste_debug(f"attachment: saved image #{image_id} -> {path}")
        return placeholder, part

    def register_file(
        self,
        source: Path,
        *,
        docs_dir: Path | None = None,
        copy_into_docs: bool = True,
    ) -> tuple[str, FilePart]:
        source = source.resolve()
        display_name = source.name
        attachment_id = f"file-{uuid.uuid4().hex[:8]}"
        dest = source
        if copy_into_docs and docs_dir is not None:
            docs_dir.mkdir(parents=True, exist_ok=True)
            target = docs_dir / source.name
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
                dest = target
        placeholder = f"[File: {display_name}]"
        part = FilePart(
            attachment_id=attachment_id,
            placeholder=placeholder,
            path=dest,
            display_name=display_name,
        )
        self._files[attachment_id] = part
        paste_debug(f"attachment: file {display_name} -> {dest}")
        return placeholder, part

    def collect_parts(self) -> list[ImagePart | FilePart]:
        return list(self._images.values()) + list(self._files.values())


def make_image_placeholder(image_id: int) -> str:
    return f"[Image #{image_id}]"


def make_file_placeholder(name: str) -> str:
    return f"[File: {name}]"


def should_auto_ingest(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.stat().st_size > AUTO_INGEST_MAX_BYTES:
        return False
    return supported_doc_suffix(path)


def build_user_turn(
    buffer_text: str,
    paste_registry: PasteBufferRegistry,
    attachment_registry: AttachmentRegistry | None = None,
) -> UserTurn:
    """Resolve paste placeholders and split buffer into message parts."""
    resolved = paste_registry.resolve_message(buffer_text)
    parts: list[MessagePart] = []
    attachment_by_placeholder: dict[str, MessagePart] = {}
    if attachment_registry is not None:
        for img in attachment_registry._images.values():
            attachment_by_placeholder[img.placeholder] = img
        for fp in attachment_registry._files.values():
            attachment_by_placeholder[fp.placeholder] = fp

    pattern = re.compile(
        r"(\[Pasted text #\d+ \+\d+ (?:lines|chars)\]"
        r"|\[Image #\d+\]"
        r"|\[File: [^\]]+\])"
    )
    pos = 0
    for match in pattern.finditer(resolved):
        if match.start() > pos:
            parts.append(TextPart(resolved[pos : match.start()]))
        token = match.group(0)
        if token in attachment_by_placeholder:
            parts.append(attachment_by_placeholder[token])
        else:
            parts.append(TextPart(token))
        pos = match.end()
    if pos < len(resolved):
        parts.append(TextPart(resolved[pos:]))
    if not parts and resolved:
        parts.append(TextPart(resolved))
    return UserTurn(parts=parts)
