#!/usr/bin/env python3
# ruff: noqa: E501,E701,E722,F841
"""One-shot incident snapshot: trace → time window → filtered logs + GPU + models.

Replaces the repeated 6-8 call sequence:
  trace_inspect.py → convert ms→journalctl timestamps → journalctl -u ai-mega-app
  → journalctl -u llama-swap → filter /api/gpu/inventory + /api/debug/summary-status noise
  → curl :8080/v1/models → nvidia-smi → ps aux

Usage:
  python scripts/incident_snapshot.py 4c8a26bf-778c-4eb1-96c7-1e1d3295e94e
  python scripts/incident_snapshot.py 4c8a26bf-778c-4eb1-96c7-1e1d3295e94e --pad 120 --with-trace
  python scripts/incident_snapshot.py --since "2026-08-22 23:33:00" --until "2026-08-22 23:35:00"
  python scripts/incident_snapshot.py 2026-08-22T23:33:11 --pad 60

Output: logs/incidents/<trace_id or timestamp>.md  (git-ignored via logs/)
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DB = ROOT / "data" / "app.db"
try:
    from app.config import get_config  # type: ignore
    _cfg = get_config()
    _p = Path(_cfg.db.path)
    CONFIG_DB = _p if _p.is_absolute() else ROOT / _p
    DEFAULT_SWAP_URL = _cfg.llama_swap.base_url
except Exception:
    CONFIG_DB = DEFAULT_DB
    DEFAULT_SWAP_URL = "http://127.0.0.1:8080/v1"

ROUTINE_SUBSTRINGS = [
    "/api/gpu/inventory",
    "/api/debug/summary-status",
    " 304 ",
    "GET /static/",
]

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

def is_trace_id(s: str) -> bool:
    return bool(UUID_RE.match(s.strip()))

def ms_to_journal_ts(ms: int) -> str:
    dt = datetime.datetime.fromtimestamp(ms / 1000).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def parse_timestamp_arg(s: str) -> int | None:
    s = s.strip()
    if s.isdigit() and len(s) >= 12:
        try:
            return int(s)
        except Exception:
            pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            dt = dt.astimezone()
            return int(dt.timestamp() * 1000)
        except Exception:
            continue
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return int(dt.timestamp() * 1000)
    except Exception:
        return None

def fetch_journal(unit: str, since: str, until: str, lines: int = 2000) -> tuple[str, str]:
    cmd = ["journalctl", "-u", unit, "--since", since, "--until", until, "--no-pager", "-n", str(lines)]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=10, stderr=subprocess.STDOUT)
        return out, ""
    except subprocess.CalledProcessError as e:
        return e.output or "", f"journalctl {unit} exit {e.returncode}"
    except FileNotFoundError:
        return "", "journalctl not found"
    except Exception as e:
        return "", str(e)

def filter_routine(text: str) -> tuple[str, int, int]:
    lines = text.splitlines()
    kept: list[str] = []
    dropped = 0
    for ln in lines:
        if any(pat in ln for pat in ROUTINE_SUBSTRINGS):
            dropped += 1
            continue
        kept.append(ln)
    return "\n".join(kept), len(kept), dropped

def fetch_models(base_url: str) -> dict:
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_error": str(e), "_url": url}

def nvidia_smi_block() -> str:
    cmd = ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free,memory.used,utilization.gpu,temperature.gpu", "--format=csv"]
    try:
        return subprocess.check_output(cmd, text=True, timeout=5)
    except Exception as e:
        return f"(nvidia-smi failed: {e})"

def ps_block() -> str:
    try:
        out = subprocess.check_output(["ps", "aux"], text=True, timeout=5)
        lines = [ln for ln in out.splitlines() if "llama" in ln.lower()]
        return "\n".join(lines) if lines else "(no llama processes)"
    except Exception as e:
        return f"(ps failed: {e})"

def load_trace_window(trace_id: str, db_path: Path) -> tuple[int, int, dict | None, list[dict]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
        if row is None:
            return 0, 0, None, []
        trace = dict(row)
        spans = conn.execute("SELECT * FROM spans WHERE trace_id=? ORDER BY started_at", (trace_id,)).fetchall()
        spans = [dict(s) for s in spans]
        if spans:
            starts = [s["started_at"] for s in spans if s.get("started_at")]
            ends = [s["ended_at"] for s in spans if s.get("ended_at")]
            lo = min(starts) if starts else trace.get("started_at", 0)
            hi = max(ends) if ends else lo
        else:
            lo = hi = trace.get("started_at", 0)
        return lo, hi, trace, spans
    finally:
        conn.close()

def main() -> int:
    ap = argparse.ArgumentParser(description="Incident snapshot: trace/time → filtered logs + GPU + models")
    ap.add_argument("target", nargs="?", help="trace_id (UUID) or timestamp (ISO like 2026-08-22T23:33:11 or epoch ms)")
    ap.add_argument("--trace-id", dest="trace_id_opt", default=None, help="Alias for target as trace_id")
    ap.add_argument("--since", dest="since_opt", default=None, help="Explicit --since timestamp (ISO), skips trace lookup")
    ap.add_argument("--until", dest="until_opt", default=None, help="Explicit --until timestamp (ISO)")
    ap.add_argument("--pad", type=int, default=90, help="Seconds of padding before/after window (default 90)")
    ap.add_argument("--lines", type=int, default=3000, help="journalctl -n limit per unit (default 3000)")
    ap.add_argument("--db", type=Path, default=None, help=f"DB path (default {CONFIG_DB})")
    ap.add_argument("--base-url", default=None, help="llama-swap base URL")
    ap.add_argument("--with-trace", action="store_true", help="Also embed the full trace_inspect markdown for the trace_id")
    ap.add_argument("--out", type=Path, default=None, help="Output file (default logs/incidents/<id>.md)")
    ap.add_argument("--stdout", action="store_true", help="Also print markdown to stdout")
    ap.add_argument("--no-filter", action="store_true", help="Do not filter routine polling lines")
    args = ap.parse_args()

    target = (args.trace_id_opt or args.target or "").strip()
    db_path = args.db or CONFIG_DB
    base_url = args.base_url or DEFAULT_SWAP_URL

    trace_id = None
    trace = None
    spans: list[dict] = []
    if args.since_opt or args.until_opt:
        since_ms = parse_timestamp_arg(args.since_opt) if args.since_opt else None
        until_ms = parse_timestamp_arg(args.until_opt) if args.until_opt else None
        if since_ms is None and args.since_opt:
            print(f"Cannot parse --since: {args.since_opt}", file=sys.stderr)
            return 2
        if until_ms is None and args.until_opt:
            print(f"Cannot parse --until: {args.until_opt}", file=sys.stderr)
            return 2
        if since_ms is not None and until_ms is None:
            until_ms = since_ms + args.pad * 1000
        if until_ms is not None and since_ms is None:
            since_ms = until_ms - args.pad * 1000
        lo, hi = since_ms or 0, until_ms or 0
        label = f"manual-{since_ms}-{until_ms}"
    elif target and is_trace_id(target):
        trace_id = target
        if not db_path.exists():
            print(f"DB not found: {db_path}", file=sys.stderr)
            return 1
        lo, hi, trace, spans = load_trace_window(trace_id, db_path)
        if trace is None:
            print(f"Trace not found: {trace_id}", file=sys.stderr)
            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                for r in conn.execute("SELECT trace_id FROM traces ORDER BY started_at DESC LIMIT 5"):
                    print(f"  {r['trace_id']}", file=sys.stderr)
                conn.close()
            except Exception:
                pass
            return 1
        label = trace_id
    elif target:
        ms = parse_timestamp_arg(target)
        if ms is None:
            print(f"Cannot parse target as trace_id or timestamp: {target}", file=sys.stderr)
            print("Expected UUID or ISO like 2026-08-22T23:33:11", file=sys.stderr)
            return 2
        lo, hi = ms - args.pad * 1000, ms + args.pad * 1000
        label = f"ts-{ms}"
    else:
        ap.error("target (trace_id or timestamp) or --since/--until is required")

    if trace_id is not None:
        lo_pad = lo - args.pad * 1000
        hi_pad = hi + args.pad * 1000
    else:
        lo_pad, hi_pad = lo, hi

    since_s = ms_to_journal_ts(lo_pad)
    until_s = ms_to_journal_ts(hi_pad)

    app_raw, app_err = fetch_journal("ai-mega-app", since_s, until_s, lines=args.lines)
    swap_raw, swap_err = fetch_journal("llama-swap", since_s, until_s, lines=args.lines)

    if args.no_filter:
        app_filt, app_kept, app_drop = app_raw, len(app_raw.splitlines()), 0
        swap_filt, swap_kept, swap_drop = swap_raw, len(swap_raw.splitlines()), 0
    else:
        app_filt, app_kept, app_drop = filter_routine(app_raw)
        swap_filt, swap_kept, swap_drop = filter_routine(swap_raw)

    models_data = fetch_models(base_url)
    nvsmi = nvidia_smi_block()
    psout = ps_block()

    now_s = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines: list[str] = []
    lines.append(f"# Incident snapshot — {label}")
    lines.append("")
    lines.append(f"> Generated {now_s} by `scripts/incident_snapshot.py`")
    lines.append(f"> Window: `{since_s}` → `{until_s}`  (pad {args.pad}s around trace)")
    if trace_id:
        lines.append(f"> trace_id: `{trace_id}`  chat_id: `{trace.get('chat_id') or '—'}`")
        if spans:
            wall = (hi - lo) if hi and lo else 0
            lines.append(f"> spans: {len(spans)}  wall {wall}ms  ({ms_to_journal_ts(lo)} → {ms_to_journal_ts(hi)})")
    lines.append(f"> DB: `{db_path}`  base_url: `{base_url}`")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- app logs: {app_kept} kept / {app_drop} filtered routine / {len(app_raw.splitlines())} total  (unit `ai-mega-app`)")
    lines.append(f"- swap logs: {swap_kept} kept / {swap_drop} filtered routine / {len(swap_raw.splitlines())} total  (unit `llama-swap`)")
    if app_err:
        lines.append(f"- app journal error: {app_err}")
    if swap_err:
        lines.append(f"- swap journal error: {swap_err}")
    lines.append(f"- filtered patterns: `{', '.join(ROUTINE_SUBSTRINGS)}`")
    lines.append("")

    lines.append("## llama-swap /v1/models")
    lines.append("")
    lines.append(f"> GET {base_url.rstrip('/')}/models")
    lines.append("")
    if isinstance(models_data, dict) and "_error" in models_data:
        lines.append(f"_Error: {models_data['_error']}_")
    else:
        lines.append("```json")
        lines.append(json.dumps(models_data, indent=2))
        lines.append("```")
    lines.append("")

    lines.append("## nvidia-smi")
    lines.append("")
    lines.append("```")
    lines.append(nvsmi.strip())
    lines.append("```")
    lines.append("")

    lines.append("## Processes (ps aux | grep llama)")
    lines.append("")
    lines.append("```")
    lines.append(psout.strip())
    lines.append("```")
    lines.append("")

    lines.append(f"## journalctl -u ai-mega-app --since \"{since_s}\" --until \"{until_s}\"  (filtered)")
    lines.append("")
    if app_filt.strip():
        lines.append("```")
        app_lines = app_filt.splitlines()
        if len(app_lines) > 4000:
            lines.append("\n".join(app_lines[:4000]))
            lines.append(f"... ({len(app_lines)-4000} more lines truncated)")
        else:
            lines.append(app_filt)
        lines.append("```")
    else:
        lines.append("_No non-routine lines in window (all filtered or no logs)._")
        if app_raw.strip() and not args.no_filter:
            lines.append("")
            lines.append(f"_Total raw lines was {len(app_raw.splitlines())}, all matched routine noise — re-run with --no-filter to see them._")
    lines.append("")

    lines.append(f"## journalctl -u llama-swap --since \"{since_s}\" --until \"{until_s}\"  (filtered)")
    lines.append("")
    if swap_filt.strip():
        lines.append("```")
        swap_lines = swap_filt.splitlines()
        if len(swap_lines) > 4000:
            lines.append("\n".join(swap_lines[:4000]))
            lines.append(f"... ({len(swap_lines)-4000} more lines truncated)")
        else:
            lines.append(swap_filt)
        lines.append("```")
    else:
        lines.append("_No non-routine lines in window._")
    lines.append("")

    if args.with_trace and trace_id:
        lines.append("---")
        lines.append("")
        lines.append(f"## Trace inspection (embedded from trace_inspect.py for {trace_id})")
        lines.append("")
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("trace_inspect", str(ROOT / "scripts" / "trace_inspect.py"))
            mod = importlib.util.module_from_spec(spec)  # type: ignore
            spec.loader.exec_module(mod)  # type: ignore
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            t = mod.load_trace(conn, trace_id)
            spans2 = mod.load_spans(conn, trace_id)
            chat = None
            msgs: list[dict] = []
            sibs = None
            if t and t.get("chat_id"):
                chat = mod.load_chat(conn, t["chat_id"])
                if chat:
                    msgs = mod.load_messages(conn, t["chat_id"])
                sibs = mod.load_all_traces_for_chat(conn, t["chat_id"])
            md = mod.build_markdown(t, spans2, chat, msgs, sibs, db_path)
            lines.append(md)
            conn.close()
        except Exception as e:
            lines.append(f"_Failed to embed trace: {e}_")

    lines.append("---")
    lines.append("")
    lines.append(f"> Reproduce: `python scripts/incident_snapshot.py {label} --pad {args.pad}`  |  `journalctl -u ai-mega-app --since \"{since_s}\" --until \"{until_s}\" --no-pager`")

    md = "\n".join(lines)

    out = args.out or (ROOT / "logs" / "incidents" / f"{label}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"Wrote {out}  (window {since_s} → {until_s}, app {app_kept}+{app_drop} swap {swap_kept}+{swap_drop})")
    if args.stdout:
        print("---")
        print(md)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
