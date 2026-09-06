"""The only module that talks to llama-swap (PLAN.md §4.1 "Client";
docs/FEATURES.md F1). Speaks plain OpenAI chat-completions over httpx;
llama-swap owns all model load/swap — there is no scheduler code here.
Model names and base_url always arrive as parameters sourced from
config.yaml at runtime — never a hardcoded literal in this module.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.types import ChatDelta, ToolCallDelta, Usage


class LLMError(Exception):
    """Typed error for every llama-swap failure mode. A stream must never
    just go silent (old build Bug 2) — callers translate this into an SSE
    `error` event. `kind` is one of "timeout", "connection", "http_error",
    "stream_error"."""

    def __init__(self, kind: str, detail: str) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}")


class LLMClient:
    """Thin OpenAI chat-completions client over llama-swap. No retry/queue
    logic, no model-lifecycle awareness — llama-swap does every load/swap
    (PLAN.md §4.1)."""

    def __init__(self, base_url: str, timeout_s: float) -> None:
        # httpx merges a relative request path onto base_url by
        # concatenating raw paths, so base_url MUST end in "/" and request
        # paths must NOT start with "/" — otherwise the merge drops a slash
        # (e.g. "http://h/v1" + "/chat" -> "http://h/v1chat", silently
        # broken). Normalize here once so every call site below is safe.
        self._base_url = base_url if base_url.endswith("/") else base_url + "/"
        self._timeout_s = timeout_s
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout_s)

    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        thinking: bool | None = None,
        max_tokens: int | None = None,
        stream: bool = True,
    ) -> AsyncIterator[ChatDelta]:
        """Stream (or single-shot) a chat completion as ChatDelta fragments.

        Tool-call fragments are yielded as raw per-chunk deltas, each tagged
        with its `index` — accumulating them into complete calls is the
        caller's job (see ToolCallDelta docstring in app/types.py).

        `thinking` maps to llama.cpp's per-request reasoning control:
        True/False sends an explicit `reasoning` field ("on"/"off"); None
        leaves the server's own `--reasoning` flag in charge (PLAN.md §4.1,
        §"Two hard operational rules"). `max_tokens` passes straight
        through — thinking-capable models need a real budget or they return
        empty content, which reads as a model failure and is not one.
        """
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        if tools is not None:
            payload["tools"] = tools
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if thinking is not None:
            payload["reasoning"] = "on" if thinking else "off"
        if stream:
            # OpenAI-compatible servers (llama.cpp included) omit `usage`
            # from streamed responses unless this is asked for explicitly.
            # Without it every streamed turn reports usage=null and the
            # Debug view has no real token counts (PLAN.md §4.16 forbids
            # client-side estimates), so it is always on.
            payload["stream_options"] = {"include_usage": True}
            async for delta in self._chat_stream(payload):
                yield delta
        else:
            yield await self._chat_once(payload)

    async def _chat_once(self, payload: dict) -> ChatDelta:
        try:
            resp = await asyncio.wait_for(
                self._client.post("chat/completions", json=payload), timeout=self._timeout_s
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMError("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMError("connection", str(exc)) from exc
        if resp.status_code >= 400:
            raise LLMError("http_error", f"{resp.status_code}: {resp.text}")
        try:
            body = resp.json()
            choice = body["choices"][0]
            message = choice.get("message") or {}
            return ChatDelta(
                content=message.get("content"),
                reasoning_content=message.get("reasoning_content"),
                tool_calls=_parse_full_tool_calls(message.get("tool_calls")),
                finish_reason=choice.get("finish_reason"),
                usage=_parse_usage(body.get("usage")),
                timings=body.get("timings"),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("stream_error", f"malformed chat response: {exc}") from exc

    async def _chat_stream(self, payload: dict) -> AsyncIterator[ChatDelta]:
        stream_cm = self._client.stream("POST", "chat/completions", json=payload)
        try:
            resp = await asyncio.wait_for(stream_cm.__aenter__(), timeout=self._timeout_s)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMError("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMError("connection", str(exc)) from exc
        try:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise LLMError(
                    "http_error", f"{resp.status_code}: {body.decode(errors='replace')}"
                )
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise LLMError("stream_error", f"malformed SSE chunk: {exc}") from exc
                delta = _parse_stream_chunk(chunk)
                if delta is not None:
                    yield delta
        except httpx.TimeoutException as exc:
            raise LLMError("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMError("connection", str(exc)) from exc
        finally:
            await stream_cm.__aexit__(None, None, None)

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        try:
            resp = await asyncio.wait_for(
                self._client.post("embeddings", json={"model": model, "input": texts}),
                timeout=self._timeout_s,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMError("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMError("connection", str(exc)) from exc
        if resp.status_code >= 400:
            raise LLMError("http_error", f"{resp.status_code}: {resp.text}")
        try:
            body = resp.json()
            ranked = sorted(body["data"], key=lambda item: item.get("index", 0))
            return [item["embedding"] for item in ranked]
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("stream_error", f"malformed embeddings response: {exc}") from exc

    async def models(self) -> list[str]:
        try:
            resp = await asyncio.wait_for(self._client.get("models"), timeout=self._timeout_s)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMError("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMError("connection", str(exc)) from exc
        if resp.status_code >= 400:
            raise LLMError("http_error", f"{resp.status_code}: {resp.text}")
        try:
            body = resp.json()
            return [item["id"] for item in body["data"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("stream_error", f"malformed models response: {exc}") from exc

    async def model_status(self) -> dict[str, bool]:
        """Return {model_id: is_loaded} for every model llama-swap knows about.

        llama-swap's /v1/models includes per-model ``status.value``
        (``"loaded"``/``"unloaded"``). This is the data source for the
        model-picker roster's ``loaded`` flag (HANDOFF 2026-08-06 A).
        """
        try:
            resp = await asyncio.wait_for(self._client.get("models"), timeout=self._timeout_s)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMError("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMError("connection", str(exc)) from exc
        if resp.status_code >= 400:
            raise LLMError("http_error", f"{resp.status_code}: {resp.text}")
        try:
            body = resp.json()
            result: dict[str, bool] = {}
            for item in body.get("data", []):
                model_id = item.get("id")
                if not model_id:
                    continue
                status = item.get("status", {})
                is_loaded = status.get("value") == "loaded" if isinstance(status, dict) else False
                result[model_id] = is_loaded
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("stream_error", f"malformed models response: {exc}") from exc

    async def close(self) -> None:
        await self._client.aclose()


def _parse_usage(raw: dict | None) -> Usage | None:
    if not raw:
        return None
    return Usage(
        prompt_tokens=raw.get("prompt_tokens"),
        completion_tokens=raw.get("completion_tokens"),
        total_tokens=raw.get("total_tokens"),
    )


def _parse_full_tool_calls(raw: list | None) -> list[ToolCallDelta] | None:
    if not raw:
        return None
    result = []
    for i, tc in enumerate(raw):
        fn = tc.get("function") or {}
        result.append(
            ToolCallDelta(
                index=tc.get("index", i),
                id=tc.get("id"),
                name=fn.get("name"),
                arguments=fn.get("arguments"),
            )
        )
    return result


def _parse_stream_chunk(chunk: dict) -> ChatDelta | None:
    """Parse one llama-swap/OpenAI SSE chunk. Returns None for chunks that
    carry no new information (e.g. the role-only preamble chunk some
    servers send first) so callers never see a wholly-empty ChatDelta."""
    choices = chunk.get("choices") or []
    content = None
    reasoning_content = None
    tool_calls = None
    finish_reason = None
    if choices:
        choice = choices[0]
        delta = choice.get("delta") or {}
        content = delta.get("content")
        reasoning_content = delta.get("reasoning_content")
        raw_tool_calls = delta.get("tool_calls")
        if raw_tool_calls:
            tool_calls = [
                ToolCallDelta(
                    index=tc.get("index", 0),
                    id=tc.get("id"),
                    name=(tc.get("function") or {}).get("name"),
                    arguments=(tc.get("function") or {}).get("arguments"),
                )
                for tc in raw_tool_calls
            ]
        finish_reason = choice.get("finish_reason")
    usage = _parse_usage(chunk.get("usage"))
    # llama.cpp attaches `timings` to the same final chunk as `usage`
    # (stream_options.include_usage). It is the only source of real tok/s.
    timings = chunk.get("timings")
    if not isinstance(timings, dict):
        timings = None
    if (
        content is None
        and reasoning_content is None
        and tool_calls is None
        and finish_reason is None
        and usage is None
        and timings is None
    ):
        return None
    return ChatDelta(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        timings=timings,
    )
