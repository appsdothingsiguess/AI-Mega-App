"""Tests for the three-layer smart router (PLAN.md §4.3, docs/FEATURES.md F5).

All tests run against FakeLlamaSwap — no real GPU or network needed.

Coverage:
  - override wins (classifier scripted but never called)
  - keyword rule wins over classifier
  - canned classifier JSON routes to correct model
  - classifier timeout → fallback_model, confidence=None, fallback_reason=timeout
  - low confidence → fallback_model, confidence preserved, fallback_reason=low_confidence
  - attachment forcing: image → vision_task / vision
  - word-boundary: "scode" does not fire a keyword rule for "write code"
  - span emitted with correct fields when trace_id provided
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import (
    Config,
    DbConfig,
    DefaultsConfig,
    LlamaSwapConfig,
    ModelEntry,
    RoutingClassifierConfig,
    RoutingConfig,
    RoutingIntents,
    RoutingRule,
)
from app.db import open_db
from app.debug import trace as trace_mod
from app.llm_client import LLMClient
from app.router import route
from tests.fakes.fake_llama_swap import FakeLlamaSwap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_URL = "http://fake-router/v1/"


def make_client(fake: FakeLlamaSwap, timeout_s: float = 5.0) -> LLMClient:
    """Build an LLMClient wired to an in-process FakeLlamaSwap."""
    client = LLMClient(base_url=BASE_URL, timeout_s=timeout_s)
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app),
        base_url=BASE_URL,
        timeout=timeout_s,
    )
    return client


def _clf_json(cls: str, confidence: float) -> str:
    return json.dumps({"class": cls, "confidence": confidence})


def make_config(
    *,
    rules: list[RoutingRule] | None = None,
    attachments: dict[str, str] | None = None,
    timeout_s: float = 2.0,
    confidence_threshold: float = 0.5,
    fallback_model: str = "chat-default",
) -> Config:
    """Minimal Config with a controlled routing section."""
    return Config(
        llama_swap=LlamaSwapConfig(base_url="http://fake/v1/"),
        db=DbConfig(path=":memory:"),
        models=[
            ModelEntry(
                name="chat-default",
                **{"class": "general"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/m/chat-default.gguf",
                quant="Q4_K_M",
            ),
            ModelEntry(
                name="coder",
                **{"class": "coding"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/m/coder.gguf",
                quant="Q4_K_M",
            ),
            ModelEntry(
                name="vision",
                **{"class": "vision"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/m/vision.gguf",
                quant="Q4_K_M",
            ),
            ModelEntry(
                name="reasoner",
                **{"class": "reasoning"},
                ctx=4096,
                gpu=0,
                tool_call="native",
                max_tokens=1024,
                file="/m/reasoner.gguf",
                quant="Q4_K_M",
            ),
        ],
        defaults=DefaultsConfig(
            chat_model="chat-default",
            utility_model="chat-default",
            title_model="chat-default",
        ),
        routing=RoutingConfig(
            rules=rules or [],
            attachments=attachments or {},
            intents=RoutingIntents(
                chat="chat-default",
                chit_chat="chat-default",
                code_task="coder",
                reasoning_task="reasoner",
                vision_task="vision",
                tool_call_needed="chat-default",
            ),
            classifier=RoutingClassifierConfig(
                model="classifier",
                timeout_s=timeout_s,
                confidence_threshold=confidence_threshold,
                fallback_model=fallback_model,
            ),
        ),
    )


def chat(model_override: str | None = None) -> dict[str, Any]:
    return {"model_override": model_override, "id": "chat-1"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_conn(tmp_path: Path):
    conn = open_db(tmp_path / "router_test.db")
    trace_mod.reset_connection(conn)
    try:
        yield conn
    finally:
        trace_mod.reset_connection(None)
        conn.close()


# ---------------------------------------------------------------------------
# Layer 1: manual override
# ---------------------------------------------------------------------------


