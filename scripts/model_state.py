#!/usr/bin/env python3
# ruff: noqa: E501,E701,E702,E722,F401,F841
"""Compact GPU + llama-swap model state.

Wraps three ad-hoc checks that otherwise run separately:
  curl :8080/v1/models  +  nvidia-smi  +  ps aux | grep llama-server

Usage:
  python scripts/model_state.py
  python scripts/model_state.py --json
  python scripts/model_state.py --base-url http://127.0.0.1:8080/v1
  python scripts/model_state.py --qwen36-url http://127.0.0.1:5807/v1

Replaces ~3 manual calls with one compact table.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def fetch_models(base_url: str, timeout: float = 5.0) -> dict | None:
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"_error": str(e), "_url": url}


def nvidia_smi() -> list[dict] | dict:
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.free,memory.used,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=5)
    except FileNotFoundError:
        return {"_error": "nvidia-smi not found"}
    except subprocess.CalledProcessError as e:
        return {"_error": f"nvidia-smi failed: {e}"}
    except Exception as e:
        return {"_error": str(e)}
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            gpus.append({
                "index": parts[0],
                "name": parts[1],
                "mem_total_mib": int(parts[2]),
                "mem_free_mib": int(parts[3]),
                "mem_used_mib": int(parts[4]),
                "util_pct": parts[5],
                "temp_c": parts[6],
            })
        except Exception:
            gpus.append({"raw": line})
    return gpus


def llama_processes() -> list[dict]:
    try:
        out = subprocess.check_output(["ps", "aux"], text=True, timeout=5)
    except Exception as e:
        return [{"_error": str(e)}]
    rows = []
    for line in out.splitlines():
        if "llama-server" not in line and "llama-swap" not in line:
            continue
        rows.append({"ps": line.strip()})
    procs = []
    for r in rows:
        line = r["ps"]
        port = ""
        model = ""
        for token in line.split():
            if token.startswith("--port"):
                port = token
            if ".gguf" in token:
                model = token.split("/")[-1][:40]
        procs.append({"line": line, "port_hint": port, "model_hint": model})
    return procs


def resolve_base_url(cli: str | None) -> str:
    if cli:
        return cli
    try:
        from app.config import get_config  # type: ignore
        return get_config().llama_swap.base_url
    except Exception:
        return "http://127.0.0.1:8080/v1"


def _models_markdown(title: str, models_data: dict | None) -> list[str]:
    lines = [f"## {title}"]
    if isinstance(models_data, dict) and "_error" in models_data:
        lines.append(f"_Error fetching {models_data.get('_url','')}: {models_data['_error']}_")
    elif isinstance(models_data, dict) and "data" in models_data:
        lines.extend(["| model | status |", "|-------|--------|"])
        for model in sorted(models_data["data"], key=lambda item: item.get("id", "")):
            # llama-swap adds status.value; llama-server's native /v1/models
            # response only returns a model from a running server, so it is
            # loaded even though the native response has no status field.
            status = model.get("status", {}).get("value", "loaded")
            lines.append(f"| `{model.get('id', '?')}` | {status} |")
    else:
        lines.extend(["```json", json.dumps(models_data, indent=2), "```"])
    lines.append("")
    return lines


def fmt_table(models_data, gpus, procs, extra_models: list[tuple[str, dict | None]] | None = None) -> str:
    lines: list[str] = []
    lines.append("# Model State")
    lines.append("")

    lines.extend(_models_markdown("llama-swap /v1/models", models_data))
    for label, relay_models in extra_models or []:
        lines.extend(_models_markdown(f"{label} /v1/models", relay_models))

    lines.append("## nvidia-smi")
    if isinstance(gpus, dict) and "_error" in gpus:
        lines.append(f"_{gpus['_error']}_")
    elif isinstance(gpus, list) and gpus:
        lines.append("| idx | name | used / total (MiB) | free | util | temp |")
        lines.append("|-----|------|-------------------|------|------|------|")
        for g in gpus:
            if "raw" in g:
                lines.append(f"| ? | {g['raw']} | | | | |")
            else:
                lines.append(f"| {g['index']} | {g['name']} | {g['mem_used_mib']} / {g['mem_total_mib']} | {g['mem_free_mib']} | {g['util_pct']}% | {g['temp_c']}C |")
    else:
        lines.append("_No GPU data_")
    lines.append("")

    lines.append("## Processes (ps aux | grep llama)")
    if not procs:
        lines.append("_No llama-server / llama-swap processes found_")
    else:
        for p in procs:
            lines.append(f"- `{p['line'][:220]}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Compact GPU + llama-swap model state")
    ap.add_argument("--base-url", default=None, help="llama-swap base URL (default from config.yaml)")
    ap.add_argument(
        "--qwen36-url",
        default=None,
        help="Qwen3.6 worker/relay base URL, e.g. http://127.0.0.1:5807/v1 or :8082/v1",
    )
    ap.add_argument("--json", action="store_true", help="Emit raw JSON instead of markdown table")
    ap.add_argument("--out", type=Path, default=None, help="Write output to file instead of stdout")
    args = ap.parse_args()

    base_url = resolve_base_url(args.base_url)
    models_data = fetch_models(base_url)
    gpus = nvidia_smi()
    procs = llama_processes()
    qwen36_url = args.qwen36_url
    if qwen36_url is None and any("--port 5807" in process.get("line", "") for process in procs):
        # The relay deliberately rejects requests from ailab itself. Query its
        # local upstream when the isolated worker is running, but label the
        # result as the corresponding worker/relay route below.
        qwen36_url = "http://127.0.0.1:5807/v1"
    extra_models = []
    if qwen36_url:
        extra_models.append(("Qwen3.6 worker/relay", fetch_models(qwen36_url)))

    if args.json:
        payload = {
            "base_url": base_url,
            "models": models_data,
            "qwen36_url": qwen36_url,
            "qwen36_models": extra_models[0][1] if extra_models else None,
            "gpus": gpus,
            "processes": procs,
        }
        text = json.dumps(payload, indent=2)
    else:
        text = fmt_table(models_data, gpus, procs, extra_models)
        text += f"\n> base_url: `{base_url}`\n"
        if qwen36_url:
            text += f"> qwen36_url: `{qwen36_url}`\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
