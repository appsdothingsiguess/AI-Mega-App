"""Shared pytest fixtures (docs/FEATURES.md F12; PLAN.md §4.10).

`db_conn`     — a tmp-path SQLite connection with the Phase-1 schema applied
                (app/db.py open_db), closed on teardown.
`test_config` — a validated `Config` (app/config.py) with `llama_swap.base_url`
                pointed at an in-process fake llama-swap, and `db.path` at a
                tmp file. See NOTE below on which fake backs it.
`app_client`  — factory `(config: Config | None = None) -> TestClient` wired
                to `app.main.create_app`; defaults to `test_config`.

NOTE for the integrator: primary path is `FakeLlamaSwap().app` from
`tests/fakes/fake_llama_swap.py`. Import is lazy with a fallback to
`e2e/fake_backend.py` (same FakeLlamaSwap, plus GET /health for socket
probes) so this harness stays green if the fake module is absent in an
isolated checkout. `tests/test_debug_trace.py` defines its own `db_conn`
fixture that shadows this one — reconcile in the integrator, not here.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.config import Config, DbConfig, DefaultsConfig, LlamaSwapConfig, ModelEntry
from app.db import open_db
from app.main import create_app

TEST_MODEL = ModelEntry(
    name="chat-default",
    **{"class": "general"},
    ctx=4096,
    gpu=0,
    tool_call="native",
    max_tokens=1024,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _load_fake_asgi_app():
    """Prefer FakeLlamaSwap().app; fall back to e2e wrapper if absent."""
    try:
        from tests.fakes.fake_llama_swap import FakeLlamaSwap

        return FakeLlamaSwap().app
    except ImportError:
        from e2e.fake_backend import app as fake_app

        return fake_app


def _start_fake_llama_swap() -> tuple[str, Callable[[], None]]:
    """Start the fake ASGI app in a background uvicorn thread. Returns
    (base_url, stop_fn)."""
    fake_app = _load_fake_asgi_app()
    port = _free_port()
    config = uvicorn.Config(fake_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=5)

    return f"http://127.0.0.1:{port}/v1", stop


@pytest.fixture
def db_conn(tmp_path: Path) -> Iterator:
    conn = open_db(tmp_path / "test.db")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def test_config(tmp_path: Path) -> Iterator[Config]:
    base_url, stop = _start_fake_llama_swap()
    try:
        yield Config(
            llama_swap=LlamaSwapConfig(base_url=base_url),
            db=DbConfig(path=str(tmp_path / "test.db")),
            models=[TEST_MODEL],
            defaults=DefaultsConfig(
                chat_model="chat-default",
                utility_model="chat-default",
                title_model="chat-default",
            ),
        )
    finally:
        stop()


@pytest.fixture
def app_client(test_config: Config) -> Callable[[Config | None], TestClient]:
    def _make(config: Config | None = None) -> TestClient:
        return TestClient(create_app(config=config or test_config))

    return _make
