#!/usr/bin/env python3
"""
Real-behavior benchmark: boots a llama-server for one model, fires realistic
requests at it, and logs full analytics (tokens, TTFT, total latency, VRAM,
output text) — the "llama-server + curl" half of docs/BENCHMARK_PLAN.md §1.

Usage:
  bench_server.py --label chat-default-q4 --model /path/model.gguf \
      --class chat-default --ctx 32768 [--tensor-split 3,1] [--mmproj path] \
      [--prompt-file p.txt] [--n-predict 256] [--port 8899] [--timeout 300]

Writes one JSON line per request to logs/benchmarks/server/<label>.jsonl and
prints a one-line summary. Always tears the server down on exit (success,
failure, or timeout) so VRAM is freed for the next run.
"""
import argparse, json, os, signal, subprocess, sys, time, shutil
from pathlib import Path
from datetime import datetime, timezone

import urllib.request
import urllib.error

BIN = "/home/john/llm-stack/engine/llama.cpp/build/bin/llama-server"
REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "server"

DEFAULT_PROMPTS = {
    "chat-default": "In three sentences, explain what a Kalman filter is used for.",
    "coder": "Write a Python function that returns the nth Fibonacci number using memoization. Only output the code.",
    "reasoner": "A farmer has 17 sheep. All but 9 die. How many are left? Show your reasoning briefly, then give the final answer.",
    "vision": None,  # handled specially with an image
    "utility": "Summarize this in one line: 'The quarterly report shows revenue up 12% driven by the new EU rollout, offset by higher cloud spend.'",
    "embed": None,  # handled by /v1/embeddings
    "classifier": "Classify the intent of this message as one of [question, command, chit-chat]: 'turn off the lights in the kitchen'\n/no_think",
    "needle": "Call the function to set a timer for 10 minutes.",
}


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def wait_healthy(port, proc, timeout):
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, "server process exited before becoming healthy"
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True, None
        except Exception:
            pass
        time.sleep(1)
    return False, f"did not become healthy within {timeout}s"


def http_json(url, payload, timeout):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    t1 = time.perf_counter()
    return body, t1 - t0


def gpu_mem():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
            text=True, timeout=5,
        )
        return {f"gpu{i}_mib": int(m) for i, m in (l.split(",") for l in out.strip().splitlines())}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-class", dest="model_class", required=True,
                     choices=list(DEFAULT_PROMPTS))
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--tensor-split", default=None, help="e.g. 3,1; omit for CPU-only / solo-GPU runs")
    ap.add_argument("--device", default=None, help="'none' to force CPU (Config-A CPU-resident tests)")
    ap.add_argument("--mmproj", default=None)
    ap.add_argument("--image", default=None, help="path to a local image for vision-class runs")
    ap.add_argument("--prompt-file", default=None)
    ap.add_argument("--n-predict", type=int, default=256)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--boot-timeout", type=int, default=180)
    ap.add_argument("--request-timeout", type=int, default=180)
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / f"{args.label}.jsonl"

    if not Path(args.model).is_file():
        log(f"FAIL {args.label}: model file not found: {args.model}")
        sys.exit(2)

    cmd = [
        BIN, "--port", str(args.port), "-m", args.model,
        "--ctx-size", str(args.ctx), "-ngl", "999", "--flash-attn", "on",
        "--jinja", "--host", "127.0.0.1",
    ]
    if args.tensor_split:
        cmd += ["--tensor-split", args.tensor_split, "-sm", "tensor"]
    if args.device:
        cmd += ["--device", args.device, "-ngl", "0"]
    if args.mmproj:
        cmd += ["--mmproj", args.mmproj]
    if args.model_class == "embed":
        cmd += ["--embeddings"]

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
        tail = ""
        try:
            tail = proc.stdout.read()[-4000:] if proc.stdout else ""
        except Exception:
            pass
        kill_proc("boot failed/timeout")
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
               "model": args.model, "class": args.model_class, "event": "boot_fail",
               "error": err, "load_s": load_s, "stderr_tail": tail}
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        log(f"FAIL {args.label}: {err}")
        sys.exit(3)

    log(f"HEALTHY {args.label} after {load_s:.1f}s")
    results = []
    try:
        if args.model_class == "embed":
            texts = [f"chunk number {i}: the quick brown fox jumps over the lazy dog." for i in range(args.repeats)]
            for i, text in enumerate(texts):
                try:
                    body, wall = http_json(
                        f"http://127.0.0.1:{args.port}/v1/embeddings",
                        {"input": text}, args.request_timeout,
                    )
                    dims = len(body.get("data", [{}])[0].get("embedding", []))
                    rec = {"ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
                           "model": args.model, "class": args.model_class, "event": "embed",
                           "run": i, "latency_s": wall, "dims": dims, **gpu_mem()}
                except Exception as e:
                    rec = {"ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
                           "class": args.model_class, "event": "embed_error", "run": i, "error": str(e)}
                results.append(rec)
                with open(jsonl_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
        else:
            if args.prompt_file:
                prompt = Path(args.prompt_file).read_text()
            else:
                prompt = DEFAULT_PROMPTS[args.model_class]
            content = [{"type": "text", "text": prompt}]
            if args.image:
                import base64
                b64 = base64.b64encode(Path(args.image).read_bytes()).decode()
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                payload_content = content
            else:
                payload_content = prompt

            for i in range(args.repeats):
                req_t0 = time.perf_counter()
                try:
                    body, wall = http_json(
                        f"http://127.0.0.1:{args.port}/v1/chat/completions",
                        {
                            "messages": [{"role": "user", "content": payload_content}],
                            "max_tokens": args.n_predict,
                            "stream": False,
                        },
                        args.request_timeout,
                    )
                    usage = body.get("usage", {})
                    msg = body.get("choices", [{}])[0].get("message", {})
                    out_text = msg.get("content", "")
                    reasoning = msg.get("reasoning_content", "")
                    gen_toks = usage.get("completion_tokens", 0)
                    tok_s = gen_toks / wall if wall > 0 and gen_toks else None
                    rec = {
                        "ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
                        "model": args.model, "class": args.model_class, "event": "chat",
                        "run": i, "latency_s": wall, "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": gen_toks, "total_tokens": usage.get("total_tokens"),
                        "gen_tok_s": tok_s, "output": out_text, "reasoning": reasoning,
                        "truncated_before_answer": bool(reasoning) and not out_text,
                        **gpu_mem(),
                    }
                except Exception as e:
                    rec = {"ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
                           "class": args.model_class, "event": "chat_error", "run": i,
                           "error": str(e), "wall_s": time.perf_counter() - req_t0}
                results.append(rec)
                with open(jsonl_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
    finally:
        kill_proc("run complete")

    errs = [r for r in results if "error" in r]
    log(f"DONE {args.label}: {len(results)} requests, {len(errs)} errors, load={load_s:.1f}s -> {jsonl_path}")
    sys.exit(0 if not errs else 4)


if __name__ == "__main__":
    main()
