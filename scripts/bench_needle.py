#!/usr/bin/env python3
"""
needle-class benchmark: Cactus Needle 26M has no GGUF/llama.cpp path (see
docs/BENCHMARK_PLAN.md §2), so per explicit sign-off it runs under its own
runtime (JAX, CPU-only) instead -- boots needle's own HTTP server
(engine/needle/serve.py, same /generate API as the upstream playground) and
benchmarks it exactly like bench_server.py does for llama.cpp models: same
JSONL analytics schema (latency, tokens where available, output), same
timeout/kill discipline.

Usage: bench_needle.py --label needle-cpu [--repeats 5] [--port 7861]
"""
import argparse, json, subprocess, sys, time, signal
from pathlib import Path
from datetime import datetime, timezone
import urllib.request

NEEDLE_DIR = Path("/home/john/llm-stack/engine/needle")
REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "server"

# Needle's tool schema is a FLAT dict of {argname: {type, description,
# required}} -- NOT nested JSON-Schema ({"type":"object","properties":{...}}).
# Using the JSON-Schema-nested form (an earlier version of this script did)
# is out-of-distribution for the model and produces garbled output (e.g. the
# literal string "properties" leaking into generated JSON) -- not a
# performance bug, a request-format bug. Format per the model card's own
# example (engine/needle/docs/hf_model_card.md).
TEST_CALLS = [
    {
        "query": "Set a timer for 10 minutes.",
        "tools": json.dumps([{"name": "set_timer", "description": "Sets a timer",
                               "parameters": {"minutes": {"type": "integer", "description": "Duration in minutes.", "required": True}}}]),
    },
    {
        "query": "Turn off the kitchen lights.",
        "tools": json.dumps([{"name": "set_light", "description": "Turns a light on or off",
                               "parameters": {
                                   "room": {"type": "string", "description": "Room name.", "required": True},
                                   "state": {"type": "string", "description": "'on' or 'off'.", "required": True}}}]),
    },
    {
        "query": "What's the weather in Paris?",
        "tools": json.dumps([{"name": "get_weather", "description": "Get current weather for a city.",
                               "parameters": {"location": {"type": "string", "description": "City name.", "required": True}}}]),
    },
]


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def wait_healthy(port, proc, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="needle-cpu")
    ap.add_argument("--port", type=int, default=7861)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--boot-timeout", type=int, default=60)
    ap.add_argument("--request-timeout", type=int, default=30)
    ap.add_argument("--checkpoint", default="checkpoints/needle.pkl")
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / f"{args.label}.jsonl"

    cmd = [str(NEEDLE_DIR / ".venv" / "bin" / "python"), str(NEEDLE_DIR / "serve.py"),
           "--checkpoint", args.checkpoint, "--port", str(args.port), "--host", "127.0.0.1"]
    log(f"START {args.label}: {' '.join(cmd)} (cwd={NEEDLE_DIR})")
    load_t0 = time.perf_counter()
    env = dict(__import__("os").environ)
    env["JAX_PLATFORMS"] = "cpu"  # force CPU explicitly, no ambiguity about device
    ncpu = str(__import__("os").cpu_count() or 1)
    env.setdefault("XLA_CPU_MULTI_THREAD_EIGEN", "true")
    env.setdefault("INTRA_OP_PARALLELISM_THREADS", ncpu)
    env.setdefault("INTER_OP_PARALLELISM_THREADS", ncpu)
    env.setdefault("OMP_NUM_THREADS", ncpu)
    proc = subprocess.Popen(cmd, cwd=str(NEEDLE_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def kill_proc(reason):
        log(f"KILL {args.label}: {reason}")
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    if not wait_healthy(args.port, proc, args.boot_timeout):
        tail = proc.stdout.read()[-3000:] if proc.stdout else ""
        kill_proc("boot failed/timeout")
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "label": args.label, "class": "needle",
               "event": "boot_fail", "stderr_tail": tail}
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        log(f"FAIL {args.label}: server did not become healthy")
        sys.exit(3)

    load_s = time.perf_counter() - load_t0
    log(f"HEALTHY {args.label} after {load_s:.1f}s")

    # JAX JIT-compiles the forward pass on first call (XLA tracing +
    # compilation), which can take seconds and has nothing to do with
    # steady-state inference speed -- warm the compilation cache with one
    # untimed call before the timed loop, same as any JAX benchmarking
    # practice. Skipping this was the bug in the first pass: it measured
    # compile time, not tok/s, which is why it looked ~1000x slower than
    # the official Cactus numbers (github.com/cactus-compute/cactus).
    try:
        warm_req = urllib.request.Request(
            f"http://127.0.0.1:{args.port}/generate",
            data=json.dumps({**TEST_CALLS[0], "max_gen_len": 128}).encode(),
            headers={"Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(warm_req, timeout=args.request_timeout) as r:
            r.read()
        log(f"WARMUP {args.label}: {time.perf_counter() - t0:.2f}s (JIT compile, excluded from results)")
    except Exception as e:
        log(f"WARMUP {args.label} failed: {e}")

    errs = 0
    for i in range(args.repeats):
        call = TEST_CALLS[i % len(TEST_CALLS)]
        payload = {**call, "max_gen_len": 128}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{args.port}/generate", data=data,
            headers={"Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=args.request_timeout) as r:
                body = json.loads(r.read())
            wall = time.perf_counter() - t0
            rec = {"ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
                   "class": "needle", "event": "generate", "run": i, "latency_s": wall,
                   "load_s": load_s, "query": call["query"], "output": body.get("result", "")}
        except Exception as e:
            errs += 1
            rec = {"ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
                   "class": "needle", "event": "generate_error", "run": i, "error": str(e)}
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    kill_proc("run complete")
    log(f"DONE {args.label}: {args.repeats} requests, {errs} errors, load={load_s:.1f}s -> {jsonl_path}")
    sys.exit(0 if not errs else 4)


if __name__ == "__main__":
    main()
