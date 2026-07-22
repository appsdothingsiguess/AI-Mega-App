#!/usr/bin/env python3
"""
Quality-transcript collector: fires a fixed prompt set at each candidate
model (one already-running llama-server at a time) and records prompt +
response pairs for later manual (Claude-judged) quality review. This does
not score anything itself — it just produces the transcripts; a
`quality_review` field is left null so a later pass can fill it in.

Reuses bench_server.py's http_json() request helper rather than
re-implementing the HTTP/JSON plumbing, and follows the same
--image / base64 vision-message pattern bench_server.py uses (a
`type: image_url` content block with a data: URI).

Input file (JSON or YAML) shape, one prompt set per model class:
  {
    "reasoner": [
      {"id": "sheep", "prompt": "A farmer has 17 sheep..."},
      {"id": "kalman", "prompt": "In three sentences, explain..."}
    ],
    "vision": [
      {"id": "chart1", "prompt": "Describe this chart.", "image": "/path/to/chart.png"}
    ]
  }

Usage:
  eval_quality_transcripts.py --prompts prompts.yaml --class reasoner \
      --model-label qwen3-14b --port 8899 [--n-predict 512] [--request-timeout 180]

Writes one JSONL row per prompt to
  logs/benchmarks/quality/<class>.jsonl
(appending across models/runs, one file per class — e.g. reasoner.jsonl,
vision.jsonl). Assumes the llama-server for --model-label is already
running at --port; this script does not boot/tear down servers.
"""
import argparse
import base64
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import http_json, log  # noqa: E402

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "quality"


def load_prompts(path):
    text = Path(path).read_text()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            log("FAIL: PyYAML not installed but a .yaml/.yml prompts file was given; "
                "install pyyaml or pass a .json file instead")
            sys.exit(2)
        return yaml.safe_load(text)
    return json.loads(text)


def build_content(prompt, image_path):
    if not image_path:
        return prompt
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", required=True, help="JSON or YAML prompt-set file")
    ap.add_argument("--class", dest="model_class", required=True,
                     help="key into the prompt-set file, e.g. reasoner, vision")
    ap.add_argument("--model-label", required=True, help="human label for the model under test")
    ap.add_argument("--model-path", default=None, help="optional gguf path, recorded for provenance")
    ap.add_argument("--port", type=int, required=True, help="port of the already-running llama-server")
    ap.add_argument("--n-predict", type=int, default=512)
    ap.add_argument("--request-timeout", type=int, default=180)
    args = ap.parse_args()

    prompt_sets = load_prompts(args.prompts)
    if args.model_class not in prompt_sets:
        log(f"FAIL: class '{args.model_class}' not found in {args.prompts} "
            f"(available: {list(prompt_sets)})")
        sys.exit(2)
    items = prompt_sets[args.model_class]

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / f"{args.model_class}.jsonl"

    url = f"http://127.0.0.1:{args.port}/v1/chat/completions"
    errs = 0
    for item in items:
        pid = item.get("id", item["prompt"][:40])
        content = build_content(item["prompt"], item.get("image"))
        req_t0 = time.perf_counter()
        try:
            body, wall = http_json(
                url,
                {"messages": [{"role": "user", "content": content}],
                 "max_tokens": args.n_predict, "stream": False},
                args.request_timeout,
            )
            usage = body.get("usage", {})
            msg = body.get("choices", [{}])[0].get("message", {})
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "class": args.model_class, "prompt_id": pid,
                "model_label": args.model_label, "model_path": args.model_path,
                "prompt": item["prompt"], "image": item.get("image"),
                "response": msg.get("content", ""),
                "reasoning": msg.get("reasoning_content", ""),
                "latency_s": wall,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "quality_review": None,
            }
        except Exception as e:
            errs += 1
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "class": args.model_class, "prompt_id": pid,
                "model_label": args.model_label, "model_path": args.model_path,
                "prompt": item["prompt"], "image": item.get("image"),
                "error": str(e), "wall_s": time.perf_counter() - req_t0,
                "quality_review": None,
            }
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    log(f"DONE {args.model_class}/{args.model_label}: {len(items)} prompts, {errs} errors -> {jsonl_path}")
    sys.exit(0 if not errs else 4)


if __name__ == "__main__":
    main()
