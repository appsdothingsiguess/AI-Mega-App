#!/usr/bin/env python3
"""Load one llama-swap model and print its live configuration.

Usage:
  python scripts/load_model_check.py
  python scripts/load_model_check.py chat-default
  python scripts/load_model_check.py reasoner --timeout 180

The request is intentionally tiny: llama-swap loads the selected alias lazily,
then the script finds the matching live llama-server process and prints its
parsed command-line flags. No generated config file is edited.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_config  # noqa: E402
from scripts.model_state import fetch_models, llama_processes, resolve_base_url  # noqa: E402


def request_model(base_url: str, alias: str, timeout: float) -> tuple[float, dict]:
    payload = {
        "model": alias,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"request for {alias!r} failed: {exc}") from exc
    return time.perf_counter() - started, body


def model_file(alias: str) -> str:
    for entry in get_config().models:
        if entry.name == alias:
            return Path(entry.file).name
    raise RuntimeError(f"unknown or disabled model alias: {alias}")


def select_alias(models, input_fn=input) -> str | None:
    """Let a terminal user choose an enabled model alias by number."""
    choices = [model for model in models if model.enabled]
    if not choices:
        raise RuntimeError("no enabled model aliases are configured")

    print("Select a model to load (q to cancel):")
    for index, model in enumerate(choices, start=1):
        print(f"  {index}. {model.name:<16} {model.class_:<10} ctx={model.ctx:,}")

    while True:
        try:
            answer = input_fn("Model number: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if answer in {"q", "quit", "exit"}:
            return None
        try:
            choice = int(answer)
        except ValueError:
            print(f"Enter a number from 1 to {len(choices)}, or q to cancel.")
            continue
        if 1 <= choice <= len(choices):
            return choices[choice - 1].name
        print(f"Enter a number from 1 to {len(choices)}, or q to cancel.")


def find_process(alias_file: str, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for process in llama_processes():
            if alias_file in process.get("line", ""):
                return process
        time.sleep(1)
    return None


def parsed_flags(command: str) -> list[tuple[str, str]]:
    tokens = shlex.split(command)
    flags: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            value = ""
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                value = tokens[index + 1]
                index += 1
            flags.append((token, value))
        index += 1
    return flags


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("alias", nargs="?", help="configured llama-swap alias to load")
    parser.add_argument("--base-url", default=None, help="llama-swap /v1 URL")
    parser.add_argument("--timeout", type=float, default=180, help="request/process timeout in seconds")
    args = parser.parse_args()

    alias = args.alias or select_alias(get_config().models)
    if alias is None:
        print("Cancelled.")
        return 0

    base_url = resolve_base_url(args.base_url)
    expected_file = model_file(alias)
    print(f"Loading {alias} via {base_url} ...", flush=True)
    wall, response = request_model(base_url, alias, args.timeout)
    process = find_process(expected_file, min(args.timeout, 30))

    print()
    print(f"Model:             {alias}")
    print(f"Request:           {wall:.2f}s")
    print(f"Response:          {response.get('model', 'ok')}")
    print(f"Expected GGUF:     {expected_file}")
    live_models = fetch_models(base_url)
    live_status = "unknown"
    if isinstance(live_models, dict):
        for model in live_models.get("data", []):
            if model.get("id") == args.alias:
                live_status = model.get("status", {}).get("value", "unknown")
                break
    print(f"Live status:       {live_status}")
    if process is None:
        print("Live process:      NOT FOUND")
        return 1

    print(f"Live process:      {process.get('line', '')}")
    print()
    print("Live llama-server flags:")
    for flag, value in parsed_flags(process["line"]):
        print(f"  {flag:<24} {value}" if value else f"  {flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
