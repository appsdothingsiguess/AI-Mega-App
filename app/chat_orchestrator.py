"""Central async request handler: routing, RAG, tool execution, and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import litellm
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    ContextWindowExceededError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from app.config import Settings
from app.debug_trace import debug_event, sanitize_messages_for_trace
from app.litellm_resolver import LitellmAliasError, resolve_litellm_params
from app.message_parts import UserTurn
from app.model_scheduler import ModelScheduler
from app.project_manager import ProjectManager
from app.protocols import EmbeddingService, SearchService, VectorStore, VisionService
from app.router import HybridRouter
from app.tools import web_search as web_search_tool
from app.turn_tracker import TurnRecord, TurnTracker
from app.types import RouteResult, RouteSource, SearchResult, ToolCallDelta

logger_llm = logging.getLogger("prompter.llm")
logger_mcp = logging.getLogger("prompter.mcp")
logger_router = logging.getLogger("prompter.router")

_DEFAULT_MODEL_LOAD_ESTIMATE_S = 34
_MAX_HISTORY_TURNS = 20
_LLM_LOG_TEXT_MAX = 2000
_SYNTHESIS_NUDGE = (
    "Using the tool results above, answer the original question in clear plain text. "
    "Do not call any tools or output JSON."
)
_TOOLS_REQUIRED_ERROR = (
    "The selected model did not call the required tools for this request. "
    "Clear the model override in the selector to use the auto-routed model, "
    "or choose a tool-capable model."
)

_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "web_search": web_search_tool.TOOL_SCHEMA,
}


def _merge_tool_call_delta(accumulated: list[ToolCallDelta], delta: Any) -> None:
    """Merge streaming tool_call deltas by index."""
    idx = delta.index if delta.index is not None else 0
    while len(accumulated) <= idx:
        accumulated.append(ToolCallDelta())
    if delta.id:
        accumulated[idx].id = delta.id
    if delta.function and delta.function.name:
        accumulated[idx].name = delta.function.name
    if delta.function and delta.function.arguments:
        accumulated[idx].arguments += delta.function.arguments


_DEEPSEEK_THINKING_RE = re.compile(
    r"<\s*(?:think|redacted_thinking)\s*>.*?<\s*/\s*(?:think|redacted_thinking)\s*>",
    re.DOTALL | re.IGNORECASE,
)


def _is_deepseek_r1_model(model_alias: str, resolved_model: str | None = None) -> bool:
    combined = f"{model_alias} {resolved_model or ''}".lower()
    return "deepseek-r1" in combined


def _strip_deepseek_reasoning(text: str) -> tuple[str, str]:
    reasoning_parts = _DEEPSEEK_THINKING_RE.findall(text)
    cleaned = _DEEPSEEK_THINKING_RE.sub("", text).strip()
    return cleaned, "".join(reasoning_parts)


def _parse_single_tool_call_object(
    parsed: dict[str, Any], available_tools: list[str]
) -> ToolCallDelta | None:
    name: str | None = None
    arguments: Any = {}

    if parsed.get("name") in available_tools:
        name = str(parsed["name"])
        arguments = parsed.get("arguments", {})
    elif isinstance(parsed.get("function"), dict):
        fn = parsed["function"]
        if fn.get("name") in available_tools:
            name = str(fn["name"])
            arguments = fn.get("arguments", {})

    if not name:
        return None

    if isinstance(arguments, str):
        args_str = arguments
    else:
        args_str = json.dumps(arguments if isinstance(arguments, dict) else {})

    return ToolCallDelta(
        id=f"call_{uuid.uuid4().hex[:12]}",
        name=name,
        arguments=args_str,
    )


def _extract_tool_calls_from_text(
    text: str, available_tools: list[str]
) -> list[ToolCallDelta]:
    candidates = [text.strip()]
    candidates.extend(
        block.strip()
        for block in re.findall(
            r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE
        )
        if block.strip()
    )

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        tool_call = _parse_single_tool_call_object(parsed, available_tools)
        if tool_call is not None:
            return [tool_call]
    return []


def _strip_tool_json_from_text(text: str) -> str:
    cleaned = text
    for match in re.finditer(
        r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE
    ):
        try:
            json.loads(match.group(1).strip())
            cleaned = cleaned.replace(match.group(0), "")
        except json.JSONDecodeError:
            continue
    stripped = cleaned.strip()
    try:
        json.loads(stripped)
        return ""
    except json.JSONDecodeError:
        return stripped


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


def _user_safe_error(exc: Exception) -> str:
    """Map LiteLLM/provider errors to user-safe messages without leaking secrets."""
    if isinstance(exc, AuthenticationError):
        return "Authentication failed. Check your API key configuration."
    if isinstance(exc, RateLimitError):
        return "Rate limit exceeded. Please try again later."
    if isinstance(exc, ContextWindowExceededError):
        return "The conversation exceeded the model's context window."
    if isinstance(exc, NotFoundError):
        return "The requested model was not found."
    if isinstance(exc, (APIConnectionError, ServiceUnavailableError)):
        return "Could not reach the model provider. Please try again later."
    if isinstance(exc, Timeout):
        return "The model request timed out. Please try again."
    return str(exc)


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


def _format_tool_appendix(tools: list[str]) -> str:
    lines = [
        "## Available tools",
        f"Tools enabled for this turn: {', '.join(tools)}",
        "",
    ]
    for name in tools:
        schema = _TOOL_SCHEMAS.get(name)
        description = ""
        if schema:
            fn = schema.get("function", schema)
            if isinstance(fn, dict):
                description = str(fn.get("description", ""))
        if description:
            lines.append(f"- {name}: {description}")
        else:
            lines.append(f"- {name}")
    lines.extend(
        [
            "",
            "Call tools through the provided function interface. "
            "Wait for tool results before giving your final answer. "
            "Do not emit tool calls as plain JSON in your reply text.",
        ]
    )
    return "\n".join(lines)


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
        search_service: SearchService | None = None,
        turn_tracker: TurnTracker | None = None,
    ) -> None:
        self.router = router
        self.vector_store = vector_store
        self.embedding_service = embedding_service
        self.vision_service = vision_service
        self.model_scheduler = model_scheduler
        self.settings = settings
        self.projects = projects
        self.search_service = search_service
        self.turn_tracker = turn_tracker

    async def handle_message(
        self,
        project_id: str,
        thread_id: str,
        user_content: str | UserTurn,
        *,
        model_override: str | None = None,
    ) -> AsyncIterator[str]:
        """Return a streaming async iterator of SSE event JSON strings."""
        _t_start = time.perf_counter()
        turn = self._parse_turn(user_content)
        self.projects.sync_docs(project_id)
        sse_trace = self.settings.debug.sse_trace

        user_text = turn.text_for_retrieval()
        turn_record = TurnRecord(
            turn_id=str(uuid.uuid4()),
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            project_id=project_id,
            thread_id=thread_id,
            user_input=user_text[:500],
        )

        route: RouteResult | None = None
        if turn.has_images():
            intent = "vision"
            tools: list[str] = []
            _t_route_done = time.perf_counter()
            turn_record.intent = intent
            turn_record.route_source = RouteSource.VISION_OVERRIDE.value
            turn_record.route_confidence = 1.0
        else:
            route = await self._route_with_fallback(turn.text_for_retrieval())
            _t_route_done = time.perf_counter()
            intent = route.intent
            tools = list(route.tools)
            turn_record.intent = intent
            turn_record.route_source = route.source.value
            turn_record.route_confidence = route.confidence

        override = (model_override or "").strip()
        if override:
            model_alias = override
            logger_router.info(
                "model_override applied: %s (intent=%s)", model_alias, intent
            )
        else:
            model_alias = self.router.resolve_model(intent)
        turn_record.model_alias = model_alias
        turn_record.tools_available = list(tools)

        yield json.dumps({"type": "routed", "model": model_alias, "intent": intent})

        if route is not None:
            route_elapsed = (_t_route_done - _t_start) * 1000
            route_payload = {
                "intent": route.intent,
                "tools": route.tools,
                "source": route.source.value,
                "confidence": route.confidence,
                "model_alias": model_alias,
            }
            if sse_trace:
                yield debug_event("route", route_payload, elapsed_ms=route_elapsed)
            if self.settings.debug.router_decisions:
                logger_router.info(json.dumps(route_payload))

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
            yield json.dumps({"type": "done", "usage": {}, "model": model_alias})
            self.projects.append_message(
                project_id, thread_id, "assistant", result, model=model_alias
            )
            turn_record.total_elapsed_ms = (time.perf_counter() - _t_start) * 1000
            if self.turn_tracker:
                self.turn_tracker.record(turn_record)
            return

        _t_rag_start = time.perf_counter()
        retrieved = await self._retrieve(project_id, turn)
        _t_rag_done = time.perf_counter()

        turn_record.rag_chunks_retrieved = len(retrieved)
        turn_record.rag_sources = [
            {"source": c.source, "score": c.score, "title": c.title}
            for c in retrieved
        ]

        if sse_trace:
            rag_elapsed = (_t_rag_done - _t_rag_start) * 1000
            yield debug_event(
                "rag",
                {
                    "chunk_count": len(retrieved),
                    "sources": [
                        {
                            "source": chunk.source,
                            "score": chunk.score,
                            "title": chunk.title,
                        }
                        for chunk in retrieved
                    ],
                },
                elapsed_ms=rag_elapsed,
            )
        if retrieved:
            yield json.dumps(
                {
                    "type": "sources",
                    "chunks": [_serialize_search_result(r) for r in retrieved],
                }
            )

        history = self.projects.get_thread_messages(project_id, thread_id)
        messages = self._build_messages(
            project_id, turn, retrieved, history, intent, tools
        )
        if sse_trace:
            yield debug_event(
                "messages",
                {"messages": sanitize_messages_for_trace(messages)},
            )
        self._persist_user_message(project_id, thread_id, turn)

        # Mutable dict for _execute_with_tools to write timing/usage back.
        phase_data: dict[str, Any] = {
            "llm_iterations": 0,
            "tools_invoked": [],
            "token_usage": None,
            "llm_elapsed_ms": 0.0,
            "tools_elapsed_ms": 0.0,
            "resolved_model": "",
            "api_base": None,
        }

        try:
            async for event in self._execute_with_tools(
                model_alias, messages, tools, sse_trace=sse_trace, phase_data=phase_data
            ):
                parsed = json.loads(event)
                if parsed.get("type") == "chunk":
                    reply_parts.append(parsed.get("content", ""))
                yield event
        except Exception as exc:
            turn_record.error = str(exc)
            raise
        finally:
            _t_end = time.perf_counter()
            turn_record.llm_iterations = phase_data["llm_iterations"]
            turn_record.tools_invoked = phase_data["tools_invoked"]
            turn_record.token_usage = phase_data["token_usage"]
            turn_record.resolved_model = phase_data["resolved_model"]
            turn_record.api_base = phase_data["api_base"]
            turn_record.total_elapsed_ms = (_t_end - _t_start) * 1000
            turn_record.phase_timings = {
                "route": (_t_route_done - _t_start) * 1000,
                "rag": (_t_rag_done - _t_rag_start) * 1000,
                "llm": phase_data["llm_elapsed_ms"],
                "tools": phase_data["tools_elapsed_ms"],
            }
            if self.turn_tracker:
                self.turn_tracker.record(turn_record)

        self.projects.append_message(
            project_id, thread_id, "assistant", "".join(reply_parts), model=model_alias
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
        timeout_s = self.settings.health.classifier_timeout_s or 30.0
        try:
            return await asyncio.wait_for(
                self.router.route(text),
                timeout=timeout_s,
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
        tools: list[str],
    ) -> list[dict[str, Any]]:
        project = self.projects.get_project(project_id)
        platform = self.settings.assistant.system_prompt.replace(
            "{project_name}", project.name
        )
        sections: list[str] = [platform.strip()]
        if tools:
            sections.append(_format_tool_appendix(tools))
        if project.system_prompt.strip():
            sections.append("## Project instructions\n" + project.system_prompt.strip())

        context_block = _format_retrieved_context(retrieved)
        if context_block:
            sections.append("## Retrieved project context\n" + context_block)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(sections)}
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

    async def _stream_final_answer(
        self,
        model: str,
        messages: list[dict[str, Any]],
        litellm_kwargs: dict[str, Any],
        *,
        sse_trace: bool = False,
        iteration: int = 0,
    ) -> AsyncIterator[str]:
        """Force a text-only completion after tool results (local models that re-call tools)."""
        synthesis_messages = [
            *messages,
            {"role": "user", "content": _SYNTHESIS_NUDGE},
        ]
        if sse_trace:
            yield debug_event(
                "llm_request",
                {
                    "alias": model,
                    "resolved_model": litellm_kwargs.get("model"),
                    "api_base": litellm_kwargs.get("api_base"),
                    "iteration": iteration,
                    "synthesis": True,
                },
            )

        _t_start = time.perf_counter()
        try:
            response = await litellm.acompletion(
                messages=synthesis_messages,
                stream=True,
                tools=None,
                **litellm_kwargs,
            )
            text_buffer = ""
            async for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    cleaned = _strip_tool_json_from_text(delta.content)
                    if cleaned:
                        text_buffer += cleaned
                        yield json.dumps({"type": "chunk", "content": cleaned})
        except Exception as exc:
            logger_llm.exception(
                "Synthesis completion failed model=%s",
                litellm_kwargs.get("model"),
            )
            yield json.dumps({"type": "error", "message": _user_safe_error(exc)})
            return

        elapsed_ms = (time.perf_counter() - _t_start) * 1000
        if sse_trace:
            yield debug_event(
                "llm_complete",
                {
                    "iteration": iteration,
                    "text_length": len(text_buffer),
                    "synthesis": True,
                },
                elapsed_ms=elapsed_ms,
            )
            yield debug_event(
                "llm_response",
                {
                    "text_length": len(text_buffer),
                    "text": text_buffer,
                    "text_preview": text_buffer[:200],
                    "synthesis": True,
                },
            )

        if not text_buffer.strip():
            yield json.dumps(
                {
                    "type": "error",
                    "message": "Model did not produce an answer after tool results.",
                }
            )

    async def _execute_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[str],
        max_iterations: int = 5,
        *,
        sse_trace: bool = False,
        phase_data: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        tool_schemas = [self._get_tool_schema(t) for t in tools] if tools else None
        try:
            litellm_kwargs = resolve_litellm_params(self.settings, model)
        except LitellmAliasError as exc:
            yield json.dumps({"type": "error", "message": str(exc)})
            yield json.dumps({"type": "done", "usage": {}, "model": model})
            return

        if phase_data is not None:
            phase_data["resolved_model"] = litellm_kwargs.get("model", "")
            phase_data["api_base"] = litellm_kwargs.get("api_base")

        iteration = 0
        tools_emitted = False
        tools_round_complete = False
        synthesis_attempted = False
        _total_llm_ms = 0.0
        _total_tools_ms = 0.0
        _last_token_usage: dict[str, Any] | None = None
        is_deepseek = _is_deepseek_r1_model(model, litellm_kwargs.get("model"))

        while iteration < max_iterations:
            iteration += 1
            active_tool_schemas = None if tools_round_complete else tool_schemas
            if sse_trace and tool_schemas and not tools_emitted:
                yield debug_event(
                    "tools",
                    {
                        "tool_names": tools,
                        "tool_schemas": tool_schemas,
                    },
                )
                tools_emitted = True

            if sse_trace:
                yield debug_event(
                    "llm_request",
                    {
                        "alias": model,
                        "resolved_model": litellm_kwargs.get("model"),
                        "api_base": litellm_kwargs.get("api_base"),
                        "iteration": iteration,
                        **{
                            key: litellm_kwargs[key]
                            for key in ("api_key",)
                            if key in litellm_kwargs
                        },
                    },
                )

            logger_llm.info(
                "LiteLLM completion alias=%s model=%s iteration=%s",
                model,
                litellm_kwargs.get("model"),
                iteration,
            )
            if logger_llm.isEnabledFor(logging.DEBUG):
                system_prompt = next(
                    (
                        str(message.get("content", ""))
                        for message in messages
                        if message.get("role") == "system"
                    ),
                    "",
                )
                user_preview = next(
                    (
                        str(message.get("content", ""))
                        for message in reversed(messages)
                        if message.get("role") == "user"
                    ),
                    "",
                )
                logger_llm.debug(
                    "LLM request iteration=%s message_count=%s system_prompt=%s user_preview=%s",
                    iteration,
                    len(messages),
                    system_prompt[:_LLM_LOG_TEXT_MAX],
                    user_preview[:200],
                )
            _t_llm_start = time.perf_counter()
            try:
                response = await litellm.acompletion(
                    messages=messages,
                    tools=active_tool_schemas,
                    stream=True,
                    **litellm_kwargs,
                )

                text_buffer = ""
                reasoning_buffer = ""
                tool_calls: list[ToolCallDelta] = []
                last_chunk: Any = None
                defer_content = bool(active_tool_schemas) or is_deepseek

                async for chunk in response:
                    last_chunk = chunk
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    reasoning_part = getattr(delta, "reasoning_content", None) or getattr(
                        delta, "thinking", None
                    )
                    if reasoning_part:
                        reasoning_buffer += reasoning_part

                    if delta.content:
                        text_buffer += delta.content
                        if not defer_content:
                            yield json.dumps(
                                {"type": "chunk", "content": delta.content}
                            )

                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            _merge_tool_call_delta(tool_calls, tc_delta)
            except Exception as exc:
                logger_llm.exception(
                    "LiteLLM completion failed alias=%s model=%s",
                    model,
                    litellm_kwargs.get("model"),
                )
                user_msg = _user_safe_error(exc)
                yield json.dumps({"type": "error", "message": user_msg})
                yield json.dumps({"type": "done", "usage": {}})
                return

            _llm_elapsed = (time.perf_counter() - _t_llm_start) * 1000
            _total_llm_ms += _llm_elapsed

            resolved_model = litellm_kwargs.get("model")
            if _is_deepseek_r1_model(model, resolved_model):
                text_buffer, inline_reasoning = _strip_deepseek_reasoning(text_buffer)
                reasoning_buffer += inline_reasoning

            fallback_used = False
            if not tool_calls and text_buffer.strip() and active_tool_schemas:
                extracted = _extract_tool_calls_from_text(text_buffer, tools)
                if extracted:
                    tool_calls = extracted
                    text_buffer = _strip_tool_json_from_text(text_buffer)
                    fallback_used = True
                    if sse_trace:
                        yield debug_event(
                            "tool_call_fallback",
                            {
                                "source": "text_json",
                                "tool_names": [tc.name for tc in tool_calls],
                            },
                        )

            if tools_round_complete:
                if tool_calls:
                    logger_llm.warning(
                        "Model requested tools after tool round; ignoring alias=%s iteration=%s",
                        model,
                        iteration,
                    )
                    tool_calls = []
                text_buffer = _strip_tool_json_from_text(text_buffer)

            if not tool_calls and active_tool_schemas:
                text_buffer = _strip_tool_json_from_text(text_buffer)

            if not tool_calls and defer_content and text_buffer:
                yield json.dumps({"type": "chunk", "content": text_buffer})

            if logger_llm.isEnabledFor(logging.DEBUG):
                logger_llm.debug(
                    "LLM response iteration=%s text=%s reasoning=%s tool_calls=%s fallback_used=%s",
                    iteration,
                    text_buffer[:_LLM_LOG_TEXT_MAX],
                    reasoning_buffer[:_LLM_LOG_TEXT_MAX],
                    [
                        {"name": tc.name, "arguments": tc.arguments}
                        for tc in tool_calls
                    ],
                    fallback_used,
                )

            # Capture token usage from last chunk if available.
            if last_chunk is not None:
                raw_usage = getattr(last_chunk, "usage", None)
                if raw_usage is not None:
                    try:
                        _last_token_usage = dict(raw_usage) if hasattr(raw_usage, "__iter__") else {
                            "prompt_tokens": getattr(raw_usage, "prompt_tokens", None),
                            "completion_tokens": getattr(raw_usage, "completion_tokens", None),
                            "total_tokens": getattr(raw_usage, "total_tokens", None),
                        }
                    except Exception:
                        _last_token_usage = None

            if sse_trace:
                yield debug_event(
                    "llm_complete",
                    {
                        "iteration": iteration,
                        "text_length": len(text_buffer),
                        "token_usage": _last_token_usage,
                    },
                    elapsed_ms=_llm_elapsed,
                )

            if sse_trace:
                yield debug_event(
                    "llm_response",
                    {
                        "text_length": len(text_buffer),
                        "text": text_buffer,
                        "text_preview": text_buffer[:200],
                        "reasoning": reasoning_buffer or None,
                        "fallback_used": fallback_used,
                        "tool_calls": [
                            {"name": tc.name, "arguments": tc.arguments}
                            for tc in tool_calls
                        ],
                        "had_structured_tool_calls": bool(tool_calls),
                    },
                )
                if reasoning_buffer:
                    yield debug_event(
                        "llm_reasoning",
                        {
                            "iteration": iteration,
                            "reasoning": reasoning_buffer,
                            "reasoning_preview": reasoning_buffer[:200],
                        },
                    )

            if not tool_calls:
                if (
                    active_tool_schemas
                    and not text_buffer.strip()
                    and not synthesis_attempted
                ):
                    yield json.dumps(
                        {"type": "error", "message": _TOOLS_REQUIRED_ERROR}
                    )
                elif (
                    tools_round_complete
                    and not text_buffer.strip()
                    and not synthesis_attempted
                ):
                    synthesis_attempted = True
                    async for event in self._stream_final_answer(
                        model,
                        messages,
                        litellm_kwargs,
                        sse_trace=sse_trace,
                        iteration=iteration,
                    ):
                        yield event
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
                _t_tool_start = time.perf_counter()
                result = await self._dispatch_tool(tc.name, tc.arguments)
                _tool_elapsed = (time.perf_counter() - _t_tool_start) * 1000
                _total_tools_ms += _tool_elapsed

                if phase_data is not None:
                    input_summary = str(tool_input)[:200] if tool_input else ""
                    output_summary = result[:200] if isinstance(result, str) else str(result)[:200]
                    phase_data["tools_invoked"].append({
                        "name": tc.name,
                        "input_summary": input_summary,
                        "output_summary": output_summary,
                        "elapsed_ms": round(_tool_elapsed, 1),
                    })

                if sse_trace:
                    yield debug_event(
                        "tool_dispatch",
                        {
                            "name": tc.name,
                            "arguments": tc.arguments,
                            "result": result,
                        },
                        elapsed_ms=_tool_elapsed,
                    )
                if not tc.id:
                    logger_mcp.warning(
                        "Tool result appended with empty tool_call_id name=%s",
                        tc.name,
                    )
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

            tools_round_complete = True

        if iteration >= max_iterations and tools_round_complete and not synthesis_attempted:
            synthesis_attempted = True
            async for event in self._stream_final_answer(
                model,
                messages,
                litellm_kwargs,
                sse_trace=sse_trace,
                iteration=iteration,
            ):
                yield event
        elif iteration >= max_iterations:
            yield json.dumps(
                {
                    "type": "error",
                    "message": f"Tool loop hit {max_iterations} iteration limit",
                }
            )

        if phase_data is not None:
            phase_data["llm_iterations"] = iteration
            phase_data["token_usage"] = _last_token_usage
            phase_data["llm_elapsed_ms"] = _total_llm_ms
            phase_data["tools_elapsed_ms"] = _total_tools_ms

        yield json.dumps({"type": "done", "usage": {}, "model": model})

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
        if name == "web_search":
            return await self._dispatch_web_search(arguments)
        return json.dumps(
            {
                "status": "not_implemented",
                "tool": name,
                "message": f"Tool {name!r} is not implemented yet.",
            }
        )

    async def _dispatch_web_search(self, arguments: str) -> str:
        if self.search_service is None:
            return json.dumps({"error": "Search service unavailable"})

        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"Invalid JSON arguments: {exc}"})

        if not isinstance(args, dict):
            return json.dumps({"error": "Invalid arguments: expected object"})

        query = args.get("query")
        if not query or not isinstance(query, str):
            return json.dumps({"error": "Missing or invalid required parameter: query"})

        max_results = args.get("max_results", web_search_tool.DEFAULT_MAX_RESULTS)
        if not isinstance(max_results, int):
            return json.dumps({"error": "max_results must be an integer"})

        try:
            return await web_search_tool.execute(
                query, max_results, self.search_service
            )
        except Exception as exc:
            logger_mcp.exception("web_search failed")
            return json.dumps({"error": str(exc)})
