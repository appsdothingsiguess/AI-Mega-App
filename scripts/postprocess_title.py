#!/usr/bin/env python3
"""
Deterministic title cleanup (Phase-0.5 Test 7 item 2): strips markdown
code-fence wrapping, quote wrapping, and trailing punctuation from a raw
model-generated title -- a cheap post-processing fix instead of re-prompting.
Mirrors the same rubric eval_title_gen.py already scores against (word count
5-8, no wrap quotes, no trailing punct).

Usage as a library: `from postprocess_title import clean_title`
Usage as a CLI self-test: `python3 postprocess_title.py` runs the fixed case
table in scripts/eval_data/title_cleanup_cases.json and reports pass/fail.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path("/home/john/AI-Mega-App")
CASES_PATH = REPO / "scripts" / "eval_data" / "title_cleanup_cases.json"

FENCE_RE = re.compile(r"^```(?:\w+)?\s*\n?|\n?```$")


def clean_title(raw: str) -> str:
    text = raw.strip()
    # Strip a full code-fence wrap (```...``` or ```lang\n...\n```)
    if text.startswith("```") and text.endswith("```") and len(text) >= 6:
        text = FENCE_RE.sub("", text).strip()
    # Strip a single leading/trailing backtick, or quote wrapping
    while len(text) >= 2 and text[0] in "`\"'" and text[-1] == text[0]:
        text = text[1:-1].strip()
    # Strip trailing punctuation
    text = re.sub(r"[.!?]+$", "", text).strip()
    return text


def _run_self_test():
    cases = json.loads(CASES_PATH.read_text())
    n_pass = 0
    for c in cases:
        got = clean_title(c["raw"])
        ok = got == c["expected"]
        n_pass += ok
        status = "PASS" if ok else "FAIL"
        print(f"{status} {c['id']}: raw={c['raw']!r} -> got={got!r} expected={c['expected']!r}")
    print(f"\n{n_pass}/{len(cases)} cases passed")
    sys.exit(0 if n_pass == len(cases) else 1)


if __name__ == "__main__":
    _run_self_test()
