"""Tests for inventory parser and the /api/gpu/apply endpoint.

Inventory: pure parse_nvidia_smi_csv tested with canned CSV text.
Apply: FastAPI TestClient with monkeypatched _poll_health so no real
       llama-swap or live filesystem paths are needed.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import (
    Config,
    DefaultsConfig,
    GpuConfig,
    LlamaSwapConfig,
    ModelEntry,
)
from app.gpu.api import router
from app.gpu.inventory import GPUInfo, parse_nvidia_smi_csv
from app.gpu.swapgen import generate

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TEST_MODEL = ModelEntry(
    name="chat-default",
    **{"class": "general"},
    ctx=4096,
    gpu=0,
    tool_call="native",
    max_tokens=1024,
    file="/models/chat.gguf",
    quant="Q4_K_M",
    resident=True,
    ttl_s=0,
)


def _make_config(swap_yaml_path: str, base_url: str = "http://127.0.0.1:9999/v1") -> Config:
    return Config(
        llama_swap=LlamaSwapConfig(base_url=base_url),
        models=[_TEST_MODEL],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="chat-default",
            title_model="chat-default",
        ),
        gpu=GpuConfig(swap_yaml_path=swap_yaml_path),
    )


def _make_app(config: Config) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.config = config
    return app


# ---------------------------------------------------------------------------
# CSV parser tests
# ---------------------------------------------------------------------------

_TWO_GPU_CSV = """\
0, NVIDIA GeForce RTX 3090, 24576, 2668
1, NVIDIA GeForce RTX 3070, 8192, 7104
"""


def test_parse_two_gpus():
    gpus = parse_nvidia_smi_csv(_TWO_GPU_CSV)
    assert len(gpus) == 2

    g0 = gpus[0]
    assert g0.index == 0
    assert g0.name == "NVIDIA GeForce RTX 3090"
    assert g0.mem_total_mb == 24576
    assert g0.mem_free_mb == 2668

    g1 = gpus[1]
    assert g1.index == 1
    assert g1.name == "NVIDIA GeForce RTX 3070"
    assert g1.mem_total_mb == 8192
    assert g1.mem_free_mb == 7104


def test_parse_empty_text():
    assert parse_nvidia_smi_csv("") == []


def test_parse_skips_malformed_lines():
    text = "not,enough\n0, RTX 3090, 24576, 2668\n"
    gpus = parse_nvidia_smi_csv(text)
    assert len(gpus) == 1
    assert gpus[0].index == 0


def test_parse_returns_gpuinfo_dataclass():
    gpus = parse_nvidia_smi_csv("0, A100, 40960, 38912\n")
    assert isinstance(gpus[0], GPUInfo)


# ---------------------------------------------------------------------------
# /api/gpu/apply endpoint tests
# ---------------------------------------------------------------------------

def test_apply_writes_file_and_returns_ok(tmp_path: Path, monkeypatch) -> None:
    """POST /apply writes generated YAML to swap_yaml_path on health OK."""
    import app.gpu.api as gpu_api

    monkeypatch.setattr(gpu_api, "_poll_health", AsyncMock(return_value=True))

    swap_path = tmp_path / "llama-swap.yaml"
    cfg = _make_config(str(swap_path))
    client = TestClient(_make_app(cfg))

    resp = client.post("/api/gpu/apply")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert swap_path.exists()
    assert swap_path.read_text(encoding="utf-8") == generate(cfg)


def test_apply_rewarms_resident_models_after_reload(tmp_path: Path, monkeypatch) -> None:
    """Regression: llama-swap's -watch-config reload (triggered by /apply
    writing a new YAML) kills every running llama-server process, not just
    the swapping GPU0 slot. Its own /health only reports proxy liveness, so
    the poll above returning OK does not mean any model is warm. /apply must
    re-warm resident models itself before replying, or the classifier (and
    every other resident model) sits cold until something else hits it."""
    import app.gpu.api as gpu_api

    monkeypatch.setattr(gpu_api, "_poll_health", AsyncMock(return_value=True))

    swap_path = tmp_path / "llama-swap.yaml"
    cfg = _make_config(str(swap_path))
    app = _make_app(cfg)

    warmed: list = []

    async def fake_warmup(llm, config) -> None:
        warmed.append((llm, config))

    monkeypatch.setattr(gpu_api, "warmup_resident_models", fake_warmup)
    monkeypatch.setattr(gpu_api, "all_residents_loaded", AsyncMock(return_value=True))

    sentinel_llm = object()
    app.state.llm_client = sentinel_llm
    client = TestClient(app)

    resp = client.post("/api/gpu/apply")

    assert resp.status_code == 200
    assert warmed == [(sentinel_llm, cfg)]


def test_apply_returns_failure_when_resident_warmup_never_converges(
    tmp_path: Path, monkeypatch
) -> None:
    """Proxy health is insufficient when a resident server stays unloaded."""
    import app.gpu.api as gpu_api

    monkeypatch.setattr(gpu_api, "_poll_health", AsyncMock(return_value=True))
    monkeypatch.setattr(gpu_api, "all_residents_loaded", AsyncMock(return_value=False))
    monkeypatch.setattr(gpu_api.asyncio, "sleep", AsyncMock())

    swap_path = tmp_path / "llama-swap.yaml"
    dispatcher = ModelEntry(
        name="dispatcher",
        **{"class": "general"},
        ctx=4096,
        gpu=1,
        tool_call="native",
        max_tokens=1024,
        file="/models/dispatcher.gguf",
        quant="Q4_K_M",
        resident=True,
        ttl_s=0,
    )
    cfg = _make_config(str(swap_path)).model_copy(
        update={"models": [_TEST_MODEL, dispatcher]}
    )
    app = _make_app(cfg)
    app.state.llm_client = object()
    warmup = AsyncMock()
    monkeypatch.setattr(gpu_api, "warmup_resident_models", warmup)

    response = TestClient(app).post("/api/gpu/apply")

    assert response.status_code == 503
    assert "resident models remain unloaded" in response.json()["detail"]
    assert warmup.await_count == 3


def test_apply_disabled_gpu_returns_400(tmp_path: Path) -> None:
    """POST /apply returns 400 when gpu.enabled is false."""
    swap_path = tmp_path / "llama-swap.yaml"
    cfg = _make_config(str(swap_path))
    cfg = cfg.model_copy(update={"gpu": GpuConfig(enabled=False, swap_yaml_path=str(swap_path))})
    client = TestClient(_make_app(cfg))

    resp = client.post("/api/gpu/apply")

    assert resp.status_code == 400
    assert "gpu.enabled" in resp.json()["detail"]


def test_apply_rollback_on_poll_failure(tmp_path: Path, monkeypatch) -> None:
    """POST /apply restores .bak and returns 503 when health poll times out."""
    import app.gpu.api as gpu_api

    monkeypatch.setattr(gpu_api, "_poll_health", AsyncMock(return_value=False))

    swap_path = tmp_path / "llama-swap.yaml"
    original_content = "# original\n"
    swap_path.write_text(original_content, encoding="utf-8")

    cfg = _make_config(str(swap_path))
    client = TestClient(_make_app(cfg))

    resp = client.post("/api/gpu/apply")

    assert resp.status_code == 503
    # Original content restored from .bak
    assert swap_path.read_text(encoding="utf-8") == original_content


def test_apply_no_existing_file_still_works(tmp_path: Path, monkeypatch) -> None:
    """POST /apply succeeds even when no existing swap_yaml_path exists."""
    import app.gpu.api as gpu_api

    monkeypatch.setattr(gpu_api, "_poll_health", AsyncMock(return_value=True))

    swap_path = tmp_path / "subdir" / "llama-swap.yaml"
    cfg = _make_config(str(swap_path))
    client = TestClient(_make_app(cfg))

    resp = client.post("/api/gpu/apply")

    assert resp.status_code == 200
    assert swap_path.exists()


# ---------------------------------------------------------------------------
# /api/gpu/swap-config endpoint test
# ---------------------------------------------------------------------------

def test_swap_config_returns_yaml_text(monkeypatch) -> None:
    """GET /swap-config returns the generated YAML as text/plain."""
    cfg = _make_config("/tmp/fake.yaml")
    client = TestClient(_make_app(cfg))

    resp = client.get("/api/gpu/swap-config")

    assert resp.status_code == 200
    assert "# generated" in resp.text
    assert "models:" in resp.text
    assert "groups:" in resp.text


# ---------------------------------------------------------------------------
# /api/gpu/inventory endpoint test (no nvidia-smi in CI → empty list)
# ---------------------------------------------------------------------------

def test_inventory_returns_list(monkeypatch) -> None:
    """GET /inventory returns a list (empty on CI where nvidia-smi is absent)."""
    cfg = _make_config("/tmp/fake.yaml")
    client = TestClient(_make_app(cfg))

    resp = client.get("/api/gpu/inventory")

    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
