#!/usr/bin/env python3
"""
Title-generation head-to-head: fires the same fixed exchange set at an
already-running llama-server and automatically scores each response against
a fixed rubric (no Claude judging needed -- these are mechanically checkable):
  - word_count: 5-8 words (the length actually asked for in the prompt)
  - no_wrap_quotes: response isn't wrapped in "..." or '...'
  - no_trailing_punct: doesn't end in . ! ?
Semantic relevance is left as a raw-text field for a quick eyeball pass,
not auto-scored (that part still needs a human/Claude glance since it's not
mechanically checkable).

Usage:
  eval_title_gen.py --prompts scripts/eval_data/title_gen_prompts.json \
      --model-label hammer --port 8899 [--n-predict 32]

Writes one JSONL row per prompt to logs/benchmarks/quality/title_gen.jsonl
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import http_json, log  # noqa: E402

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "quality"

PROMPT_TEMPLATE = (
    "Generate a short 5-8 word title for this chat exchange "
    "(title only, no punctuation at the end, no quotes):\n\n{exchange}"
)


def score_title(text):
    stripped = text.strip()
    unwrapped = stripped
    wrapped_in_quotes = False
    if len(stripped) >= 2 and stripped[0] in "\"'" and stripped[-1] in "\"'":
        wrapped_in_quotes = True
        unwrapped = stripped[1:-1].strip()
    words = unwrapped.split()
    word_count = len(words)
    trailing_punct = bool(re.search(r"[.!?]$", unwrapped))
    return {
        "word_count": word_count,
        "word_count_ok": 5 <= word_count <= 8,
        "wrapped_in_quotes": wrapped_in_quotes,
        "trailing_punct": trailing_punct,
        "rubric_pass": (5 <= word_count <= 8) and not wrapped_in_quotes and not trailing_punct,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", default=str(REPO / "scripts" / "eval_data" / "title_gen_prompts.json"))
    ap.add_argument("--model-label", required=True)
    ap.add_argument("--model-path", default=None)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--n-predict", type=int, default=32)
    ap.add_argument("--request-timeout", type=int, default=120)
    args = ap.parse_args()

    items = json.loads(Path(args.prompts).read_text())
    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / "title_gen.jsonl"

    url = f"http://127.0.0.1:{args.port}/v1/chat/completions"
    n_pass = 0
    latencies = []

    for item in items:
        prompt = PROMPT_TEMPLATE.format(exchange=item["exchange"])
        req_t0 = time.perf_counter()
        try:
            body, wall = http_json(
                url,
                {"messages": [{"role": "user", "content": prompt}],
                 "max_tokens": args.n_predict, "stream": False},
                args.request_timeout,
            )
            msg = body.get("choices", [{}])[0].get("message", {})
            text = msg.get("content", "")
            rubric = score_title(text)
            latencies.append(wall)
            if rubric["rubric_pass"]:
                n_pass += 1
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "model_label": args.model_label, "model_path": args.model_path,
                "prompt_id": item["id"], "response": text, "latency_s": wall,
                **rubric,
            }
            log(f"{'PASS' if rubric['rubric_pass'] else 'FAIL'} {args.model_label}/{item['id']}: "
                f"{text!r} ({rubric['word_count']}w, {wall:.2f}s)")
        except Exception as e:
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "model_label": args.model_label, "model_path": args.model_path,
                "prompt_id": item["id"], "error": str(e),
                "wall_s": time.perf_counter() - req_t0, "rubric_pass": False,
            }
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    n = len(items)
    avg_lat = sum(latencies) / len(latencies) if latencies else None
    log(f"DONE {args.model_label}: {n_pass}/{n} rubric-pass, avg_latency={avg_lat:.3f}s -> {jsonl_path}"
        if avg_lat else f"DONE {args.model_label}: {n_pass}/{n} rubric-pass -> {jsonl_path}")


if __name__ == "__main__":
    main()
