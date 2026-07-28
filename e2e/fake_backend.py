"""Socket-bound e2e/dev wrapper around FakeLlamaSwap (docs/FEATURES.md F12).

`tests/fakes/fake_llama_swap.py` is the single OpenAI-compatible fake
implementation (class API, `.app` ASGI). This module exposes a module-level
`app` for uvicorn (`e2e.fake_backend:app`) used by `scripts/dev.sh` and
Playwright global-setup, and adds GET /health so process probes can wait
for the fake to come up. It is not a second protocol implementation.
"""

from __future__ import annotations

from tests.fakes.fake_llama_swap import FakeLlamaSwap

_fake = FakeLlamaSwap()
app = _fake.app


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
