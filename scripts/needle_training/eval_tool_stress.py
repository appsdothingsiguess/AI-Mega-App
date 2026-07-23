#!/usr/bin/env python3
"""
Phase-0.5 Test 7 item 7: harder tool-registry stress test. Same scoring
(call_f1/exact_match/json_parse_rate) and boot/request machinery as
eval_llm_tool_calling.py, but pointed at data_stress.jsonl -- a 13-tool
registry with deliberately overlapping/confusable names (file_read vs
file_read_lines vs read_file_metadata; web_search vs web_search_news vs
web_search_images; fetch_url vs fetch_url_raw) instead of the real app's
narrow 6-tool set. Run against both Hammer2.1-1.5b and FunctionGemma-270M
(the two adopted/candidate dispatchers) to get a head-to-head under
registry pressure, directly comparable to the narrow-set numbers already in
docs/phase0-measurements.md (76.3%/79.0% zero-shot/prompt-tuned call_f1 for
the generic small-Qwen harness).

Usage:
  eval_tool_stress.py --label hammer --model /path/Hammer2.1-1.5b.gguf [--port 8899]
  eval_tool_stress.py --label functiongemma --model /path/functiongemma.gguf [--port 8899]

Writes per-item + summary to logs/benchmarks/server/tool-stress-<label>.jsonl
"""
import argparse, json, os, signal, subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_llm_tool_calling import extract_json_array, call_key, wait_healthy, http_json, log  # noqa: E402

BIN = "/home/john/llm-stack/engine/llama.cpp/build/bin/llama-server"
REPO = Path("/home/john/AI-Mega-App")
DATA = REPO / "scripts" / "needle_training" / "data_stress.jsonl"
OUTDIR = REPO / "logs" / "benchmarks" / "server"

SYSTEM_PROMPT = (
    "You are a function-calling dispatcher. Given a user query and a list of "
    "available tools, respond with ONLY a JSON array containing exactly one "
    "object: {\"name\": <tool_name>, \"arguments\": {<arg>: <value>, ...}}. "
    "Some tool names are similar -- pick the ONE that most precisely matches "
    "the user's request (e.g. a request for only file metadata should use "
    "read_file_metadata, not file_read). Use only tools/arguments from the "
    "provided list. No explanation, no markdown fences, no <think> tags -- "
    "output the JSON array and nothing else."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--ctx", type=int, default=4096)
    ap.add_argument("--tensor-split", default=None)
    ap.add_argument("--n-predict", type=int, default=200)
    ap.add_argument("--boot-timeout", type=int, default=120)
    ap.add_argument("--request-timeout", type=int, default=60)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / f"tool-stress-{args.label}.jsonl"
    if jsonl_path.exists():
        jsonl_path.unlink()

    test = [json.loads(l) for l in DATA.read_text().splitlines() if l.strip()]
    log(f"Loaded {len(test)} stress examples (13-tool overlapping registry) from {DATA}")

    if not Path(args.model).is_file():
        log(f"FAIL {args.label}: model not found: {args.model}")
        sys.exit(2)

    cmd = [BIN, "--port", str(args.port), "-m", args.model, "--ctx-size", str(args.ctx),
           "-ngl", "999", "--flash-attn", "on", "--jinja", "--host", "127.0.0.1", "--temp", "0"]
    if args.tensor_split:
        cmd += ["--tensor-split", args.tensor_split, "-sm", "tensor"]

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = "/home/john/llm-stack/engine/llama.cpp/build/bin:" + env.get("LD_LIBRARY_PATH", "")

    log(f"START {args.label}: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def kill_proc(reason):
        log(f"KILL {args.label}: {reason}")
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    load_t0 = time.perf_counter()
    ok, err = wait_healthy(args.port, proc, args.boot_timeout)
    load_s = time.perf_counter() - load_t0
    if not ok:
        kill_proc("boot failed")
        log(f"FAIL {args.label}: {err}")
        sys.exit(3)
    log(f"HEALTHY {args.label} after {load_s:.1f}s")

    tp_calls = fp_calls = fn_calls = 0
    exact = 0
    parse_ok = 0
    latencies = []

    try:
        for i, ex in enumerate(test):
            tools = json.loads(ex["tools"])
            ref_calls = json.loads(ex["answers"])
            user_content = f"Tools:\n{json.dumps(tools)}\n\nQuery: {ex['query']}\n/no_think"
            payload = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": args.n_predict, "temperature": 0, "stream": False,
            }
            try:
                body, wall = http_json(f"http://127.0.0.1:{args.port}/v1/chat/completions", payload, args.request_timeout)
                out_text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception as e:
                out_text, wall = "", None
                log(f"  [{i}] request error: {e}")

            pred_calls = extract_json_array(out_text)
            if pred_calls is None:
                pred_calls = []
            else:
                parse_ok += 1
            if wall is not None:
                latencies.append(wall)

            ref_keys = {call_key(c) for c in ref_calls} - {None}
            pred_keys = {call_key(c) for c in (pred_calls if isinstance(pred_calls, list) else [])} - {None}
            tp_calls += len(pred_keys & ref_keys)
            fp_calls += len(pred_keys - ref_keys)
            fn_calls += len(ref_keys - pred_keys)
            is_exact = ref_keys == pred_keys and len(ref_keys) > 0
            if is_exact:
                exact += 1

            rec = {"ts": datetime.now(timezone.utc).isoformat(), "label": args.label, "i": i,
                   "query": ex["query"], "ref": ref_calls, "pred": pred_calls, "raw_output": out_text,
                   "latency_s": wall, "exact": is_exact}
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            log(f"  [{i}] {'OK' if is_exact else 'WRONG'} query={ex['query'][:50]!r} ref={ref_calls} pred={pred_calls}")
    finally:
        kill_proc("done")

    n = len(test)
    precision = tp_calls / max(tp_calls + fp_calls, 1)
    recall = tp_calls / max(tp_calls + fn_calls, 1)
    call_f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    avg_lat = sum(latencies) / len(latencies) if latencies else None

    summary = {
        "label": args.label, "model": args.model, "n_test": n, "registry_size": 13,
        "call_f1": round(call_f1, 4), "exact_match": round(exact / max(n, 1), 4),
        "json_parse_rate": round(parse_ok / max(n, 1), 4),
        "avg_latency_s": round(avg_lat, 3) if avg_lat else None,
        "load_s": round(load_s, 1),
    }
    log(f"SUMMARY {json.dumps(summary)}")
    with open(jsonl_path, "a") as f:
        f.write(json.dumps({"event": "summary", **summary}) + "\n")


if __name__ == "__main__":
    main()
