#!/usr/bin/env python3
"""
Phase-0.5 Test 7 item 3 (+ item 8, max-context ceiling): does quality/speed
hold up as a real conversation fills toward 32k tokens (and beyond, for the
ceiling test), vs. the short single-turn prompts used everywhere else in the
harness so far.

Loads scripts/eval_data/context_depth_transcript.json (a real multi-turn
conversation with a `seed_fact` stated in turn 0), replays it against a
booted llama-server with GROWING context (the full running message history,
never reset), and at each token checkpoint in `--checkpoints` pads the
conversation with deterministic filler turns until the running prompt
crosses that checkpoint's token estimate (chars/4), then asks the recall
probe ("what's the codename ... who owns it?") and records TTFT/tok-s/latency
plus the raw answer for later Claude-judged correctness scoring.

Usage:
  bench_context_depth.py --label chat-default-ctxdepth \
      --model /path/model.gguf --model-class chat-default --ctx 40000 \
      [--tensor-split 3,1] [--checkpoints 2000,8000,16000,24000,32000] \
      [--port 8899]

For the Test 8 max-ceiling variant, pass a larger --ctx and --checkpoints
that extend past 32000 (e.g. 32000,65000,100000) -- the script stops early
if a request errors out (VRAM/ctx-allocation failure), which is itself the
signal for "ceiling found".

Writes one JSON line per checkpoint to
logs/benchmarks/quality/context_depth.jsonl -- recall-probe correctness is
Claude-judged afterward from the logged `probe_answer` field, same as
eval_quality_transcripts.py's transcripts.
"""
import argparse, json, os, signal, subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import BIN, wait_healthy, http_json, gpu_mem, log  # noqa: E402

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "quality"
DATA = REPO / "scripts" / "eval_data" / "context_depth_transcript.json"

FILLER_PARAGRAPH = (
    "For context, here is some unrelated background chatter to pad out this "
    "conversation's length: the benchmark harness logs every request to a "
    "JSONL file under logs/benchmarks, tags each row with a wall-clock "
    "timestamp, and never leaves a llama-server process resident after a "
    "job finishes. Every model in the roster gets its own boot-health-check-"
    "teardown cycle so GPU memory is never shared across unrelated runs "
    "unless a test is deliberately exercising concurrency. "
)

PROBE_TURN_ID = 10


def est_tokens(messages):
    return sum(len(m["content"]) for m in messages) // 4


def build_checkpoint_messages(turns, seed_fact, target_tokens):
    """Real turns 0..N-1 (excluding the final probe) + deterministic filler
    turns, alternating user/assistant, until token estimate crosses target."""
    real_turns = [t for t in turns if t["turn_id"] != PROBE_TURN_ID]
    probe_turn = next(t for t in turns if t["turn_id"] == PROBE_TURN_ID)

    messages = [{"role": t["role"], "content": t["content"]} for t in real_turns]
    filler_i = 0
    next_role = "user"
    while est_tokens(messages) < target_tokens:
        messages.append({"role": next_role, "content": f"(filler #{filler_i}) {FILLER_PARAGRAPH}"})
        next_role = "assistant" if next_role == "user" else "user"
        filler_i += 1
        if filler_i > 5000:  # sanity guard against an infinite loop
            break
    messages.append({"role": "user", "content": probe_turn["content"]})
    return messages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-class", dest="model_class", default="chat-default")
    ap.add_argument("--ctx", type=int, default=40000)
    ap.add_argument("--tensor-split", default=None)
    ap.add_argument("--checkpoints", default="2000,8000,16000,24000,32000")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--boot-timeout", type=int, default=180)
    ap.add_argument("--request-timeout", type=int, default=300)
    ap.add_argument("--n-predict", type=int, default=128)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / "context_depth.jsonl"

    data = json.loads(DATA.read_text())
    checkpoints = [int(c) for c in args.checkpoints.split(",")]

    if not Path(args.model).is_file():
        log(f"FAIL {args.label}: model not found: {args.model}")
        sys.exit(2)

    cmd = [BIN, "--port", str(args.port), "-m", args.model, "--ctx-size", str(args.ctx),
           "-ngl", "999", "--flash-attn", "on", "--jinja", "--host", "127.0.0.1"]
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

    baseline_tok_s = None
    try:
        for target in checkpoints:
            messages = build_checkpoint_messages(data["turns"], data["seed_fact"], target)
            est = est_tokens(messages)
            t0 = time.perf_counter()
            try:
                body, wall = http_json(
                    f"http://127.0.0.1:{args.port}/v1/chat/completions",
                    {"messages": messages, "max_tokens": args.n_predict, "stream": False},
                    args.request_timeout,
                )
                usage = body.get("usage", {})
                answer = body.get("choices", [{}])[0].get("message", {}).get("content", "")
                gen_toks = usage.get("completion_tokens", 0)
                tok_s = gen_toks / wall if wall > 0 and gen_toks else None
                if baseline_tok_s is None and tok_s:
                    baseline_tok_s = tok_s
                degradation_pct = (
                    round(100 * (1 - tok_s / baseline_tok_s), 1)
                    if tok_s and baseline_tok_s else None
                )
                rec = {
                    "ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
                    "model": args.model, "checkpoint_target_tokens": target,
                    "estimated_prompt_tokens": est, "actual_prompt_tokens": usage.get("prompt_tokens"),
                    "latency_s": wall, "gen_tok_s": tok_s,
                    "degradation_pct_vs_first_checkpoint": degradation_pct,
                    "probe_answer": answer, "event": "checkpoint", **gpu_mem(),
                }
                log(f"CHECKPOINT {args.label} target={target} actual={usage.get('prompt_tokens')} "
                    f"tok_s={tok_s} degradation={degradation_pct}% answer={answer[:120]!r}")
            except Exception as e:
                rec = {
                    "ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
                    "model": args.model, "checkpoint_target_tokens": target,
                    "estimated_prompt_tokens": est, "event": "checkpoint_error",
                    "error": str(e), "wall_s": time.perf_counter() - t0,
                }
                log(f"FAIL checkpoint target={target}: {e} -- stopping (ceiling likely found here)")
                with open(jsonl_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                break
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
    finally:
        kill_proc("run complete")

    log(f"DONE {args.label} -> {jsonl_path}")


if __name__ == "__main__":
    main()
