#!/usr/bin/env python3
# ruff: noqa: E501,E702,F541,I001
"""Diff swapgen output vs deployed llama-swap config.

Compares app/gpu/swapgen.generate(get_config()) (fresh, from config.yaml +
settings.local.yaml overlay) against the on-disk file at
config.gpu.swap_yaml_path (default /home/john/llm-stack/serving/llama-swap/config.yaml).

Detects silent drift like coder-small's placement flipping between GPU0/GPU1
three separate times (HANDOFF entries) before it surfaces live.

Usage:
  python scripts/config_drift_check.py           # diff + exit 1 if drift
  python scripts/config_drift_check.py --write   # overwrite deployed file with fresh output
  python scripts/config_drift_check.py --out /tmp/fresh.yaml  # dump fresh without diffing
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="Diff swapgen vs deployed llama-swap config")
    ap.add_argument("--deployed", type=Path, default=None, help="Deployed yaml path (default from config.gpu.swap_yaml_path)")
    ap.add_argument("--write", action="store_true", help="Overwrite deployed file with fresh generated content")
    ap.add_argument("--out", type=Path, default=None, help="Write fresh generated yaml to this path and exit 0 (no diff)")
    ap.add_argument("--stdout", action="store_true", help="Also print fresh yaml to stdout with --out, or diff to stdout by default")
    args = ap.parse_args()

    try:
        from app.config import get_config  # type: ignore
        cfg = get_config()
        # Import swapgen without triggering app.gpu.__init__ (which imports httpx)
        import importlib.util
        spec = importlib.util.spec_from_file_location("swapgen_file", str(ROOT / "app" / "gpu" / "swapgen.py"))
        mod = importlib.util.module_from_spec(spec)  # type: ignore
        spec.loader.exec_module(mod)  # type: ignore
        fresh = mod.generate(cfg)  # type: ignore
        deployed_path = args.deployed or Path(cfg.gpu.swap_yaml_path)
    except Exception as e:
        print(f"Failed to generate fresh config: {e}", file=sys.stderr)
        return 2

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(fresh, encoding="utf-8")
        print(f"Wrote fresh config to {args.out} ({len(fresh)} bytes)")
        if args.stdout:
            print("--- fresh ---")
            print(fresh)
        return 0

    deployed_text: str | None = None
    if deployed_path.exists():
        deployed_text = deployed_path.read_text(encoding="utf-8")
    else:
        print(f"Deployed file not found: {deployed_path}", file=sys.stderr)
        print("--- fresh generated (no deployed to compare) ---")
        print(fresh)
        return 1

    if deployed_text == fresh:
        print(f"No drift: fresh matches deployed ({deployed_path})")
        return 0

    diff = difflib.unified_diff(
        deployed_text.splitlines(), fresh.splitlines(),
        fromfile=f"deployed:{deployed_path}", tofile="fresh:swapgen.generate(get_config())",
        lineterm="\n",
    )
    diff_text = "".join(diff)

    print(f"DRIFT detected: {deployed_path} differs from fresh swapgen output")
    print(diff_text if diff_text.strip() else "(diff empty — files differ in trailing newline only)")
    print("\nTo apply fresh: python scripts/config_drift_check.py --write  (or --out /tmp/fresh.yaml to preview)")

    if args.write:
        deployed_path.parent.mkdir(parents=True, exist_ok=True)
        deployed_path.write_text(fresh, encoding="utf-8")
        print(f"Wrote fresh config to {deployed_path}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
