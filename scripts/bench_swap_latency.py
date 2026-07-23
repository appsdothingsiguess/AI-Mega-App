#!/usr/bin/env python3
"""
Phase-0.5 Test 7 item 1: dynamic util-load-on-demand cold-swap latency.
Measures real llama-swap swap-in latency (not a direct llama-server boot,
see bench_server.py for that proxy number) -- fires a request for a model
that llama-swap does NOT currently have resident, and times from request-sent
to first-successful-response, which includes llama-swap's own model-unload
(if it must evict something to make room) + model-load + first-token time.

PREREQUISITE: serving/llama-swap/config.yaml (outside this repo, on the box)
must be regenerated against current model paths and actually running -- this
does not boot llama-server directly, it hits llama-swap's own proxied
endpoint (default :8080, OpenAI-compatible) by design, per docs/PLAN.md's
"reached only through app/llm_client.py" contract.

Usage:
  bench_swap_latency.py --model-alias utility --swap-url http://127.0.0.1:8080 \
      [--repeats 5] [--warm-other-model-first coder]

Writes one JSON line per attempt to
logs/benchmarks/swap/<model-alias>_coldload.jsonl
"""
import argparse, json, sys, time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import http_json, log  # noqa: E402

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "swap"

PROMPTS = {
    "utility": "Summarize this in one line: 'The quarterly report shows revenue up 12%.'",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-alias", required=True, help="llama-swap model alias/group name to trigger a swap for")
    ap.add_argument("--swap-url", default="http://127.0.0.1:8080")
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--request-timeout", type=int, default=60)
    ap.add_argument("--n-predict", type=int, default=32)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / f"{args.model_alias}_coldload.jsonl"
    prompt = args.prompt or PROMPTS.get(args.model_alias, "Say hello in one sentence.")

    latencies = []
    for i in range(args.repeats):
        t0 = time.perf_counter()
        try:
            body, wall = http_json(
                f"{args.swap_url}/v1/chat/completions",
                {"model": args.model_alias,
                 "messages": [{"role": "user", "content": prompt}],
                 "max_tokens": args.n_predict, "stream": False},
                args.request_timeout,
            )
            usage = body.get("usage", {})
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(), "model_alias": args.model_alias,
                "run": i, "wall_s": wall, "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
            }
            log(f"  [{i}] model={args.model_alias} wall={wall:.2f}s "
                f"(this includes any llama-swap swap-in cost if the model wasn't already resident)")
        except Exception as e:
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(), "model_alias": args.model_alias,
                "run": i, "error": str(e), "wall_s": time.perf_counter() - t0,
            }
            log(f"  [{i}] error: {e}")
        latencies.append(rec.get("wall_s"))
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    valid = [l for l in latencies if l is not None]
    summary = {
        "event": "summary", "model_alias": args.model_alias, "n": len(latencies),
        "first_call_s": latencies[0] if latencies else None,  # the one most likely to include swap-in cost
        "avg_s": round(sum(valid) / len(valid), 3) if valid else None,
    }
    log(f"SUMMARY {json.dumps(summary)} -- note: first_call_s is the number that matters for "
        f"cold-swap latency, later calls measure an already-resident model")
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(summary) + "\n")


if __name__ == "__main__":
    main()
