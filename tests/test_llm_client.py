from __future__ import annotations

import httpx
import pytest

from app.llm_client import LLMClient, LLMError
from tests.fakes import FakeLlamaSwap

BASE_URL = "http://fake-llama-swap/v1/"


def make_client(fake: FakeLlamaSwap, timeout_s: float = 5.0) -> LLMClient:
    """Build an LLMClient wired to an in-process FakeLlamaSwap instead of a
    real socket. LLMClient's public constructor takes only (base_url,
    timeout_s) per the frozen interface, so tests swap the internal httpx
    client for one bound to httpx.ASGITransport after construction."""
    client = LLMClient(base_url=BASE_URL, timeout_s=timeout_s)
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url=BASE_URL, timeout=timeout_s
    )
    return client


async def collect(client: LLMClient, **kwargs) -> list:
    messages = [{"role": "user", "content": "hi"}]
    return [d async for d in client.chat("chat-default", messages, **kwargs)]


async def test_streamed_content_deltas():
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=["Hel", "lo", " world"], finish_reason="stop")
    client = make_client(fake)
    try:
        deltas = await collect(client)
        contents = [d.content for d in deltas if d.content is not None]
        assert contents == ["Hel", "lo", " world"]
        assert deltas[-1].finish_reason == "stop"
        assert fake.chat_requests[0]["model"] == "chat-default"
        assert fake.chat_requests[0]["stream"] is True
    finally:
        await client.close()


async def test_tool_call_delta_accumulation():
    fake = FakeLlamaSwap()
    fake.script_chat(
        tool_calls=[
            {
                "index": 0,
                "id": "call_1",
                "name": "web_search",
                "arg_chunks": ['{"query":', ' "weather"}'],
            }
        ],
        finish_reason="tool_calls",
    )
    client = make_client(fake)
    try:
        deltas = await collect(client)
        # Accumulate fragments by index, as the caller (chat orchestrator)
        # is documented to do — ToolCallDelta docstring in app/types.py.
        acc: dict[int, dict] = {}
        for d in deltas:
            for tc in d.tool_calls or []:
                slot = acc.setdefault(tc.index, {"id": None, "name": None, "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.name:
                    slot["name"] = tc.name
                if tc.arguments:
                    slot["arguments"] += tc.arguments
        assert acc == {
            0: {"id": "call_1", "name": "web_search", "arguments": '{"query": "weather"}'}
        }
        assert deltas[-1].finish_reason == "tool_calls"
    finally:
        await client.close()


async def test_usage_extraction_streamed():
    fake = FakeLlamaSwap()
    fake.script_chat(
        content_chunks=["ok"],
        usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    )
    client = make_client(fake)
    try:
        deltas = await collect(client)
        usages = [d.usage for d in deltas if d.usage is not None]
        assert len(usages) == 1
        assert usages[0].prompt_tokens == 12
        assert usages[0].completion_tokens == 3
        assert usages[0].total_tokens == 15
    finally:
        await client.close()


async def test_non_stream_chat_returns_single_delta():
    fake = FakeLlamaSwap()
    fake.script_chat(
        content_chunks=["full reply"], usage={"prompt_tokens": 1, "completion_tokens": 2}
    )
    client = make_client(fake)
    try:
        deltas = await collect(client, stream=False)
        assert len(deltas) == 1
        assert deltas[0].content == "full reply"
        assert deltas[0].finish_reason == "stop"
        assert deltas[0].usage.completion_tokens == 2
        assert fake.chat_requests[0]["stream"] is False
    finally:
        await client.close()


async def test_timeout_raises_llm_error():
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=["late"], delay_s=0.3)
    client = make_client(fake, timeout_s=0.05)
    try:
        with pytest.raises(LLMError) as exc_info:
            await collect(client)
        assert exc_info.value.kind == "timeout"
    finally:
        await client.close()


async def test_http_500_raises_llm_error():
    fake = FakeLlamaSwap()
    fake.script_chat(status_code=500, error_body="boom")
    client = make_client(fake)
    try:
        with pytest.raises(LLMError) as exc_info:
            await collect(client)
        assert exc_info.value.kind == "http_error"
        assert "500" in exc_info.value.detail
    finally:
        await client.close()


async def test_http_500_non_stream_raises_llm_error():
    fake = FakeLlamaSwap()
    fake.script_chat(status_code=500, error_body="boom")
    client = make_client(fake)
    try:
        with pytest.raises(LLMError) as exc_info:
            await collect(client, stream=False)
        assert exc_info.value.kind == "http_error"
    finally:
        await client.close()


async def test_embed_round_trip():
    fake = FakeLlamaSwap()
    fake.script_embeddings(vectors=[[0.1, 0.2], [0.3, 0.4]])
    client = make_client(fake)
    try:
        vectors = await client.embed("embed", ["hello", "world"])
        assert vectors == [[0.1, 0.2], [0.3, 0.4]]
        assert fake.embed_requests[0]["model"] == "embed"
        assert fake.embed_requests[0]["input"] == ["hello", "world"]
    finally:
        await client.close()


async def test_embed_http_error_raises_llm_error():
    fake = FakeLlamaSwap()
    fake.script_embeddings(vectors=[], status_code=503, error_body="unavailable")
    client = make_client(fake)
    try:
        with pytest.raises(LLMError) as exc_info:
            await client.embed("embed", ["hi"])
        assert exc_info.value.kind == "http_error"
    finally:
        await client.close()


async def test_models_list():
    fake = FakeLlamaSwap()
    fake.script_models(["chat-default", "coder", "dispatcher"])
    client = make_client(fake)
    try:
        names = await client.models()
        assert names == ["chat-default", "coder", "dispatcher"]
    finally:
        await client.close()


async def test_streaming_requests_usage_from_the_server():
    """OpenAI-compatible servers omit `usage` from streams unless asked.
    Without stream_options every live turn reported usage=null and the Debug
    view had no real token counts (PLAN.md §4.16 bans client estimates)."""
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=["ok"])
    client = make_client(fake)
    try:
        await collect(client)
        assert fake.chat_requests[0]["stream_options"] == {"include_usage": True}
    finally:
        await client.close()


async def test_non_stream_request_omits_stream_options():
    fake = FakeLlamaSwap()
    fake.script_chat(content_chunks=["ok"])
    client = make_client(fake)
    try:
        await collect(client, stream=False)
        assert "stream_options" not in fake.chat_requests[0]
    finally:
        await client.close()


async def test_llama_cpp_timings_reach_the_caller():
    """`timings` (tok/s) rides the same final chunk as `usage` — it is the
    only server-side source for the Debug view's tok/s readout."""
    fake = FakeLlamaSwap()
    fake.script_chat(
        content_chunks=["ok"],
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        timings={"predicted_per_second": 31.5},
    )
    client = make_client(fake)
    try:
        deltas = await collect(client)
        timings = [d.timings for d in deltas if d.timings is not None]
        assert timings == [{"predicted_per_second": 31.5}]
    finally:
        await client.close()
