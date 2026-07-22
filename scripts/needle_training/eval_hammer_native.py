#!/usr/bin/env python3
"""
Eval Hammer2.1-1.5b using ITS OWN native chat template (MadeAgents/Hammer2.1-1.5b
tokenizer_config.json's chat_template: ChatML wrapper with [BEGIN/END OF
AVAILABLE_TOOLS] blocks and a JSON-array output format), rendered via
transformers.apply_chat_template and sent to llama-server's raw /completion
endpoint (bypassing --jinja/GGUF-embedded-template ambiguity).

Compares against the generic-prompt harness (eval_llm_tool_calling.py)
results already in docs/phase0-measurements.md: 76.3% zero-shot, 79.0%
prompt-tuned. Same held-out 60 examples, same call_f1/exact_match/parse-rate
metric (reuses eval_llm_tool_calling.py's scoring functions directly).

Usage:
  eval_hammer_native.py --model /path/Hammer2.1-1.5b-Q4_K_M.gguf --port 8895
"""
import argparse, json, os, subprocess, sys, time, signal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_llm_tool_calling import extract_json_array, call_key, wait_healthy, http_json, log

REPO = Path("/home/john/AI-Mega-App")
DATA = REPO / "scripts" / "needle_training" / "data.jsonl"
BIN = "/home/john/llm-stack/engine/llama.cpp/build/bin/llama-server"
N_TEST = 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=8895)
    ap.add_argument("--n-test", type=int, default=N_TEST)
    ap.add_argument("--n-predict", type=int, default=200)
    ap.add_argument("--ctx", type=int, default=4096)
    args = ap.parse_args()

    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained("MadeAgents/Hammer2.1-1.5b")

    lines = DATA.read_text().splitlines()
    test = [json.loads(l) for l in lines[-args.n_test:]]

    cmd = [BIN, "--port", str(args.port), "-m", args.model, "--ctx-size", str(args.ctx),
           "-ngl", "999", "--flash-attn", "on", "--host", "127.0.0.1", "--temp", "0"]
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = "/home/john/llm-stack/engine/llama.cpp/build/bin:" + env.get("LD_LIBRARY_PATH", "")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def kill_proc():
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    ok, err = wait_healthy(args.port, proc, 60)
    if not ok:
        log(f"FAIL boot: {err}")
        kill_proc()
        sys.exit(1)
    log("HEALTHY")

    tp = fp = fn = exact = parse_ok = 0
    latencies = []

    try:
        for i, ex in enumerate(test):
            tools = json.loads(ex["tools"])
            ref_calls = json.loads(ex["answers"])
            messages = [{"role": "user", "content": ex["query"]}]
            prompt = tokenizer.apply_chat_template(messages, tools=tools, tokenize=False, add_generation_prompt=True)
            # Hammer's native template renders AVAILABLE_TOOLS via Python str()
            # (single-quoted repr), which teaches the model to emit single-quoted
            # "JSON" that fails strict json.loads. Swap in a proper JSON rendering.
            prompt = prompt.replace(str(tools), json.dumps(tools))

            payload = {"prompt": prompt, "n_predict": args.n_predict, "temperature": 0, "stream": False}
            try:
                body, wall = http_json(f"http://127.0.0.1:{args.port}/completion", payload, 60)
                out_text = body.get("content", "")
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
            tp += len(pred_keys & ref_keys)
            fp += len(pred_keys - ref_keys)
            fn += len(ref_keys - pred_keys)
            is_exact = ref_keys == pred_keys and len(ref_keys) > 0
            if is_exact:
                exact += 1
            log(f"  [{i}] {'OK' if is_exact else 'WRONG'} query={ex['query'][:50]!r} ref={ref_calls} pred={pred_calls}")
    finally:
        kill_proc()

    n = len(test)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    summary = {
        "label": "hammer-native-template", "n_test": n,
        "call_f1": round(f1 * 100, 1), "exact_match": round(100 * exact / n, 1),
        "json_parse_rate": round(100 * parse_ok / n, 1),
        "avg_latency_s": round(sum(latencies) / len(latencies), 3) if latencies else None,
    }
    print("SUMMARY", json.dumps(summary))


if __name__ == "__main__":
    main()
