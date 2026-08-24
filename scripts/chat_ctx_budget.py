#!/usr/bin/env python3
# ruff: noqa: E501,E701,E722,F841,I001,UP031,F541
"""Preview a chat's context fit: summary coverage + assemble_context, mirroring turn.py.

Reuses the CURRENT real logic (app/chat/context.py assemble_context +
app/background/summary_coverage.py trusted_covered_count, as called in
app/chat/turn.py:_request_completion) so the preview never drifts from what a
turn actually does. Current semantics are lossless: NO silent truncation —
assemble_context builds SYSTEM + [SUMMARY] + all raw messages after the trusted
covered prefix and REFUSES (messages=None) if the estimate exceeds the budget.

Answers "will this turn fit / get refused" *before* sending a test message.

Usage:
  python scripts/chat_ctx_budget.py <chat_id>
  python scripts/chat_ctx_budget.py <chat_id> --model coder-small
  python scripts/chat_ctx_budget.py <chat_id> --model chat-default --next-msg "hello world"
  python scripts/chat_ctx_budget.py --list   # show recent chats
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
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
except Exception:
    CONFIG_DB = DEFAULT_DB


def resolve_db(cli: Path | None) -> Path:
    return cli or CONFIG_DB


def _load_module(name: str, rel_path: str):
    import sys as _sys
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel_path))
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[name] = mod  # register before exec so @dataclass/_type checks work
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def list_chats(conn: sqlite3.Connection, limit: int = 15) -> None:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, updated_at, model_override, summary FROM chats ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    print(f"{'chat_id':<36} {'title':<30} {'override':<14} summary?")
    print("-" * 100)
    for r in rows:
        summ = "yes" if r["summary"] else "no"
        title = (r["title"] or "")[:30]
        print(f"{r['id']:<36} {title:<30} {str(r['model_override'] or ''):<14} {summ}")


def budget_preview(chat_id: str, model_name: str | None, next_msg: str | None, db_path: Path) -> int:
    # Load the CURRENT real modules (works under system python3 which lacks httpx,
    # because context.py / summary_coverage.py / summary_policy.py have no httpx dep).
    # Load CURRENT real logic. Prefer direct file loads (context.py /
    # summary_coverage.py / history.py have NO httpx dep and avoid package
    # __init__ chains), so this works under system python3. Fall back to
    # package imports when they succeed (venv).
    context_mod = _load_module("context_mod", "app/chat/context.py")
    coverage_mod = _load_module("coverage_mod", "app/background/summary_coverage.py")
    history_mod = _load_module("history_mod", "app/chat/history.py")
    assemble_context = context_mod.assemble_context
    estimate_prompt_tokens = context_mod.estimate_prompt_tokens
    SYSTEM_MESSAGE = context_mod.SYSTEM_MESSAGE
    trusted_covered_count = coverage_mod.trusted_covered_count
    list_messages = history_mod.list_messages
    try:
        from app.config import get_config  # type: ignore
        get_config_fn = get_config
    except Exception as e:
        print(f"Import app.config failed: {e}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    chat_row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if chat_row is None:
        print(f"Chat not found: {chat_id}", file=sys.stderr)
        print("Try --list to see recent chats.", file=sys.stderr)
        conn.close()
        return 1
    chat = dict(chat_row)

    cfg = get_config_fn()
    if model_name is None:
        model_name = chat.get("model_override") or cfg.defaults.chat_model
    entry = next((m for m in cfg.models if m.name == model_name), None)
    if entry is None:
        print(f"Model not found in roster: {model_name}", file=sys.stderr)
        print(f"Known: {', '.join(m.name for m in cfg.models)}", file=sys.stderr)
        conn.close()
        return 1

    ctx = entry.ctx
    max_tokens = entry.max_tokens
    budget_tokens = max(ctx - max_tokens, 0)

    # Mirror turn.py:_request_completion exactly.
    raw = list_messages(conn, chat_id)
    summary = chat.get("summary")
    covered = trusted_covered_count(conn, chat_id, raw, summary)
    assembled = assemble_context(raw, summary, covered, ctx, max_tokens)

    # If --next-msg, simulate appending it (raw turns are user-role, not counted
    # as covered) by rebuilding with an extra message and re-deriving coverage.
    # Coverage is by count+fingerprint, so appending does NOT invalidate it.
    eff_covered = covered
    eff_assembly = assembled
    if next_msg is not None:
        eff_raw = raw + [{"role": "user", "content": next_msg}]
        eff_covered = trusted_covered_count(conn, chat_id, eff_raw, summary)
        eff_assembly = assemble_context(eff_raw, summary, eff_covered, ctx, max_tokens)

    fits = eff_assembly.fits
    used = eff_assembly.estimated_prompt_tokens
    pct = (used / budget_tokens * 100) if budget_tokens > 0 else float("inf")

    # Per-message table with coverage markers.
    messages = eff_assembly.messages
    raw_for_display = eff_raw if next_msg is not None else raw
    covered_disp = eff_covered

    print(f"# Chat ctx budget — {chat_id}")
    print(f"Chat title: {chat.get('title') or '—'}  |  model_override: {chat.get('model_override') or '—'}")
    print(f"Summary: {'present (%d chars)' % (len(summary) if summary else 0) if summary else 'none'}")
    print(f"Trusted covered messages: {covered_disp if covered_disp is not None else 'NONE (no trusted coverage → full raw history)'}")
    print(f"Model: {model_name}  ctx={ctx}  max_tokens={max_tokens}  budget_tokens={budget_tokens} (ctx-max_tokens)")
    print(f"Chars/token est: 3.5 (conservative, context.py)")
    print("")
    print(f"Messages in DB: {len(raw)}" + (f"  |  +1 simulated --next-msg" if next_msg is not None else ""))
    if covered_disp is not None:
        print(f"Messages rendered via summary: {covered_disp}  |  raw tail after coverage: {len(raw_for_display) - covered_disp}")
    print(f"Assembled wire messages (SYSTEM + [SUMMARY] + raw tail): {len(messages) if messages else 0}")
    print(f"Estimated prompt tokens: {used:.0f}  /  budget {budget_tokens}  ({pct if pct == float('inf') else f'{pct:.1f}%'})")
    if fits:
        print("✓ FITS — the turn would stream (lossless, nothing dropped)")
    else:
        print("⛔ WOULD BE REFUSED — assemble_context returns no messages (estimate > budget). No silent truncation; the turn errors as context-too-large.")
    print("")

    # Message table
    print("| # | role | chars | est tok | coverage | preview |")
    print("|---|------|-------|---------|----------|---------|")
    display = raw_for_display
    for i, m in enumerate(display):
        chars = len(m.get("content", ""))
        tok = chars / 3.5
        role = m.get("role", "?")
        cov = "summarized" if (covered_disp is not None and i < covered_disp) else "raw"
        preview = (m.get("content", "")[:55].replace("\n", " ").replace("|", "/")) + ("…" if chars > 55 else "")
        print(f"| {i} | {role} | {chars} | {tok:.0f} | {cov} | {preview} |")
    print("")

    if summary:
        print(f"Summary text ({len(summary)} chars):")
        print(summary[:800] + (" …" if len(summary) > 800 else ""))
        print("")
    print(f"DB: {db_path}  |  Tip: re-run with --model <other> to compare across ctx sizes (e.g. coder-small vs chat-default)")

    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Preview a chat's context fit (lossless assemble_context, mirrors turn.py)")
    ap.add_argument("chat_id", nargs="?", help="Chat ID")
    ap.add_argument("--model", dest="model_name", default=None, help="Target model alias (default: chat's model_override or defaults.chat_model)")
    ap.add_argument("--next-msg", default=None, help="Simulate appending this user message before budgeting")
    ap.add_argument("--db", type=Path, default=None, help=f"DB path (default from config.yaml or {DEFAULT_DB})")
    ap.add_argument("--list", action="store_true", help="List recent chats and exit")
    args = ap.parse_args()

    db_path = resolve_db(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    if args.list:
        conn = sqlite3.connect(str(db_path))
        list_chats(conn)
        conn.close()
        return 0

    if not args.chat_id:
        ap.error("chat_id is required (or use --list)")

    return budget_preview(args.chat_id, args.model_name, args.next_msg, db_path)


if __name__ == "__main__":
    raise SystemExit(main())
