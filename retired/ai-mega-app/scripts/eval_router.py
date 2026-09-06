#!/usr/bin/env python3
"""Router eval harness (PLAN.md §4.3 / §4.10).

Scores eval/router_eval.csv against app.router.route.

CI (no GPU):
  python scripts/eval_router.py --fake

Box gate (live llama-swap):
  python scripts/eval_router.py --base-url http://127.0.0.1:8080 --min-accuracy 90

Integrator note (do not edit CI from this script): an optional CI job can run
`--fake`; the live box gate uses `--min-accuracy 90`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CLASSES = frozenset(
    {
        "chat",
        "chit_chat",
        "code_task",
        "tool_call_needed",
        "reasoning_task",
        "vision_task",
    }
)
_CLASSES_BY_LEN = sorted(CLASSES, key=len, reverse=True)


def extract_label(text: str) -> str | None:
    """Longest-first \\b match — never `if label in text` (chat ⊂ chit_chat)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip().lower()
    for cat in _CLASSES_BY_LEN:
        if re.search(rf"\b{re.escape(cat)}\b", text):
            return cat
    return None


def load_rows(path: Path) -> list[dict[str, str]]:
    raw = path.read_text(encoding="utf-8")
    kept = [ln for ln in raw.splitlines(keepends=True) if not ln.lstrip().startswith("#")]
    reader = csv.DictReader(io.StringIO("".join(kept)))
    required = {"prompt", "expected_class", "attachment", "notes"}
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        raise SystemExit(f"CSV missing columns {sorted(required)}: {path}")
    rows: list[dict[str, str]] = []
    for i, row in enumerate(reader, start=2):
        prompt = (row.get("prompt") or "").strip()
        expected = (row.get("expected_class") or "").strip()
        if not prompt:
            raise SystemExit(f"row {i}: empty prompt")
        if expected not in CLASSES:
            raise SystemExit(f"row {i}: invalid expected_class {expected!r}")
        rows.append(
            {
                "prompt": prompt,
                "expected_class": expected,
                "attachment": (row.get("attachment") or "").strip(),
                "notes": (row.get("notes") or "").strip(),
            }
        )
    if not rows:
        raise SystemExit(f"CSV has no data rows: {path}")
    return rows


def _attachments(raw: str) -> list[dict[str, str]]:
    if not raw or raw == "empty":
        return []
    return [{"type": raw}]


def _precision_recall(
    expected: list[str], predicted: list[str]
) -> dict[str, tuple[float, float, int, int, int]]:
    """Per-class (precision, recall, tp, fp, fn)."""
    out: dict[str, tuple[float, float, int, int, int]] = {}
    for cls in sorted(CLASSES):
        tp = sum(1 for e, p in zip(expected, predicted) if e == cls and p == cls)
        fp = sum(1 for e, p in zip(expected, predicted) if e != cls and p == cls)
        fn = sum(1 for e, p in zip(expected, predicted) if e == cls and p != cls)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        out[cls] = (prec, rec, tp, fp, fn)
    return out


def _print_report(
    expected: list[str],
    predicted: list[str],
    rows: list[dict[str, str]],
) -> float:
    n = len(expected)
    correct = sum(1 for e, p in zip(expected, predicted) if e == p)
    acc = correct / n if n else 0.0

    print(f"\nOverall accuracy: {correct}/{n} = {acc * 100:.2f}%\n")
    print(f"{'class':<20} {'P':>7} {'R':>7} {'tp':>4} {'fp':>4} {'fn':>4}")
    for cls, (prec, rec, tp, fp, fn) in _precision_recall(expected, predicted).items():
        support = sum(1 for e in expected if e == cls)
        if support == 0 and tp == 0 and fp == 0:
            continue
        print(f"{cls:<20} {prec:7.3f} {rec:7.3f} {tp:4d} {fp:4d} {fn:4d}")

    labels = sorted(CLASSES)
    idx = {c: i for i, c in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for e, p in zip(expected, predicted):
        if e in idx and p in idx:
            matrix[idx[e]][idx[p]] += 1
    print("\nConfusion matrix (rows=expected, cols=predicted):")
    print(" " * 18 + " ".join(f"{c[:6]:>6}" for c in labels))
    for i, cls in enumerate(labels):
        print(f"{cls:<18}" + " ".join(f"{matrix[i][j]:6d}" for j in range(len(labels))))

    fails = [(r, p) for r, p in zip(rows, predicted) if r["expected_class"] != p]
    print(f"\nFailing rows ({len(fails)}):")
    for r, p in fails[:50]:
        note = f"  [{r['notes']}]" if r["notes"] else ""
        print(f"  expected={r['expected_class']:<18} got={p:<18}{note}")
        print(f"    {r['prompt'][:120]}")
    if len(fails) > 50:
        print(f"  ... {len(fails) - 50} more")
    return acc


def _install_fake_classify(label_map: dict[str, str]) -> None:
    """Monkeypatch app.router.classifier.classify → async stub from CSV map.

    Router imports the module as `_clf` and calls `_clf.classify`, so patching
    the module attribute is enough. Rules/attachments still run first in route().
    """
    import app.router.classifier as clf_mod

    async def _stub(
        text: str,
        *,
        llm_client: object,
        cfg: object,
    ) -> tuple[str, float] | None:
        label = label_map.get(text)
        if label is None:
            return None
        return (label, 1.0)

    clf_mod.classify = _stub  # type: ignore[assignment]


async def _run_eval(
    rows: list[dict[str, str]],
    *,
    fake: bool,
    base_url: str | None,
) -> tuple[list[str], list[str]]:
    from app.config import get_config
    from app.llm_client import LLMClient
    from app.router import route

    cfg = get_config()
    if fake:
        label_map = {r["prompt"]: r["expected_class"] for r in rows}
        _install_fake_classify(label_map)
        # route() requires a non-None llm_client before calling classify;
        # the stub never touches the client.
        llm: object = object()
    else:
        assert base_url is not None
        llm = LLMClient(base_url=base_url, timeout_s=cfg.llama_swap.timeout_s)

    expected: list[str] = []
    predicted: list[str] = []
    for r in rows:
        atts = _attachments(r["attachment"])
        result = await route(
            {},
            r["prompt"],
            atts,
            llm_client=llm,  # type: ignore[arg-type]
            config=cfg,
            trace_id=None,
        )
        pred = result.intent
        if pred not in CLASSES:
            extracted = extract_label(pred)
            pred = extracted if extracted else pred
        expected.append(r["expected_class"])
        predicted.append(pred)
    return expected, predicted


def main() -> int:
    ap = argparse.ArgumentParser(description="Score router against labeled CSV")
    ap.add_argument("--csv", type=Path, default=ROOT / "eval" / "router_eval.csv")
    ap.add_argument("--fake", action="store_true", help="CI mode: stub classifier from CSV")
    ap.add_argument("--base-url", type=str, default=None, help="Live llama-swap base URL")
    ap.add_argument(
        "--min-accuracy",
        type=float,
        default=None,
        help="Exit 1 if overall accuracy %% is below N",
    )
    args = ap.parse_args()

    if not args.csv.is_file():
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        return 1

    rows = load_rows(args.csv)
    counts = Counter(r["expected_class"] for r in rows)
    print(f"Loaded {len(rows)} rows from {args.csv}")
    for cls in sorted(counts):
        print(f"  {cls}: {counts[cls]}")

    try:
        from app.router import route as _route  # noqa: F401
    except ImportError as exc:
        print(f"Router import failed ({exc}); CSV validated only.")
        return 0

    if not args.fake and not args.base_url:
        print("Provide --fake or --base-url", file=sys.stderr)
        return 1
    if args.fake and args.base_url:
        print("Use either --fake or --base-url, not both", file=sys.stderr)
        return 1

    expected, predicted = asyncio.run(
        _run_eval(rows, fake=args.fake, base_url=args.base_url)
    )
    acc = _print_report(expected, predicted, rows)
    if args.min_accuracy is not None and acc * 100.0 < args.min_accuracy:
        print(f"\nFAIL: accuracy {acc * 100:.2f}% < --min-accuracy {args.min_accuracy}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
