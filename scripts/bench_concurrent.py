#!/usr/bin/env python3
"""
Concurrent-load benchmark: boots N llama-server processes at once (unlike
bench_server.py / bench_models.sh, which drive one model at a time) and
fires request batches at all of them simultaneously, one thread per
resident model, to see how tok/s and latency degrade under real contention
(e.g. a reasoner on GPU0 + a coder on GPU1 + an embed model sharing GPU0).

Reuses bench_server.py's health-check / HTTP / gpu_mem helpers as a library
rather than re-implementing them.

Usage:
  bench_concurrent.py --label dual-load \
      --model gguf=/path/a.gguf,port=8901,class=reasoner,tensor-split=1,0 \
      --model gguf=/path/b.gguf,port=8902,class=coder,device=cuda1 \
      [--duration 30] [--repeats 5] [--n-predict 256] [--ctx 8192]

Each --model spec is a comma-separated key=value list. Recognized keys:
  gguf (required), port (required), class (required, see bench_server
  DEFAULT_PROMPTS keys), tensor-split, device, mmproj, ctx, n-predict,
  prompt-file. Anything not overridden falls back to the top-level flag
  of the same name (--ctx, --n-predict) or bench_server's default prompt
  for that class.

Writes one JSONL row per request (plus gpu_mem_before/during/after
snapshot rows) to
  logs/benchmarks/concurrent/<label>_<timestamp>.jsonl
All rows in one invocation share a `concurrent_group_id` so they can be
grouped back into a single scenario. Tears down every server on exit,
success or failure (Ctrl-C, exception, timeout).
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import BIN, DEFAULT_PROMPTS, http_json, wait_healthy, gpu_mem, log  # noqa: E402

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "concurrent"


def parse_model_spec(spec, defaults):
    """Parse a `key=value,key=value` --model spec into a dict.

    tensor-split's own value is comma-separated (e.g. "3,1"), which collides
    with the outer key=value delimiter -- write it as "3:1" in the spec and
    it's converted back to "3,1" here before being passed to llama-server.
    """
    d = {}
    for part in spec.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        d[k.strip()] = v.strip()
    for req in ("gguf", "port", "class"):
        if req not in d:
            raise ValueError(f"--model spec missing required key '{req}': {spec}")
    d.setdefault("ctx", defaults["ctx"])
    d.setdefault("n-predict", defaults["n_predict"])
    d["port"] = int(d["port"])
    d["ctx"] = int(d["ctx"])
    d["n-predict"] = int(d["n-predict"])
    if "tensor-split" in d:
        d["tensor-split"] = d["tensor-split"].replace(":", ",")
    return d


def build_cmd(spec):
    cmd = [
        BIN, "--port", str(spec["port"]), "-m", spec["gguf"],
        "--ctx-size", str(spec["ctx"]), "-ngl", "999", "--flash-attn", "on",
        "--jinja", "--host", "127.0.0.1",
    ]
    # cuda-devices pins via CUDA_VISIBLE_DEVICES (see boot_server) and skips
    # --tensor-split/-sm tensor entirely -- those are for genuine multi-GPU
    # splits, not single-device pinning.
    if spec.get("tensor-split") and not spec.get("cuda-devices"):
        cmd += ["--tensor-split", spec["tensor-split"], "-sm", "tensor"]
    if spec.get("device"):
        cmd += ["--device", spec["device"], "-ngl", "0"]
    if spec.get("mmproj"):
        cmd += ["--mmproj", spec["mmproj"]]
    if spec.get("cache-type-k"):
        cmd += ["--cache-type-k", spec["cache-type-k"]]
    if spec.get("cache-type-v"):
        cmd += ["--cache-type-v", spec["cache-type-v"]]
    if spec["class"] == "embed":
        cmd += ["--embeddings"]
    return cmd


def boot_server(spec, boot_timeout):
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = "/home/john/llm-stack/engine/llama.cpp/build/bin:" + env.get("LD_LIBRARY_PATH", "")
    if spec.get("cuda-devices"):
        # Real single-GPU pinning. A degenerate --tensor-split (e.g. "1,0")
        # with -sm tensor is NOT equivalent -- measured ~3x slower than a
        # clean CUDA_VISIBLE_DEVICES restriction (40 vs 126-131 tok/s on the
        # same model), likely because -sm tensor's cross-GPU sync machinery
        # doesn't degrade gracefully to a single real device.
        env["CUDA_VISIBLE_DEVICES"] = spec["cuda-devices"]
    cmd = build_cmd(spec)
    log(f"START port={spec['port']} class={spec['class']}: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t0 = time.perf_counter()
    ok, err = wait_healthy(spec["port"], proc, boot_timeout)
    load_s = time.perf_counter() - t0
    return proc, ok, err, load_s


def kill_proc(proc, label):
    log(f"KILL {label}")
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def worker(spec, group_id, args, jsonl_path, lock, out_rows):
    prompt_file = spec.get("prompt-file")
    if prompt_file:
        prompt = Path(prompt_file).read_text()
    else:
        prompt = DEFAULT_PROMPTS.get(spec["class"])
    url_chat = f"http://127.0.0.1:{spec['port']}/v1/chat/completions"
    url_embed = f"http://127.0.0.1:{spec['port']}/v1/embeddings"

    end_time = time.time() + args.duration if args.duration else None
    i = 0
    while True:
        if end_time is not None and time.time() >= end_time:
            break
        if end_time is None and i >= args.repeats:
            break
        req_t0 = time.perf_counter()
        try:
            if spec["class"] == "embed":
                text = f"chunk number {i}: the quick brown fox jumps over the lazy dog."
                body, wall = http_json(url_embed, {"input": text}, args.request_timeout)
                dims = len(body.get("data", [{}])[0].get("embedding", []))
                rec = {
                    "ts": datetime.now(timezone.utc).isoformat(), "concurrent_group_id": group_id,
                    "port": spec["port"], "model": spec["gguf"], "class": spec["class"],
                    "event": "embed", "run": i, "latency_s": wall, "dims": dims,
                }
            else:
                body, wall = http_json(
                    url_chat,
                    {"messages": [{"role": "user", "content": prompt}],
                     "max_tokens": spec["n-predict"], "stream": False},
                    args.request_timeout,
                )
                usage = body.get("usage", {})
                msg = body.get("choices", [{}])[0].get("message", {})
                gen_toks = usage.get("completion_tokens", 0)
                tok_s = gen_toks / wall if wall > 0 and gen_toks else None
                rec = {
                    "ts": datetime.now(timezone.utc).isoformat(), "concurrent_group_id": group_id,
                    "port": spec["port"], "model": spec["gguf"], "class": spec["class"],
                    "event": "chat", "run": i, "wall_clock_s": wall,
                    "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": gen_toks,
                    "gen_tok_s": tok_s, "output": msg.get("content", ""),
                }
        except Exception as e:
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(), "concurrent_group_id": group_id,
                "port": spec["port"], "model": spec["gguf"], "class": spec["class"],
                "event": "error", "run": i, "error": str(e),
                "wall_clock_s": time.perf_counter() - req_t0,
            }
        with lock:
            out_rows.append(rec)
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        i += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", action="append", required=True, dest="models",
                     help="comma-separated spec: gguf=...,port=...,class=...[,tensor-split=...,device=...,mmproj=...,ctx=...,n-predict=...,prompt-file=...]")
    ap.add_argument("--ctx", type=int, default=8192, help="default ctx for specs that omit it")
    ap.add_argument("--n-predict", type=int, default=256, help="default n-predict for specs that omit it")
    ap.add_argument("--duration", type=float, default=None,
                     help="seconds each worker thread should keep firing requests; overrides --repeats if set")
    ap.add_argument("--repeats", type=int, default=5, help="requests per model if --duration is not set")
    ap.add_argument("--boot-timeout", type=int, default=180)
    ap.add_argument("--request-timeout", type=int, default=180)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jsonl_path = OUTDIR / f"{args.label}_{ts}.jsonl"
    group_id = str(uuid.uuid4())

    defaults = {"ctx": args.ctx, "n_predict": args.n_predict}
    specs = [parse_model_spec(m, defaults) for m in args.models]

    for s in specs:
        if not Path(s["gguf"]).is_file():
            log(f"FAIL: model file not found: {s['gguf']}")
            sys.exit(2)

    procs = []
    lock = threading.Lock()
    out_rows = []

    def log_meta(event, extra=None):
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "concurrent_group_id": group_id,
               "event": event, "gpu_mem": gpu_mem()}
        if extra:
            rec.update(extra)
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    log_meta("gpu_snapshot_before")

    try:
        for spec in specs:
            proc, ok, err, load_s = boot_server(spec, args.boot_timeout)
            procs.append(proc)
            if not ok:
                log(f"FAIL boot port={spec['port']}: {err}")
                log_meta("boot_fail", {"port": spec["port"], "error": err, "load_s": load_s})
                sys.exit(3)
            log(f"HEALTHY port={spec['port']} after {load_s:.1f}s")
            log_meta("boot_ok", {"port": spec["port"], "load_s": load_s})

        log_meta("gpu_snapshot_during_start")

        threads = []
        for spec in specs:
            t = threading.Thread(target=worker, args=(spec, group_id, args, jsonl_path, lock, out_rows))
            t.start()
            threads.append(t)

        # periodic mid-run gpu snapshot while workers are firing
        def sampler():
            while any(t.is_alive() for t in threads):
                log_meta("gpu_snapshot_during")
                time.sleep(2)

        sampler_thread = threading.Thread(target=sampler, daemon=True)
        sampler_thread.start()

        for t in threads:
            t.join()

    finally:
        log_meta("gpu_snapshot_after")
        for proc, spec in zip(procs, specs):
            kill_proc(proc, f"port={spec['port']}")

    errs = [r for r in out_rows if r.get("event") == "error"]
    log(f"DONE {args.label}: {len(out_rows)} requests across {len(specs)} models, {len(errs)} errors -> {jsonl_path}")
    sys.exit(0 if not errs else 4)


if __name__ == "__main__":
    main()
