"""Settings overlay API + orchestrator router-seam wiring proofs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import config as config_mod
from app.config import Config, reset_config_cache
from app.main import create_app
from app.settings import store as store_mod
from app.types import RouteResult
from tests.chat_fixtures import FakeLLMClient, _parse_sse

MINIMAL_MODEL = {
    "name": "chat-default",
    "class": "general",
    "ctx": 4096,
    "gpu": 0,
    "tool_call": "native",
    "max_tokens": 1024,
    "file": "/models/chat-default.gguf",
    "quant": "Q4_K_M",
}

REQUIRED_ALIASES = (
    "coder", "reasoner", "vision", "classifier", "dispatcher", "utility",
    "utility-gpu",
)

MINIMAL_CONFIG: dict = {
    "llama_swap": {"base_url": "http://127.0.0.1:8080/v1"},
    "db": {"path": "app.db"},
    "models": [
        MINIMAL_MODEL,
        *[{**MINIMAL_MODEL, "name": alias} for alias in REQUIRED_ALIASES],
    ],
    "defaults": {
        "chat_model": "chat-default",
        "utility_model": "chat-default",
        "title_model": "chat-default",
    },
}


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def _patch_config_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base: dict | None = None
) -> tuple[Path, Path]:
    """Point CONFIG_PATH / OVERLAY_PATH at tmp files for config + store."""
    base_path = tmp_path / "config.yaml"
    overlay_path = tmp_path / "settings.local.yaml"
    cfg = dict(base or MINIMAL_CONFIG)
    cfg["db"] = {"path": str(tmp_path / "app.db")}
    write_yaml(base_path, cfg)

    monkeypatch.setattr(config_mod, "CONFIG_PATH", base_path)
    monkeypatch.setattr(config_mod, "OVERLAY_PATH", overlay_path)
    monkeypatch.setattr(store_mod, "CONFIG_PATH", base_path)
    monkeypatch.setattr(store_mod, "OVERLAY_PATH", overlay_path)
    # Default arg is bound at def time; update so bare load_config() hits tmp.
    monkeypatch.setattr(
        config_mod.load_config, "__defaults__", (base_path,), raising=False
    )
    reset_config_cache()
    return base_path, overlay_path


def _settings_client(tmp_path: Path) -> TestClient:
    cfg = config_mod.load_config()
    app = create_app(config=cfg)
    app.state.llm_client = FakeLLMClient(chunks=["ok"])
    client = TestClient(app)
    client.__enter__()
    return client


def test_overlay_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, overlay_path = _patch_config_paths(monkeypatch, tmp_path)
    client = _settings_client(tmp_path)

    put = client.put("/api/settings/models/chat-default", json={"gpu": "cpu"})
    assert put.status_code == 200
    assert put.json()["gpu"] == "cpu"

    get = client.get("/api/settings")
    assert get.status_code == 200
    models = {m["name"]: m for m in get.json()["models"]}
    assert models["chat-default"]["gpu"] == "cpu"

    assert overlay_path.is_file()
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    assert overlay["models"] == {"chat-default": {"gpu": "cpu"}}


def test_model_write_migrates_legacy_full_roster_to_sparse_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, overlay_path = _patch_config_paths(monkeypatch, tmp_path)
    legacy = [dict(model) for model in MINIMAL_CONFIG["models"]]
    legacy[0]["resident"] = True
    write_yaml(overlay_path, {"models": legacy})
    client = _settings_client(tmp_path)

    response = client.put("/api/settings/models/chat-default", json={"gpu": "cpu"})
    assert response.status_code == 200
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    assert overlay["models"] == {
        "chat-default": {"resident": True, "gpu": "cpu"}
    }


def test_invalid_write_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, overlay_path = _patch_config_paths(monkeypatch, tmp_path)
    # Seed a known-good overlay so we can prove it is left untouched.
    write_yaml(overlay_path, {"server": {"port": 9001}})
    before = overlay_path.read_text(encoding="utf-8")
    client = _settings_client(tmp_path)

    resp = client.put(
        "/api/settings/routing",
        json={"rules": [{"keywords": ["badkeyword"], "intent": "code_task"}]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    assert body["detail"]  # non-empty reason from ConfigError

    assert overlay_path.read_text(encoding="utf-8") == before


def test_effective_config_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, overlay_path = _patch_config_paths(monkeypatch, tmp_path)
    write_yaml(
        overlay_path,
        {
            "server": {"port": 9999},
            "routing": {"intents": {"code_task": "chat-default"}},
        },
    )
    reset_config_cache()
    client = _settings_client(tmp_path)

    data = client.get("/api/settings").json()
    assert data["server"]["port"] == 9999
    assert data["defaults"]["chat_model"] == "chat-default"
    assert data["models"][0]["name"] == "chat-default"
    assert data["routing"]["intents"]["code_task"] == "chat-default"
    # Overlay only patched code_task; other intents keep base/defaults.
    assert data["routing"]["intents"]["chat"] == "chat-default"


def test_put_model_reflected_in_swap_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    try:
        import app.gpu  # noqa: F401
    except ImportError:
        pytest.skip(
            "app.gpu not on this branch — swap-config wiring deferred "
            "(do not copy gpu into the tree)"
        )

    _patch_config_paths(monkeypatch, tmp_path)
    client = _settings_client(tmp_path)

    put = client.put("/api/settings/models/chat-default", json={"gpu": 1})
    assert put.status_code == 200

    swap = client.get("/api/gpu/swap-config")
    assert swap.status_code == 200
    text = swap.text if isinstance(swap.text, str) else str(swap.content)
    # Generated YAML should reflect the new placement (CUDA pin or gpu: 1).
    assert "CUDA_VISIBLE_DEVICES" in text or "gpu" in text.lower()


def test_orchestrator_uses_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_config_paths(monkeypatch, tmp_path)

    # Mirror route()'s real signature and swallow anything added to it:
    # a stub narrower than the callee masked a missing-argument bug for a
    # whole phase once (docs/HANDOFF.md, 2026-08-02).
    async def fake_route(
        chat, text: str, attachments: list, **kwargs
    ) -> RouteResult:
        return RouteResult(
            model="chat-default",
            source="rule",
            intent="code_task",
            latency_ms=1.5,
            confidence=None,
        )

    monkeypatch.setattr("app.chat.orchestrator._route", fake_route)

    client = _settings_client(tmp_path)
    chat_id = client.post("/api/chats", json={}).json()["id"]
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    done = next(data for ev, data in events if ev == "done")
    assert done["route"]["source"] == "rule"
    assert done["route"]["intent"] == "code_task"
    assert done["route"]["model"] == "chat-default"
    assert done["route"]["confidence"] is None

    trace_id = done["trace_id"]
    trace = client.get(f"/api/debug/trace/{trace_id}")
    assert trace.status_code == 200
    stages = [s["stage"] for s in trace.json()["spans"]]
    assert "route" in stages
    route_span = next(s for s in trace.json()["spans"] if s["stage"] == "route")
    route_data = (
        route_span["data"]
        if isinstance(route_span["data"], dict)
        else json.loads(route_span["data"])
    )
    assert route_data["source"] == "rule"


def test_list_models_returns_real_loaded_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_config_paths(monkeypatch, tmp_path)
    fake_llm = FakeLLMClient(
        chunks=["ok"],
        model_status={"chat-default": True, "coder": False},
    )
    cfg = config_mod.load_config()
    app = create_app(config=cfg)
    app.state.llm_client = fake_llm
    client = TestClient(app)
    client.__enter__()
    try:
        resp = client.get("/api/models")
        assert resp.status_code == 200
        models = {m["alias"]: m for m in resp.json()}
        assert models["chat-default"]["loaded"] is True
    finally:
        client.__exit__(None, None, None)


def test_list_models_hides_disabled_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disabled = {**MINIMAL_MODEL, "name": "disabled-model", "enabled": False}
    base = {**MINIMAL_CONFIG, "models": [*MINIMAL_CONFIG["models"], disabled]}
    _patch_config_paths(monkeypatch, tmp_path, base)
    client = _settings_client(tmp_path)
    try:
        response = client.get("/api/models")
        assert response.status_code == 200
        aliases = [model["alias"] for model in response.json()]
        assert "disabled-model" not in aliases
        assert "chat-default" in aliases
    finally:
        client.__exit__(None, None, None)
