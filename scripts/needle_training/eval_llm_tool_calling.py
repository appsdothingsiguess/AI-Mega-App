#!/usr/bin/env python3
"""
Head-to-head eval: a small Qwen chat model as tool-call dispatcher, scored
with the SAME call_f1/exact_match metric needle's own eval.py uses, on the
SAME held-out test slice of scripts/needle_training/data.jsonl (last 60 of
250, the same file the needle finetune rehearsal used).

This is the eval the handoff doc (HANDOFF_phase0_benchmarks.md) flagged as
"not yet run" before picking needle vs. a small-Qwen fallback as the tool
dispatcher.

Usage:
  eval_llm_tool_calling.py --label qwen3-4b --model /path/model.gguf \
      [--port 8899] [--n-test 60] [--ctx 4096] [--tensor-split 3,1]
"""
import argparse, json, os, re, signal, subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone
import urllib.request

BIN = "/home/john/llm-stack/engine/llama.cpp/build/bin/llama-server"
REPO = Path("/home/john/AI-Mega-App")
DATA = REPO / "scripts" / "needle_training" / "data.jsonl"
OUTDIR = REPO / "logs" / "benchmarks" / "server"

SYSTEM_PROMPT = (
    "You are a function-calling dispatcher. Given a user query and a list of "
    "available tools, respond with ONLY a JSON array containing exactly one "
    "object: {\"name\": <tool_name>, \"arguments\": {<arg>: <value>, ...}}. "
    "Use only tools/arguments from the provided list. No explanation, no "
    "markdown fences, no <think> tags -- output the JSON array and nothing else."
)


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def wait_healthy(port, proc, timeout):
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, "server exited before healthy"
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True, None
        except Exception:
            pass
        time.sleep(1)
    return False, f"not healthy within {timeout}s"


def http_json(url, payload, timeout):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    return body, time.perf_counter() - t0


def extract_json_array(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\[.*\]", text, flags=re.S)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    # Some small models emit ["tool_name", {args}] instead of
    # [{"name": "tool_name", "arguments": {args}}] -- salvage that shape too,
    # it's a format slip, not a wrong-tool/wrong-args error.
    if (isinstance(parsed, list) and len(parsed) == 2
            and isinstance(parsed[0], str) and isinstance(parsed[1], dict)):
        return [{"name": parsed[0], "arguments": parsed[1]}]
    return parsed


def _normalize_value(v):
    if isinstance(v, str):
        try:
            v = str(float(v))
        except ValueError:
            pass
        s = v.strip().lower() if isinstance(v, str) else v
        return s
    if isinstance(v, float):
        return str(v)
    return v


def _normalize_args(args):
    if not isinstance(args, dict):
        return args
    return {k: _normalize_value(v) for k, v in args.items()}


def call_key(c):
    if not isinstance(c, dict):
        return None
    args = c.get("arguments", c.get("parameters", {}))  # some small models emit "parameters" instead of "arguments"
    if isinstance(args, dict) and set(args.keys()) <= {"type", "properties"}:
        args = args.get("properties", args)
    return json.dumps({"name": c.get("name"), "arguments": _normalize_args(args)}, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--ctx", type=int, default=4096)
    ap.add_argument("--tensor-split", default=None)
    ap.add_argument("--n-test", type=int, default=60)
    ap.add_argument("--n-predict", type=int, default=200)
    ap.add_argument("--no-think", action="store_true", help="try to suppress Qwen3 thinking mode")
    ap.add_argument("--boot-timeout", type=int, default=120)
    ap.add_argument("--request-timeout", type=int, default=60)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / f"tool-eval-{args.label}.jsonl"
    if jsonl_path.exists():
        jsonl_path.unlink()

    lines = DATA.read_text().splitlines()
    test = [json.loads(l) for l in lines[-args.n_test:]]
    log(f"Loaded {len(test)} held-out test examples from {DATA} (last {args.n_test} of {len(lines)})")

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
        tail = (proc.stdout.read()[-3000:] if proc.stdout else "")
        kill_proc("boot failed")
        log(f"FAIL {args.label}: {err}\n{tail}")
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
                "max_tokens": args.n_predict,
                "temperature": 0,
                "stream": False,
            }
            try:
                body, wall = http_json(f"http://127.0.0.1:{args.port}/v1/chat/completions", payload, args.request_timeout)
                out_text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception as e:
                out_text = ""
                wall = None
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
            mark = "OK" if is_exact else "WRONG"
            log(f"  [{i}] {mark} query={ex['query'][:50]!r} ref={ref_calls} pred={pred_calls}")
    finally:
        kill_proc("done")

    n = len(test)
    precision = tp_calls / max(tp_calls + fp_calls, 1)
    recall = tp_calls / max(tp_calls + fn_calls, 1)
    call_f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    avg_lat = sum(latencies) / len(latencies) if latencies else None

    summary = {
        "label": args.label, "model": args.model, "n_test": n,
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
