"""Central async request handler: routing, RAG, tool execution, and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import litellm

from app.config import Settings
from app.message_parts import UserTurn
from app.model_scheduler import ModelScheduler
from app.project_manager import ProjectManager
from app.protocols import EmbeddingService, VectorStore, VisionService
from app.router import HybridRouter
from app.types import RouteResult, RouteSource, SearchResult, ToolCallDelta

logger_llm = logging.getLogger("prompter.llm")
logger_mcp = logging.getLogger("prompter.mcp")

_CLASSIFIER_ROUTE_TIMEOUT_S = 3.0
_DEFAULT_MODEL_LOAD_ESTIMATE_S = 34
_MAX_HISTORY_TURNS = 20

_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
}


def _merge_tool_call_delta(accumulated: list[ToolCallDelta], delta: Any) -> None:
    """Merge streaming tool_call deltas by index."""
    idx = delta.index
    while len(accumulated) <= idx:
        accumulated.append(ToolCallDelta())
    if delta.id:
        accumulated[idx].id = delta.id
    if delta.function and delta.function.name:
        accumulated[idx].name = delta.function.name
    if delta.function and delta.function.arguments:
        accumulated[idx].arguments += delta.function.arguments


def _parse_tool_input(arguments: str) -> dict[str, Any] | str:
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return arguments


def _serialize_search_result(result: SearchResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "source": result.source,
        "source_file": result.source,
        "title": result.title,
        "score": result.score,
        "metadata": result.metadata,
    }


def _format_retrieved_context(chunks: list[SearchResult]) -> str:
    if not chunks:
        return ""
    parts: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        header = f"[{idx}] source={chunk.source}"
        if chunk.score is not None:
            header += f" score={chunk.score}"
        parts.append(f"{header}\n{chunk.text}")
    return "\n\n---\n\n".join(parts)


class ChatOrchestrator:
    """Central request handler. Coordinates routing, retrieval, tool execution, and streaming."""

    def __init__(
        self,
        router: HybridRouter,
        vector_store: VectorStore,
        embedding_service: EmbeddingService,
        vision_service: VisionService | None,
        model_scheduler: ModelScheduler | None,
        settings: Settings,
        projects: ProjectManager,
    ) -> None:
        self.router = router
        self.vector_store = vector_store
        self.embedding_service = embedding_service
        self.vision_service = vision_service
        self.model_scheduler = model_scheduler
        self.settings = settings
        self.projects = projects

    async def handle_message(
        self,
        project_id: str,
        thread_id: str,
        user_content: str | UserTurn,
    ) -> AsyncIterator[str]:
        """Return a streaming async iterator of SSE event JSON strings."""
        turn = self._parse_turn(user_content)
        self.projects.sync_docs(project_id)

        if turn.has_images():
            intent = "vision"
            tools: list[str] = []
        else:
            route = await self._route_with_fallback(turn.text_for_retrieval())
            intent = route.intent
            tools = list(route.tools)

        model_alias = self.router.resolve_model(intent)

        if self._model_needs_loading(model_alias):
            ollama_name = self.settings.alias_to_ollama_name(model_alias)
            yield json.dumps(
                {
                    "type": "model_loading",
                    "model": ollama_name,
                    "estimated_seconds": _DEFAULT_MODEL_LOAD_ESTIMATE_S,
                }
            )

        if (
            model_alias.startswith("local/")
            and self.model_scheduler
            and self.settings.ollama.scheduler_enabled
        ):
            await self.model_scheduler.ensure_loaded(model_alias)

        reply_parts: list[str] = []

        if intent == "vision" and self.vision_service:
            self._persist_user_message(project_id, thread_id, turn)
            images = [img.path.read_bytes() for img in turn.images()]
            if len(images) == 1:
                result = await self.vision_service.analyze(
                    images[0], turn.text_for_retrieval()
                )
            else:
                result = await self.vision_service.analyze_multi(
                    images, turn.text_for_retrieval()
                )
            reply_parts.append(result)
            yield json.dumps({"type": "chunk", "content": result})
            yield json.dumps({"type": "done", "usage": {}})
            self.projects.append_message(project_id, thread_id, "assistant", result)
            return

        retrieved = await self._retrieve(project_id, turn)
        if retrieved:
            yield json.dumps(
                {
                    "type": "sources",
                    "chunks": [_serialize_search_result(r) for r in retrieved],
                }
            )

        history = self.projects.get_thread_messages(project_id, thread_id)
        messages = self._build_messages(
            project_id, turn, retrieved, history, intent
        )
        self._persist_user_message(project_id, thread_id, turn)

        async for event in self._execute_with_tools(model_alias, messages, tools):
            parsed = json.loads(event)
            if parsed.get("type") == "chunk":
                reply_parts.append(parsed.get("content", ""))
            yield event

        self.projects.append_message(
            project_id, thread_id, "assistant", "".join(reply_parts)
        )

    def _parse_turn(self, user_content: str | UserTurn) -> UserTurn:
        if isinstance(user_content, UserTurn):
            return user_content
        return UserTurn.from_text(user_content)

    def _persist_user_message(
        self, project_id: str, thread_id: str, turn: UserTurn
    ) -> None:
        self.projects.append_message(
            project_id,
            thread_id,
            "user",
            turn.text_for_model(),
            attachments=turn.attachment_metadata() or None,
        )

    async def _route_with_fallback(self, text: str) -> RouteResult:
        try:
            return await asyncio.wait_for(
                self.router.route(text),
                timeout=_CLASSIFIER_ROUTE_TIMEOUT_S,
            )
        except TimeoutError:
            return RouteResult(
                intent="general_chat",
                tools=[],
                confidence=0.0,
                source=RouteSource.CLASSIFIER,
            )

    def _model_needs_loading(self, model_alias: str) -> bool:
        if not self.model_scheduler or not model_alias.startswith("local/"):
            return False
        if not self.settings.ollama.scheduler_enabled:
            return False
        ollama_name = self.settings.alias_to_ollama_name(model_alias)
        scheduler = self.model_scheduler
        if ollama_name in scheduler._resident:
            return False
        return ollama_name != scheduler._loaded_main

    async def _retrieve(self, project_id: str, turn: UserTurn) -> list[SearchResult]:
        try:
            project = self.projects.get_project(project_id)
            if project.file_count == 0:
                return []

            enabled = self.projects.get_enabled_sources(project_id)
            query = turn.text_for_retrieval().strip()
            if not query:
                return []

            embeddings = await self.embedding_service.embed([query])
            if not embeddings:
                return []

            filter_dict: dict[str, Any] | None = None
            if enabled:
                filter_dict = {"source": enabled}

            return await self.vector_store.search(
                embeddings[0],
                top_k=self.settings.rag.top_k,
                filter=filter_dict,
            )
        except Exception as exc:
            logging.getLogger("prompter.rag").warning(
                "RAG retrieval failed; continuing without context: %s", exc
            )
            return []

    def _build_messages(
        self,
        project_id: str,
        turn: UserTurn,
        retrieved: list[SearchResult],
        history: list[dict[str, Any]],
        intent: str,
    ) -> list[dict[str, Any]]:
        project = self.projects.get_project(project_id)
        system_sections: list[str] = [
            f"You are a project assistant for '{project.name}'.",
            "Use the project instructions and retrieved document excerpts when answering.",
            "If context does not contain the answer, say so clearly.",
        ]
        if project.system_prompt.strip():
            system_sections.append(
                "## Project instructions\n" + project.system_prompt.strip()
            )

        context_block = _format_retrieved_context(retrieved)
        if context_block:
            system_sections.append("## Retrieved project context\n" + context_block)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(system_sections)}
        ]

        recent = history[-_MAX_HISTORY_TURNS:]
        for item in recent:
            role = item.get("role", "user")
            if role not in {"user", "assistant", "system"}:
                continue
            content = str(item.get("content", "")).strip()
            if content:
                messages.append({"role": role, "content": content})

        user_message = turn.text_for_model()
        images = turn.images()
        if images and intent == "vision":
            content_parts: list[dict[str, Any]] = [
                {"type": "text", "text": user_message}
            ]
            for url in turn.image_data_urls():
                content_parts.append(
                    {"type": "image_url", "image_url": {"url": url}}
                )
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_message})

        return messages

    async def _execute_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[str],
        max_iterations: int = 5,
    ) -> AsyncIterator[str]:
        tool_schemas = [self._get_tool_schema(t) for t in tools] if tools else None
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            logger_llm.info("LiteLLM completion model=%s iteration=%s", model, iteration)
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tool_schemas,
                stream=True,
            )

            text_buffer = ""
            tool_calls: list[ToolCallDelta] = []

            async for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    text_buffer += delta.content
                    yield json.dumps({"type": "chunk", "content": delta.content})

                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        _merge_tool_call_delta(tool_calls, tc_delta)

            if not tool_calls:
                break

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": text_buffer or None,
                "tool_calls": [tc.to_openai_format() for tc in tool_calls],
            }
            messages.append(assistant_msg)

            for tc in tool_calls:
                tool_input = _parse_tool_input(tc.arguments)
                yield json.dumps(
                    {
                        "type": "tool_call",
                        "name": tc.name,
                        "input": tool_input,
                    }
                )
                result = await self._dispatch_tool(tc.name, tc.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
                yield json.dumps(
                    {
                        "type": "tool_result",
                        "name": tc.name,
                        "output": result,
                    }
                )

        if iteration >= max_iterations:
            yield json.dumps(
                {
                    "type": "error",
                    "message": f"Tool loop hit {max_iterations} iteration limit",
                }
            )

        yield json.dumps({"type": "done", "usage": {}})

    def _get_tool_schema(self, name: str) -> dict[str, Any]:
        if name in _TOOL_SCHEMAS:
            return _TOOL_SCHEMAS[name]
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Execute the {name} tool.",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    async def _dispatch_tool(self, name: str, arguments: str) -> str:
        logger_mcp.info("Dispatch tool name=%s arguments=%s", name, arguments)
        return json.dumps(
            {
                "status": "not_implemented",
                "tool": name,
                "message": f"Tool {name!r} is not implemented yet (Task 6).",
            }
        )
