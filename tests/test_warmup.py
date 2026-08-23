"""Tests for app/warmup.py — resident-model warm-up (PLAN.md §4.1)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.config import Config, DbConfig, DefaultsConfig, LlamaSwapConfig, ModelEntry
from app.warmup import (
    all_residents_loaded,
    resident_swap_names,
    server_identity,
    warmup_one,
    warmup_resident_models,
)


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
    assert llm.chat_calls == ["dispatcher"]


async def test_warmup_dedupes_only_matching_file_and_gpu_identity():
    llm = FakeLLM()
    shared_file = "/shared.gguf"
    cfg = _make_config(
        _model("utility-gpu", resident=True, gpu=1, file=shared_file),
        _model("utility-gpu-alias", resident=True, gpu=1, file=shared_file),
        _model("utility", resident=True, gpu="cpu", file=shared_file),
    )
    await warmup_resident_models(llm, cfg, timeout_s=5)
    assert llm.chat_calls == ["utility-gpu", "utility"]
    assert server_identity(cfg.models[0]) != server_identity(cfg.models[2])


async def test_generic_warmup_excludes_every_gpu0_alias_and_swap_member():
    llm = FakeLLM()
    cfg = _make_config(
        _model("chat-default", resident=True, gpu=0, file="/shared.gguf"),
        _model("reasoner", resident=True, gpu=0, file="/shared.gguf"),
        _model("coder", resident=True, gpu=0, file="/coder.gguf"),
        _model("dispatcher", resident=True, gpu=1, file="/dispatcher.gguf"),
        _model("utility", resident=True, gpu="cpu", file="/utility.gguf"),
        _model("disabled", resident=True, gpu=1, file="/disabled.gguf").model_copy(
            update={"enabled": False}
        ),
    )

    assert resident_swap_names(cfg) == ["dispatcher", "utility"]
    await warmup_resident_models(llm, cfg, timeout_s=5)
    assert llm.chat_calls == ["dispatcher", "utility"]


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


# ---------------------------------------------------------------------------
# all_residents_loaded — startup retry/backoff check (WS-B)
# ---------------------------------------------------------------------------


class StatusFakeLLM(FakeLLM):
    """Fake LLM that also supports model_status() for startup retry tests."""

    def __init__(self, status_map: dict[str, bool] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._status_map = status_map or {}

    async def model_status(self) -> dict[str, bool]:
        return dict(self._status_map)


async def test_all_residents_loaded_true_when_everyone_loaded():
    llm = StatusFakeLLM(status_map={"chat-default": False, "dispatcher": True})
    cfg = _make_config(
        _model("chat-default", resident=True, file="/chat.gguf"),
        _model("dispatcher", resident=True, gpu=1, file="/disp.gguf"),
    )
    assert await all_residents_loaded(llm, cfg) is True


async def test_all_residents_loaded_false_when_one_missing():
    llm = StatusFakeLLM(status_map={"dispatcher": True, "utility": False})
    cfg = _make_config(
        _model("chat-default", resident=True, file="/chat.gguf"),
        _model("dispatcher", resident=True, gpu=1, file="/disp.gguf"),
        _model("utility", resident=True, gpu="cpu", file="/utility.gguf"),
    )
    assert await all_residents_loaded(llm, cfg) is False


async def test_all_residents_loaded_false_when_llm_is_none():
    cfg = _make_config(_model("chat-default", resident=True))
    assert await all_residents_loaded(None, cfg) is False


async def test_all_residents_loaded_false_when_cfg_is_none():
    llm = StatusFakeLLM()
    assert await all_residents_loaded(llm, None) is False


async def test_all_residents_loaded_true_when_no_residents():
    """Vacuously true — no residents means nothing to load."""
    llm = StatusFakeLLM()
    cfg = _make_config(
        _model("chat-default", resident=False, file="/chat.gguf"),
    )
    assert await all_residents_loaded(llm, cfg) is True


async def test_warmup_loop_uses_filtered_source_for_periodic_sweeps(monkeypatch):
    from types import SimpleNamespace

    import app.main as main_mod

    cfg = _make_config(
        _model("chat-default", resident=True, gpu=0, file="/chat.gguf"),
        _model("dispatcher", resident=True, gpu=1, file="/disp.gguf"),
    )
    app = SimpleNamespace(state=SimpleNamespace(llm_client=object(), config=cfg))
    skips: list[set[str]] = []
    intervals: list[float] = []

    async def fake_loaded(llm, config):
        return {"dispatcher"}

    async def fake_warm(llm, config, *, skip):
        assert config is cfg
        skips.append(skip)

    async def fake_all_loaded(llm, config):
        return True

    async def fake_sleep(interval):
        intervals.append(interval)
        if len(intervals) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(main_mod, "loaded_resident_names", fake_loaded)
    monkeypatch.setattr(main_mod, "warmup_resident_models", fake_warm)
    monkeypatch.setattr(main_mod, "all_residents_loaded", fake_all_loaded)
    monkeypatch.setattr(main_mod.asyncio, "sleep", fake_sleep)

    try:
        await main_mod._warmup_loop(app)
    except asyncio.CancelledError:
        pass

    assert skips == [{"dispatcher"}, set()]
    assert intervals == [main_mod._WARMUP_INTERVAL_S, main_mod._WARMUP_INTERVAL_S]


async def test_all_residents_loaded_false_on_model_status_exception():
    class FailingStatusLLM(StatusFakeLLM):
        async def model_status(self) -> dict[str, bool]:
            raise RuntimeError("llama-swap unreachable")

    llm = FailingStatusLLM(status_map={})
    cfg = _make_config(
        _model("dispatcher", resident=True, gpu=1, file="/dispatcher.gguf")
    )
    assert await all_residents_loaded(llm, cfg) is False


async def test_warmup_loop_retries_in_startup_phase():
    """The _warmup_loop in main.py retries with _STARTUP_BACKOFF_S until
    all residents report loaded, then settles into _WARMUP_INTERVAL_S.
    We test the retry semantics by driving the loop components directly."""
    # Simulate a resident model that becomes loaded after the first sweep.
    call_count = 0

    class RetryLLM(StatusFakeLLM):
        async def model_status(self) -> dict[str, bool]:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return {"dispatcher": True}
            return {"dispatcher": False}

        async def chat(self, model, messages, **kwargs):
            from app.types import ChatDelta
            self.chat_calls.append(model)
            yield ChatDelta(content="ok", finish_reason="stop")

    llm = RetryLLM()
    cfg = _make_config(_model("dispatcher", resident=True, gpu=1, file="/disp.gguf"))

    # Phase 1: sweep then check — not loaded yet.
    await warmup_resident_models(llm, cfg)
    assert await all_residents_loaded(llm, cfg) is False
    assert call_count == 1

    # Phase 2: sweep again, now resident loaded.
    await warmup_resident_models(llm, cfg)
    assert await all_residents_loaded(llm, cfg) is True
    assert call_count == 2
