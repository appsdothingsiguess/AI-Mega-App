"""Tests for app/chat_orchestrator.py."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import litellm.exceptions
import pytest

from app.chat_orchestrator import ChatOrchestrator, _format_tool_appendix
from app.config import (
    AssistantSettings,
    DebugSettings,
    DEFAULT_ASSISTANT_PROMPT,
    ModelsConfig,
    OllamaSettings,
    Settings,
)
from app.message_parts import ImagePart, UserTurn
from app.project_manager import ProjectManager
from app.types import RouteResult, RouteSource, SearchResult


def _settings(**overrides: object) -> Settings:
    return Settings(projects_dir="./projects", data_dir="./data", **overrides)


def _parse_events(events: list[str]) -> list[dict]:
    return [json.loads(event) for event in events]


async def _collect_events(
    orchestrator: ChatOrchestrator,
    project_id: str,
    thread_id: str,
    content: str | UserTurn,
    *,
    model_override: str | None = None,
) -> list[dict]:
    raw: list[str] = []
    async for event in orchestrator.handle_message(
        project_id, thread_id, content, model_override=model_override
    ):
        raw.append(event)
    return _parse_events(raw)


@pytest.fixture
def orchestrator_deps(
    settings: Settings, manager: ProjectManager
) -> dict[str, MagicMock | AsyncMock]:
    router = MagicMock()
    router.route = AsyncMock(
        return_value=RouteResult(
            intent="general_chat",
            tools=[],
            confidence=0.9,
            source=RouteSource.CLASSIFIER,
        )
    )
    router.resolve_model = MagicMock(return_value="remote/deepseek-v4-pro")

    vector_store = AsyncMock()
    vector_store.search = AsyncMock(return_value=[])

    embedding = AsyncMock()
    embedding.embed = AsyncMock(return_value=[[0.1, 0.2]])

    vision = AsyncMock()
    vision.analyze = AsyncMock(return_value="Vision analysis result")
    vision.analyze_multi = AsyncMock(return_value="Multi-image analysis")

    scheduler = AsyncMock()
    scheduler._resident = {"qwen2.5:1.5b", "nomic-embed-text"}
    scheduler._loaded_main = None
    scheduler.ensure_loaded = AsyncMock()

    return {
        "router": router,
        "vector_store": vector_store,
        "embedding": embedding,
        "vision": vision,
        "scheduler": scheduler,
        "settings": settings,
        "projects": manager,
    }


@pytest.fixture
def orchestrator(orchestrator_deps: dict) -> ChatOrchestrator:
    return ChatOrchestrator(
        router=orchestrator_deps["router"],
        vector_store=orchestrator_deps["vector_store"],
        embedding_service=orchestrator_deps["embedding"],
        vision_service=orchestrator_deps["vision"],
        model_scheduler=orchestrator_deps["scheduler"],
        settings=orchestrator_deps["settings"],
        projects=orchestrator_deps["projects"],
    )


@pytest.fixture
def thread_ids(manager: ProjectManager) -> tuple[str, str]:
    project = manager.init_project("Orchestrator Test")
    thread = manager.create_thread(project.id, "main")
    return project.id, thread.id


class _FakeDelta:
    def __init__(
        self,
        *,
        content: str | None = None,
        tool_calls: list | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChunk:
    def __init__(self, delta: _FakeDelta) -> None:
        self.choices = [MagicMock(delta=delta)]


class _FakeFunction:
    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCallDelta:
    def __init__(
        self,
        index: int,
        *,
        id: str | None = None,
        function: _FakeFunction | None = None,
    ) -> None:
        self.index = index
        self.id = id
        self.function = function


async def _fake_text_stream(*_args, **_kwargs):
    for text in ("Hello", " world"):
        yield _FakeChunk(_FakeDelta(content=text))


async def _fake_tool_then_text_stream(*_args, **_kwargs):
    yield _FakeChunk(
        _FakeDelta(
            tool_calls=[
                _FakeToolCallDelta(
                    0,
                    id="call_1",
                    function=_FakeFunction(name="web_search", arguments='{"query":'),
                )
            ]
        )
    )
    yield _FakeChunk(
        _FakeDelta(
            tool_calls=[
                _FakeToolCallDelta(
                    0,
                    function=_FakeFunction(arguments=' "test"}'),
                )
            ]
        )
    )
    for text in ("Done", "."):
        yield _FakeChunk(_FakeDelta(content=text))


async def _fake_tool_only_stream(*_args, **_kwargs):
    yield _FakeChunk(
        _FakeDelta(
            tool_calls=[
                _FakeToolCallDelta(
                    0,
                    id="call_loop",
                    function=_FakeFunction(
                        name="web_search",
                        arguments='{"query":"again"}',
                    ),
                )
            ]
        )
    )


@pytest.mark.asyncio
async def test_basic_chat_flow(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_fake_text_stream,
    ) as mock_completion:
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Hello there"
        )

    assert events[0] == {
        "type": "routed",
        "model": "remote/deepseek-v4-pro",
        "intent": "general_chat",
    }
    assert events[1] == {"type": "chunk", "content": "Hello"}
    assert events[2] == {"type": "chunk", "content": " world"}
    assert events[-1] == {
        "type": "done",
        "usage": {},
        "model": "remote/deepseek-v4-pro",
    }
    orchestrator_deps["router"].route.assert_awaited_once()
    assert mock_completion.await_args.kwargs["model"] == "openai/deepseek-v4-pro"
    assert mock_completion.await_args.kwargs["api_base"] == "https://opencode.ai/zen/go/v1"


@pytest.mark.asyncio
async def test_vision_path(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
    tmp_path: Path,
) -> None:
    project_id, thread_id = thread_ids
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    turn = UserTurn(
        parts=[
            ImagePart(
                attachment_id="img-1",
                placeholder="[Image #1]",
                path=image_path,
                mime="image/png",
            )
        ]
    )

    with patch("app.chat_orchestrator.litellm.acompletion") as mock_completion:
        events = await _collect_events(orchestrator, project_id, thread_id, turn)

    mock_completion.assert_not_called()
    orchestrator_deps["router"].route.assert_not_called()
    orchestrator_deps["vision"].analyze.assert_awaited_once()
    assert events[0] == {
        "type": "routed",
        "model": "remote/deepseek-v4-pro",
        "intent": "vision",
    }
    assert events[1]["type"] == "chunk"
    assert events[1]["content"] == "Vision analysis result"
    assert events[-1] == {
        "type": "done",
        "usage": {},
        "model": "remote/deepseek-v4-pro",
    }


@pytest.mark.asyncio
async def test_tool_execution_loop(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    orchestrator_deps["router"].route = AsyncMock(
        return_value=RouteResult(
            intent="web_search",
            tools=["web_search"],
            confidence=1.0,
            source=RouteSource.KEYWORD,
        )
    )
    orchestrator_deps["router"].resolve_model = MagicMock(
        return_value="remote/kimi-k2-6"
    )

    call_count = 0

    async def _tool_then_text(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            async for chunk in _fake_tool_then_text_stream():
                yield chunk
        else:
            async for chunk in _fake_text_stream():
                yield chunk

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_tool_then_text,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Search for news"
        )

    types = [event["type"] for event in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert "chunk" in types
    assert events[-1]["type"] == "done"
    tool_call = next(event for event in events if event["type"] == "tool_call")
    assert tool_call["name"] == "web_search"
    assert tool_call["input"] == {"query": "test"}


@pytest.mark.asyncio
async def test_tool_loop_limit(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    orchestrator_deps["router"].route = AsyncMock(
        return_value=RouteResult(
            intent="web_search",
            tools=["web_search"],
            confidence=1.0,
            source=RouteSource.KEYWORD,
        )
    )

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_fake_tool_only_stream,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Keep searching"
        )

    error_events = [event for event in events if event["type"] == "error"]
    assert error_events
    assert "5 iteration limit" in error_events[0]["message"]
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_rag_retrieval(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    manager: ProjectManager,
    thread_ids: tuple[str, str],
    tmp_path: Path,
) -> None:
    project_id, thread_id = thread_ids
    docs_dir = manager.projects_root / project_id / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "guide.md").write_text("LM Studio setup guide", encoding="utf-8")
    manager.sync_docs(project_id)

    search_results = [
        SearchResult(
            text="Use LM Studio locally.",
            source="guide.md",
            title="Guide",
            score=0.91,
        )
    ]
    orchestrator_deps["embedding"].embed = AsyncMock(return_value=[[0.5, 0.6]])
    orchestrator_deps["vector_store"].search = AsyncMock(return_value=search_results)

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_fake_text_stream,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "How do I run this?"
        )

    sources = next(event for event in events if event["type"] == "sources")
    assert sources["chunks"][0]["source_file"] == "guide.md"
    orchestrator_deps["embedding"].embed.assert_awaited_once()
    orchestrator_deps["vector_store"].search.assert_awaited_once()


@pytest.mark.asyncio
async def test_classifier_timeout_fallback(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids

    async def _slow_route(_message: str) -> RouteResult:
        await asyncio.sleep(5)
        return RouteResult(
            intent="coding_advanced",
            tools=[],
            confidence=0.5,
            source=RouteSource.CLASSIFIER,
        )

    orchestrator_deps["router"].route = _slow_route

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_fake_text_stream,
    ):
        started = asyncio.get_running_loop().time()
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Explain recursion"
        )
        elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 4.5
    assert events[-1]["type"] == "done"
    orchestrator_deps["router"].resolve_model.assert_called_with("general_chat")


@pytest.mark.asyncio
async def test_model_loading_event(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    local_settings = _settings(
        models=ModelsConfig(general_chat="local/qwen3-8b"),
        ollama=OllamaSettings(scheduler_enabled=True),
        ollama_model_names={"local/qwen3-8b": "qwen3:8b"},
    )
    orchestrator.settings = local_settings
    orchestrator_deps["router"].resolve_model = MagicMock(return_value="local/qwen3-8b")
    orchestrator_deps["scheduler"]._loaded_main = "qwen2.5-coder:7b"
    orchestrator_deps["scheduler"]._resident = {"qwen2.5:1.5b", "nomic-embed-text"}

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_fake_text_stream,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Run locally"
        )

    assert events[0] == {
        "type": "routed",
        "model": "local/qwen3-8b",
        "intent": "general_chat",
    }
    assert events[1]["type"] == "model_loading"
    assert events[1]["model"] == "qwen3:8b"
    assert events[1]["estimated_seconds"] == 34
    orchestrator_deps["scheduler"].ensure_loaded.assert_awaited_once_with(
        "local/qwen3-8b"
    )


@pytest.mark.asyncio
async def test_debug_events_emitted_when_sse_trace_enabled(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    orchestrator.settings = orchestrator.settings.model_copy(
        update={"debug": DebugSettings(sse_trace=True)}
    )

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_fake_text_stream,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Hello there"
        )

    debug_events = [event for event in events if event.get("type") == "debug"]
    stages = [event["stage"] for event in debug_events]
    assert stages == ["route", "rag", "messages", "llm_request", "llm_complete", "llm_response"]
    route = debug_events[0]["data"]
    assert route["intent"] == "general_chat"
    assert route["model_alias"] == "remote/deepseek-v4-pro"
    assert debug_events[1]["data"]["chunk_count"] == 0
    assert isinstance(debug_events[2]["data"]["messages"], list)
    assert debug_events[3]["data"]["alias"] == "remote/deepseek-v4-pro"
    assert debug_events[4]["data"]["text_length"] == len("Hello world")


@pytest.mark.asyncio
async def test_debug_rag_stage_includes_sources_when_retrieved(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    manager: ProjectManager,
    thread_ids: tuple[str, str],
    tmp_path: Path,
) -> None:
    project_id, thread_id = thread_ids
    docs_dir = manager.projects_root / project_id / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "doc.md").write_text("Retrieved chunk body", encoding="utf-8")
    manager.sync_docs(project_id)

    orchestrator.settings = orchestrator.settings.model_copy(
        update={"debug": DebugSettings(sse_trace=True)}
    )
    orchestrator_deps["embedding"].embed = AsyncMock(return_value=[[0.5, 0.6]])
    orchestrator_deps["vector_store"].search = AsyncMock(
        return_value=[
            SearchResult(
                text="Retrieved chunk",
                source="doc.md",
                title="Doc",
                score=0.88,
            )
        ]
    )

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_fake_text_stream,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "What is in the doc?"
        )

    rag_event = next(event for event in events if event.get("stage") == "rag")
    assert rag_event["data"]["chunk_count"] == 1
    assert rag_event["data"]["sources"][0]["source"] == "doc.md"
    assert rag_event["data"]["sources"][0]["score"] == 0.88


@pytest.mark.asyncio
async def test_debug_tool_stages_when_tools_fire(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    orchestrator.settings = orchestrator.settings.model_copy(
        update={"debug": DebugSettings(sse_trace=True)}
    )
    orchestrator_deps["router"].route = AsyncMock(
        return_value=RouteResult(
            intent="web_search",
            tools=["web_search"],
            confidence=1.0,
            source=RouteSource.KEYWORD,
        )
    )
    orchestrator_deps["router"].resolve_model = MagicMock(
        return_value="remote/kimi-k2-6"
    )

    call_count = 0

    async def _tool_then_text(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            async for chunk in _fake_tool_then_text_stream():
                yield chunk
        else:
            async for chunk in _fake_text_stream():
                yield chunk

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_tool_then_text,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Search for news"
        )

    debug_events = [event for event in events if event.get("type") == "debug"]
    stages = [event["stage"] for event in debug_events]
    assert stages == [
        "route",
        "rag",
        "messages",
        "tools",
        "llm_request",
        "llm_complete",
        "llm_response",
        "tool_dispatch",
        "llm_request",
        "llm_complete",
        "llm_response",
    ]
    tools_event = next(event for event in debug_events if event["stage"] == "tools")
    assert tools_event["data"]["tool_names"] == ["web_search"]
    assert tools_event["data"]["tool_schemas"]
    dispatch_event = next(
        event for event in debug_events if event["stage"] == "tool_dispatch"
    )
    assert dispatch_event["data"]["name"] == "web_search"
    assert dispatch_event["data"]["arguments"] == '{"query": "test"}'
    assert "error" in dispatch_event["data"]["result"]


@pytest.mark.asyncio
async def test_no_debug_events_when_sse_trace_disabled(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    orchestrator.settings = orchestrator.settings.model_copy(
        update={"debug": DebugSettings(sse_trace=False)}
    )
    orchestrator_deps["router"].route = AsyncMock(
        return_value=RouteResult(
            intent="web_search",
            tools=["web_search"],
            confidence=1.0,
            source=RouteSource.KEYWORD,
        )
    )

    call_count = 0

    async def _tool_then_text(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            async for chunk in _fake_tool_then_text_stream():
                yield chunk
        else:
            async for chunk in _fake_text_stream():
                yield chunk

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_tool_then_text,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Search for news"
        )

    assert all(event.get("type") != "debug" for event in events)




@pytest.mark.asyncio
async def test_litellm_authentication_error_emits_error_and_done(
    orchestrator: ChatOrchestrator,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids

    async def _raise_auth_error(*_args, **_kwargs):
        raise litellm.exceptions.AuthenticationError(
            message="Invalid API key sk-secret123",
            llm_provider="openai",
            model="openai/deepseek-v4-pro",
        )

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_raise_auth_error,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Hello there"
        )

    error_events = [event for event in events if event["type"] == "error"]
    assert len(error_events) == 1
    assert "sk-secret123" not in error_events[0]["message"]
    assert "Authentication failed" in error_events[0]["message"]
    assert events[-1] == {"type": "done", "usage": {}}


@pytest.mark.asyncio
async def test_model_override_skips_resolve_model_with_tools(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_id, thread_id = thread_ids
    orchestrator_deps["router"].route = AsyncMock(
        return_value=RouteResult(
            intent="web_search",
            tools=["web_search"],
            confidence=1.0,
            source=RouteSource.KEYWORD,
        )
    )
    orchestrator_deps["router"].resolve_model = MagicMock(
        return_value="remote/kimi-k2-6"
    )

    call_count = 0

    async def _tool_then_text(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            async for chunk in _fake_tool_then_text_stream():
                yield chunk
        else:
            async for chunk in _fake_text_stream():
                yield chunk

    with caplog.at_level("INFO", logger="prompter.router"):
        with patch(
            "app.chat_orchestrator.litellm.acompletion",
            side_effect=_tool_then_text,
        ) as mock_completion:
            events = await _collect_events(
                orchestrator,
                project_id,
                thread_id,
                "Search for news",
                model_override="remote/deepseek-v4-pro",
            )

    orchestrator_deps["router"].route.assert_awaited_once()
    orchestrator_deps["router"].resolve_model.assert_not_called()

    routed = next(event for event in events if event["type"] == "routed")
    assert routed == {
        "type": "routed",
        "model": "remote/deepseek-v4-pro",
        "intent": "web_search",
    }

    types = [event["type"] for event in events]
    assert "tool_call" in types

    assert mock_completion.await_args.kwargs["model"] == "openai/deepseek-v4-pro"
    assert (
        mock_completion.await_args.kwargs["api_base"]
        == "https://opencode.ai/zen/go/v1"
    )

    assert any(
        "model_override applied: remote/deepseek-v4-pro" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_model_override_vision_inference_uses_vision_service(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
    tmp_path: Path,
) -> None:
    project_id, thread_id = thread_ids
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    turn = UserTurn(
        parts=[
            ImagePart(
                attachment_id="img-1",
                placeholder="[Image #1]",
                path=image_path,
                mime="image/png",
            )
        ]
    )

    with patch("app.chat_orchestrator.litellm.acompletion") as mock_completion:
        events = await _collect_events(
            orchestrator,
            project_id,
            thread_id,
            turn,
            model_override="remote/deepseek-v4-pro",
        )

    mock_completion.assert_not_called()
    orchestrator_deps["router"].route.assert_not_called()
    orchestrator_deps["router"].resolve_model.assert_not_called()
    orchestrator_deps["vision"].analyze.assert_awaited_once()

    routed = next(event for event in events if event["type"] == "routed")
    assert routed == {
        "type": "routed",
        "model": "remote/deepseek-v4-pro",
        "intent": "vision",
    }


@pytest.mark.asyncio
async def test_routed_sse_and_persisted_model(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    manager: ProjectManager,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_fake_text_stream,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Persist model label"
        )

    routed = next(event for event in events if event["type"] == "routed")
    assert routed == {
        "type": "routed",
        "model": "remote/deepseek-v4-pro",
        "intent": "general_chat",
    }

    messages = manager.get_thread_messages(project_id, thread_id)
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert assistant_msgs[-1]["model"] == "remote/deepseek-v4-pro"


def test_platform_prompt_order(
    orchestrator: ChatOrchestrator,
    manager: ProjectManager,
    thread_ids: tuple[str, str],
) -> None:
    project_id, _thread_id = thread_ids
    manager.update_system_prompt(project_id, "Custom project tone instructions.")

    messages = orchestrator._build_messages(
        project_id,
        UserTurn.from_text("Hello"),
        [],
        [],
        "general_chat",
        [],
    )
    system_content = messages[0]["content"]
    platform_pos = system_content.find("Prompter X")
    project_pos = system_content.find("## Project instructions")
    assert platform_pos >= 0
    assert project_pos >= 0
    assert platform_pos < project_pos


def test_tool_appendix_when_tools_assigned() -> None:
    appendix = _format_tool_appendix(["web_search"])
    assert "## Available tools" in appendix
    assert "web_search" in appendix
    assert "Search the web for current information" in appendix
    assert "Do not emit tool calls as plain JSON" in appendix


def test_default_assistant_prompt_from_settings(
    orchestrator: ChatOrchestrator,
    thread_ids: tuple[str, str],
) -> None:
    project_id, _thread_id = thread_ids
    custom_prompt = 'You are TestBot for "{project_name}". Use tools when needed.'
    orchestrator.settings = orchestrator.settings.model_copy(
        update={"assistant": AssistantSettings(system_prompt=custom_prompt)}
    )

    messages = orchestrator._build_messages(
        project_id,
        UserTurn.from_text("Hi"),
        [],
        [],
        "general_chat",
        ["web_search"],
    )
    system_content = messages[0]["content"]
    project = orchestrator.projects.get_project(project_id)
    assert f'You are TestBot for "{project.name}"' in system_content
    assert "## Available tools" in system_content
    assert DEFAULT_ASSISTANT_PROMPT != custom_prompt


async def _fake_text_json_tool_stream(*_args, **_kwargs):
    payload = json.dumps(
        {"name": "web_search", "arguments": {"query": "S&P 500 close Friday"}}
    )
    yield _FakeChunk(_FakeDelta(content=payload))


@pytest.mark.asyncio
async def test_text_json_tool_call_fallback(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    orchestrator_deps["router"].route = AsyncMock(
        return_value=RouteResult(
            intent="web_search",
            tools=["web_search"],
            confidence=1.0,
            source=RouteSource.KEYWORD,
        )
    )
    orchestrator_deps["router"].resolve_model = MagicMock(
        return_value="local/qwen3-8b"
    )

    call_count = 0

    async def _json_then_text(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            async for chunk in _fake_text_json_tool_stream():
                yield chunk
        else:
            async for chunk in _fake_text_stream():
                yield chunk

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_json_then_text,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "S&P 500 close Friday"
        )

    tool_calls = [event for event in events if event["type"] == "tool_call"]
    assert len(tool_calls) >= 1
    assert tool_calls[0]["name"] == "web_search"
    assert tool_calls[0]["input"] == {"query": "S&P 500 close Friday"}

    chunk_text = "".join(
        event.get("content", "") for event in events if event["type"] == "chunk"
    )
    assert "web_search" not in chunk_text
    assert "S&P 500 close Friday" not in chunk_text or "Hello" in chunk_text


async def _fake_deepseek_reasoning_stream(*_args, **_kwargs):
    think_block = "<think>secret reasoning</think>"
    yield _FakeChunk(_FakeDelta(content=f"{think_block}Hello world"))


@pytest.mark.asyncio
async def test_deepseek_reasoning_stripped(
    orchestrator: ChatOrchestrator,
    orchestrator_deps: dict,
    manager: ProjectManager,
    settings: Settings,
    thread_ids: tuple[str, str],
) -> None:
    project_id, thread_id = thread_ids
    orchestrator.settings = settings.model_copy(
        update={
            "models": ModelsConfig(general_chat="local/deepseek-r1-8b"),
            "ollama_model_names": {"local/deepseek-r1-8b": "deepseek-r1:8b"},
        }
    )
    orchestrator_deps["router"].resolve_model = MagicMock(
        return_value="local/deepseek-r1-8b"
    )

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_fake_deepseek_reasoning_stream,
    ):
        events = await _collect_events(
            orchestrator, project_id, thread_id, "Explain recursion briefly"
        )

    chunk_text = "".join(
        event.get("content", "") for event in events if event["type"] == "chunk"
    )
    assert "secret reasoning" not in chunk_text
    assert "Hello world" in chunk_text

    messages = manager.get_thread_messages(project_id, thread_id)
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert assistant_msgs
    assert "secret reasoning" not in assistant_msgs[-1]["content"]
    assert "Hello world" in assistant_msgs[-1]["content"]
