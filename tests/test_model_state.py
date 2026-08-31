from __future__ import annotations

from scripts import model_state


def test_fmt_table_can_include_qwen36_worker_or_relay() -> None:
    output = model_state.fmt_table(
        {"data": [{"id": "chat-default", "status": {"value": "loaded"}}]},
        [],
        [],
        [("Qwen3.6 worker/relay", {"data": [{"id": "qwen3.6-35b-ngram"}]})],
    )

    assert "## llama-swap /v1/models" in output
    assert "## Qwen3.6 worker/relay /v1/models" in output
    assert "`qwen3.6-35b-ngram`" in output
    assert "| `qwen3.6-35b-ngram` | loaded |" in output


def test_fetch_models_uses_the_supplied_endpoint(monkeypatch) -> None:
    seen: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"data": []}'

    def fake_urlopen(url, timeout):
        seen.append(url)
        assert timeout == 5.0
        return Response()

    monkeypatch.setattr(model_state.urllib.request, "urlopen", fake_urlopen)

    assert model_state.fetch_models("http://127.0.0.1:5807/v1") == {"data": []}
    assert seen == ["http://127.0.0.1:5807/v1/models"]
