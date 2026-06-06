"""Orchestrates RAG retrieval, prompt assembly, and LM Studio inference."""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path
from typing import Any

from app.config import Settings
from app.lmstudio_client import (
    ChatMessage,
    ImageContentPart,
    LMStudioClient,
    TextContentPart,
    flatten_message_content,
)
from app.message_parts import ImagePart, UserTurn
from app.project_manager import ProjectManager
from app.rag import ChunkStore, DOCS_DIRNAME
from app.schemas import ChatResponse

logger = logging.getLogger(__name__)

VISION_NAME_HINTS = (
    "vision",
    "vl-",
    "-vl",
    "llava",
    "pixtral",
    "gemini",
    "qwen2-vl",
    "qwen-vl",
    "minicpm-v",
    "bakllava",
    "moondream",
)


class ChatService:
    """End-to-end chat for a project thread."""

    def __init__(
        self,
        settings: Settings,
        projects: ProjectManager,
        lm_client: LMStudioClient | None = None,
    ) -> None:
        self.settings = settings
        self.projects = projects
        self.lm_client = lm_client or LMStudioClient(settings)

    def send_message(
        self,
        project_id: str,
        thread_id: str,
        user_content: str | UserTurn,
    ) -> ChatResponse:
        turn = (
            user_content
            if isinstance(user_content, UserTurn)
            else UserTurn.from_text(user_content)
        )
        self.projects.sync_docs(project_id)
        project = self.projects.get_project(project_id)
        root = self.projects.projects_root / project_id
        store = ChunkStore(root, self.settings)

        retrieval_text = turn.text_for_retrieval()
        enabled_list = self.projects.get_enabled_sources(project_id)
        source_files: set[str] | None = set(enabled_list) if enabled_list else None
        retrieved = store.retrieve(retrieval_text, top_k=self.settings.rag_top_k, source_files=source_files)
        history = self.projects.get_thread_messages(project_id, thread_id)

        use_vision = self._vision_enabled() and turn.has_images()
        model_text, image_notes = self._prepare_user_content(
            project_id, root, turn, use_vision=use_vision
        )

        messages = build_prompt_messages(
            project_name=project.name,
            system_prompt=project.system_prompt,
            retrieved_chunks=retrieved,
            history=history,
            user_message=model_text,
            user_images=turn.images() if use_vision else [],
        )

        if self.settings.debug_prompts:
            _log_prompt(messages)

        persist_text = turn.text_for_model()
        if image_notes:
            persist_text = f"{persist_text}\n\n{image_notes}".strip()
        attachments = turn.attachment_metadata()
        self.projects.append_message(
            project_id,
            thread_id,
            "user",
            persist_text,
            attachments=attachments or None,
        )

        reply = self.lm_client.chat(messages, use_vision=use_vision)
        self.projects.append_message(project_id, thread_id, "assistant", reply)

        return ChatResponse(
            thread_id=thread_id,
            reply=reply,
            retrieved_chunks=retrieved,
        )

    def _vision_enabled(self) -> bool:
        flag = self.settings.lmstudio_supports_vision
        if flag == "true":
            return True
        if flag == "false":
            return False
        model = (self.settings.lmstudio_vision_model or self.settings.lmstudio_model).lower()
        return any(hint in model for hint in VISION_NAME_HINTS)

    def _prepare_user_content(
        self,
        project_id: str,
        project_root: Path,
        turn: UserTurn,
        *,
        use_vision: bool,
    ) -> tuple[str, str]:
        """Return model-facing text and optional user notices for non-vision paths."""
        if use_vision or not turn.has_images():
            return turn.text_for_model(), ""

        docs_dir = project_root / DOCS_DIRNAME
        docs_dir.mkdir(parents=True, exist_ok=True)
        notes: list[str] = []
        for img in turn.images():
            dest = docs_dir / f"clipboard-{img.path.name}"
            shutil.copy2(img.path, dest)
            notes.append(
                f"Image saved to docs/{dest.name}; load a vision model "
                f"(set LMSTUDIO_VISION_MODEL) for direct image Q&A."
            )
        return turn.text_for_model(), "\n".join(notes)


def build_prompt_messages(
    *,
    project_name: str,
    system_prompt: str,
    retrieved_chunks: list[dict[str, Any]],
    history: list[dict[str, Any]],
    user_message: str,
    user_images: list[ImagePart] | None = None,
    max_history_turns: int = 20,
) -> list[ChatMessage]:
    """Assemble inspectable messages: system vs retrieved context vs history."""
    system_sections: list[str] = [
        f"You are a project assistant for '{project_name}'.",
        "Use the project instructions and retrieved document excerpts when answering.",
        "If context does not contain the answer, say so clearly.",
    ]
    if system_prompt.strip():
        system_sections.append("## Project instructions\n" + system_prompt.strip())

    context_block = format_retrieved_context(retrieved_chunks)
    if context_block:
        system_sections.append("## Retrieved project context\n" + context_block)

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content="\n\n".join(system_sections))
    ]

    recent = history[-max_history_turns:]
    for item in recent:
        role = item.get("role", "user")
        if role not in {"user", "assistant", "system"}:
            continue
        content = str(item.get("content", "")).strip()
        if content:
            messages.append(ChatMessage(role=role, content=content))

    if user_images:
        parts: list[TextContentPart | ImageContentPart] = [
            TextContentPart(content=user_message)
        ]
        for img in user_images:
            raw = img.path.read_bytes()
            import base64

            b64 = base64.standard_b64encode(raw).decode("ascii")
            parts.append(ImageContentPart(data_url=f"data:{img.mime};base64,{b64}"))
        messages.append(ChatMessage(role="user", content=parts))
    else:
        messages.append(ChatMessage(role="user", content=user_message))
    return messages


def format_retrieved_context(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return ""
    parts: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        source = chunk.get("source_file", "unknown")
        score = chunk.get("score")
        header = f"[{idx}] source={source}"
        if score is not None:
            header += f" score={score}"
        parts.append(f"{header}\n{chunk.get('text', '')}")
    return "\n\n---\n\n".join(parts)


def _log_prompt(messages: list[ChatMessage]) -> None:
    lines = ["=== DEBUG_PROMPTS: assembled payload ==="]
    for msg in messages:
        lines.append(f"--- {msg.role.upper()} ---")
        lines.append(flatten_message_content(msg.content))
    lines.append("=== end DEBUG_PROMPTS ===")
    print("\n".join(lines), file=sys.stderr)
