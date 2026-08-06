"""Tests for app/warmup.py — resident-model warm-up (PLAN.md §4.1)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.config import Config, DbConfig, DefaultsConfig, LlamaSwapConfig, ModelEntry
from app.warmup import warmup_one, warmup_resident_models


class FakeLLM:
    """Stand-in for LLMClient with controllable chat/embed behavior."""

    def __init__(
        self,
        hang: bool = False,
        fail: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.hang = hang
        self.fail = fail
        self.delay = delay
        self.chat_calls: list[str] = []
        self.embed_calls: list[str] = []

    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        stream: bool = True,
        thinking: bool | None = None,
    ) -> AsyncIterator:
        from app.types import ChatDelta

        self.chat_calls.append(model)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.hang:
            await asyncio.sleep(999)
        if self.fail:
            raise RuntimeError("simulated failure")
        yield ChatDelta(content="ok", finish_reason="stop")

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(model)
        if self.hang:
            await asyncio.sleep(999)
        if self.fail:
            raise RuntimeError("simulated failure")
        return [[0.0] * 8]


def _make_config(*models: ModelEntry) -> Config:
    return Config(
        llama_swap=LlamaSwapConfig(base_url="http://127.0.0.1:8080/v1"),
        db=DbConfig(path=":memory:"),
        models=list(models),
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="chat-default",
            title_model="chat-default",
        ),
    )


def _model(
    name: str,
    *,
    resident: bool = True,
    gpu: int | str = 0,
    class_: str = "general",
    file: str = "/m.gguf",
) -> ModelEntry:
    return ModelEntry(
        name=name,
        **{"class": class_},
        ctx=4096,
        gpu=gpu,
        tool_call="none",
        max_tokens=64,
        file=file,
        quant="Q4_K_M",
        resident=resident,
        ttl_s=0 if resident else None,
    )


async def test_warmup_one_completes_normally():
    llm = FakeLLM()
    await warmup_one(llm, "chat-default", timeout_s=5)
    assert llm.chat_calls == ["chat-default"]


async def test_warmup_one_uses_embed_endpoint_for_embed_class():
    llm = FakeLLM()
    await warmup_one(llm, "embed", is_embed=True, timeout_s=5)
    assert llm.embed_calls == ["embed"]
    assert llm.chat_calls == []


async def test_warmup_one_logs_failure_but_does_not_raise():
    llm = FakeLLM(fail=True)
    await warmup_one(llm, "chat-default", timeout_s=5)
    assert llm.chat_calls == ["chat-default"]


async def test_warmup_one_times_out_on_hang():
    llm = FakeLLM(hang=True)
    await warmup_one(llm, "chat-default", timeout_s=0.1)
    assert llm.chat_calls == ["chat-default"]


async def test_warmup_resident_models_pings_all_in_parallel():
    llm = FakeLLM(delay=0.05)
    cfg = _make_config(
        _model("chat-default", resident=True, file="/chat.gguf"),
        _model("dispatcher", resident=True, gpu=1, file="/disp.gguf"),
        _model("coder", resident=False, file="/coder.gguf"),
    )
    await warmup_resident_models(llm, cfg, timeout_s=5)
    assert sorted(llm.chat_calls) == ["chat-default", "dispatcher"]


async def test_warmup_resident_models_dedupes_shared_file():
    """reasoner shares chat-default's GGUF — warmup should ping only once."""
    llm = FakeLLM()
    shared_file = "/shared.gguf"
    cfg = _make_config(
        _model("chat-default", resident=True, file=shared_file),
        _model("reasoner", resident=False, file=shared_file),
    )
    await warmup_resident_models(llm, cfg, timeout_s=5)
    assert llm.chat_calls == ["chat-default"]


async def test_warmup_resident_models_one_hang_does_not_block_others():
    """A hung model must not prevent others from completing (HANDOFF A2)."""
    call_log: list[str] = []

    class SelectiveHangLLM(FakeLLM):
        async def chat(self, model, messages, **kwargs):
            from app.types import ChatDelta
            call_log.append(model)
            if model == "hung-model":
                await asyncio.sleep(999)
            yield ChatDelta(content="ok", finish_reason="stop")

    llm = SelectiveHangLLM()
    cfg = _make_config(
        _model("hung-model", resident=True, file="/hung.gguf"),
        _model("fast-model", resident=True, gpu=1, file="/fast.gguf"),
    )
    await warmup_resident_models(llm, cfg, timeout_s=0.1)
    assert "fast-model" in call_log


async def test_warmup_skips_none_when_llm_is_none():
    cfg = _make_config(_model("chat-default"))
    await warmup_resident_models(None, cfg)


async def test_warmup_skips_none_when_config_is_none():
    llm = FakeLLM()
    await warmup_resident_models(llm, None)
    assert llm.chat_calls == []
