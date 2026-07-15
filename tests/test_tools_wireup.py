"""Tests for Task 6 tool wire-up: dispatch table, ask_user short-circuit, todo_write events."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.chat_orchestrator import ChatOrchestrator
from app.project_manager import ProjectManager
from app.types import RouteResult, RouteSource


@pytest.fixture
def orchestrator_with_project(
    settings, manager: ProjectManager
) -> tuple[ChatOrchestrator, str, str]:
    project = manager.init_project("Tool Wireup Test")
    thread = manager.create_thread(project.id, "main")

    router = MagicMock()
    router.route = AsyncMock(
        return_value=RouteResult(
            intent="bash",
            tools=["bash", "grep", "glob", "file_ops", "todo_write", "ask_user"],
            confidence=1.0,
            source=RouteSource.KEYWORD,
        )
    )
    router.resolve_model = MagicMock(return_value="remote/deepseek-v4-pro")

    orchestrator = ChatOrchestrator(
        router=router,
        vector_store=AsyncMock(),
        embedding_service=AsyncMock(),
        vision_service=None,
        model_scheduler=None,
        settings=settings,
        projects=manager,
    )
    return orchestrator, project.id, thread.id


# ---------------------------------------------------------------------------
# Schema registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["bash", "grep", "glob", "web_fetch", "file_ops", "todo_write", "ask_user"],
)
def test_get_tool_schema_registered(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str], name: str
) -> None:
    orchestrator, _project_id, _thread_id = orchestrator_with_project
    schema = orchestrator._get_tool_schema(name)
    assert schema["function"]["name"] == name


# ---------------------------------------------------------------------------
# Dispatch: bash / grep / glob / file_ops / todo_write / web_fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_bash_runs_in_project_root(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    orchestrator, project_id, _thread_id = orchestrator_with_project
    raw = await orchestrator._dispatch_tool(
        "bash",
        json.dumps({"command": "echo hello"}),
        project_id=project_id,
    )
    parsed = json.loads(raw)
    assert parsed["exit_code"] == 0
    assert "hello" in parsed["stdout"]


@pytest.mark.asyncio
async def test_dispatch_bash_no_project_context(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    orchestrator, _project_id, _thread_id = orchestrator_with_project
    raw = await orchestrator._dispatch_tool("bash", json.dumps({"command": "echo hi"}))
    parsed = json.loads(raw)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_dispatch_grep_finds_match(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    orchestrator, project_id, _thread_id = orchestrator_with_project
    project_root = orchestrator.projects.projects_root / project_id
    (project_root / "docs").mkdir(parents=True, exist_ok=True)
    (project_root / "docs" / "note.txt").write_text("needle here\n", encoding="utf-8")

    raw = await orchestrator._dispatch_tool(
        "grep",
        json.dumps({"pattern": "needle"}),
        project_id=project_id,
    )
    parsed = json.loads(raw)
    assert len(parsed) == 1
    assert parsed[0]["file"] == "docs/note.txt"


@pytest.mark.asyncio
async def test_dispatch_glob_finds_files(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    orchestrator, project_id, _thread_id = orchestrator_with_project
    project_root = orchestrator.projects.projects_root / project_id
    (project_root / "a.md").write_text("hi", encoding="utf-8")

    raw = await orchestrator._dispatch_tool(
        "glob",
        json.dumps({"pattern": "*.md"}),
        project_id=project_id,
    )
    parsed = json.loads(raw)
    assert "a.md" in parsed


@pytest.mark.asyncio
async def test_dispatch_file_ops_write_and_read(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    orchestrator, project_id, _thread_id = orchestrator_with_project

    write_raw = await orchestrator._dispatch_tool(
        "file_ops",
        json.dumps({"operation": "write", "path": "scratch.txt", "content": "hi"}),
        project_id=project_id,
    )
    assert json.loads(write_raw)["written"] == "scratch.txt"

    read_raw = await orchestrator._dispatch_tool(
        "file_ops",
        json.dumps({"operation": "read", "path": "scratch.txt"}),
        project_id=project_id,
    )
    assert json.loads(read_raw)["content"] == "hi"


@pytest.mark.asyncio
async def test_dispatch_web_fetch_invalid_url(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    orchestrator, _project_id, _thread_id = orchestrator_with_project
    raw = await orchestrator._dispatch_tool(
        "web_fetch", json.dumps({"url": "ftp://example.com"})
    )
    assert "error" in json.loads(raw)


@pytest.mark.asyncio
async def test_dispatch_todo_write_success(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    orchestrator, project_id, thread_id = orchestrator_with_project
    todos = [{"id": "1", "content": "Do the thing", "status": "pending"}]
    raw = await orchestrator._dispatch_tool(
        "todo_write",
        json.dumps({"todos": todos}),
        project_id=project_id,
        thread_id=thread_id,
    )
    parsed = json.loads(raw)
    assert parsed["written"] is True
    assert parsed["todos"] == todos


@pytest.mark.asyncio
async def test_dispatch_todo_write_missing_thread(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    orchestrator, project_id, _thread_id = orchestrator_with_project
    raw = await orchestrator._dispatch_tool(
        "todo_write",
        json.dumps({"todos": []}),
        project_id=project_id,
    )
    assert "error" in json.loads(raw)


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_still_not_implemented(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    orchestrator, _project_id, _thread_id = orchestrator_with_project
    raw = await orchestrator._dispatch_tool("mystery_tool", "{}")
    parsed = json.loads(raw)
    assert parsed["status"] == "not_implemented"


# ---------------------------------------------------------------------------
# ask_user short-circuit + todo_write additive SSE event
# ---------------------------------------------------------------------------


class _FakeDelta:
    def __init__(self, *, content=None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChunk:
    def __init__(self, delta: _FakeDelta) -> None:
        self.choices = [MagicMock(delta=delta)]


class _FakeFunction:
    def __init__(self, name=None, arguments=None) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCallDelta:
    def __init__(self, index: int, *, id=None, function=None) -> None:
        self.index = index
        self.id = id
        self.function = function


async def _ask_user_stream(*_args, **kwargs):
    if kwargs.get("tools") is None:
        yield _FakeChunk(_FakeDelta(content="unused"))
        return
    yield _FakeChunk(
        _FakeDelta(
            tool_calls=[
                _FakeToolCallDelta(
                    0,
                    id="call_ask",
                    function=_FakeFunction(
                        name="ask_user",
                        arguments=json.dumps(
                            {
                                "question": "Which environment?",
                                "options": ["staging", "prod"],
                            }
                        ),
                    ),
                )
            ]
        )
    )


@pytest.mark.asyncio
async def test_ask_user_short_circuits_loop(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    from unittest.mock import patch

    orchestrator, project_id, thread_id = orchestrator_with_project

    with patch(
        "app.chat_orchestrator.litellm.acompletion", side_effect=_ask_user_stream
    ):
        events: list[dict] = []
        async for event in orchestrator.handle_message(
            project_id, thread_id, "Deploy the service"
        ):
            events.append(json.loads(event))

    types = [e["type"] for e in events]
    assert "ask_user" in types
    assert types[-1] == "done"
    # No tool_result/tool_call for ask_user itself, and loop stopped after one ask.
    ask_events = [e for e in events if e["type"] == "ask_user"]
    assert len(ask_events) == 1
    assert ask_events[0]["question"] == "Which environment?"
    assert ask_events[0]["options"] == ["staging", "prod"]
    assert not [e for e in events if e["type"] == "tool_result"]


async def _todo_write_then_text_stream(*_args, **kwargs):
    if kwargs.get("tools") is None:
        yield _FakeChunk(_FakeDelta(content="Done"))
        return
    yield _FakeChunk(
        _FakeDelta(
            tool_calls=[
                _FakeToolCallDelta(
                    0,
                    id="call_todo",
                    function=_FakeFunction(
                        name="todo_write",
                        arguments=json.dumps(
                            {
                                "todos": [
                                    {
                                        "id": "1",
                                        "content": "Step one",
                                        "status": "in_progress",
                                    }
                                ]
                            }
                        ),
                    ),
                )
            ]
        )
    )


@pytest.mark.asyncio
async def test_todo_write_emits_tool_result_and_todos_events(
    orchestrator_with_project: tuple[ChatOrchestrator, str, str],
) -> None:
    from unittest.mock import patch

    orchestrator, project_id, thread_id = orchestrator_with_project

    with patch(
        "app.chat_orchestrator.litellm.acompletion",
        side_effect=_todo_write_then_text_stream,
    ):
        events: list[dict] = []
        async for event in orchestrator.handle_message(
            project_id, thread_id, "Track my steps"
        ):
            events.append(json.loads(event))

    types = [e["type"] for e in events]
    assert "tool_result" in types
    assert "todos" in types
    todos_event = next(e for e in events if e["type"] == "todos")
    assert todos_event["todos"][0]["content"] == "Step one"
