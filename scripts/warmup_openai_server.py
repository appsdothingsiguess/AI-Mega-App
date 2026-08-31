#!/usr/bin/env python3
"""Wait for an OpenAI-compatible llama-server and run one warmup request."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


BASE_URL = os.environ.get("WARMUP_BASE_URL", "http://127.0.0.1:5807/v1")
TIMEOUT_S = int(os.environ.get("WARMUP_TIMEOUT_S", "300"))
PROMPT = os.environ.get(
    "WARMUP_PROMPT",
    "Return exactly one short sentence confirming the model is ready.",
)


def request(method: str, url: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read())


def main() -> int:
    deadline = time.monotonic() + TIMEOUT_S
    health_url = BASE_URL.removesuffix("/v1") + "/health"
    while time.monotonic() < deadline:
        try:
            request("GET", health_url)
            break
        except (OSError, urllib.error.HTTPError):
            time.sleep(2)
    else:
        print(f"warmup timed out waiting for {health_url}", file=sys.stderr)
        return 1

    payload = {
        "model": os.environ.get("WARMUP_MODEL", "qwen3.6-35b-ngram"),
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 16,
        "temperature": 0,
        "stream": False,
    }
    try:
        request("POST", BASE_URL + "/chat/completions", payload)
    except (OSError, urllib.error.HTTPError) as exc:
        print(f"warmup request failed: {exc}", file=sys.stderr)
        return 1
    print(f"warmed {BASE_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
