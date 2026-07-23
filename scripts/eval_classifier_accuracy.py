#!/usr/bin/env python3
"""
Phase-0.5 Test 7 item 5: classifier broader accuracy pass. Phase-0 only
hand-tested 5 intents; this runs ~90 labeled examples across the app's real
routing categories (chat, code_task, tool_call_needed, reasoning_task,
vision_task, chit_chat, plus deliberately ambiguous ones) against a booted
classifier server.

PREREQUISITE: the classifier's thinking-mode suppression fix (the '/no_think'
prompt suffix, see phase0-measurements.md §2) must already be confirmed
working -- this eval reproduces the same root-cause failure if run against a
still-broken classifier, it doesn't diagnose that fix.

Usage:
  eval_classifier_accuracy.py --label classifier-qwen3-1.7b --model /path.gguf \
      [--device none] [--port 8899] [--no-think]

Writes per-item + confusion-matrix summary to
logs/benchmarks/quality/classifier_accuracy.jsonl
"""
import argparse, json, os, re, signal, subprocess, sys, time
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import BIN, wait_healthy, http_json, log  # noqa: E402

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "quality"
DATA = REPO / "scripts" / "eval_data" / "classifier_intents.json"

CATEGORIES = ["chat", "code_task", "tool_call_needed", "reasoning_task", "vision_task", "chit_chat"]
# checked longest-first so "chit_chat" never gets misread as a "chat" substring match
_CATEGORIES_BY_LEN = sorted(CATEGORIES, key=len, reverse=True)

PROMPT_TEMPLATE = (
    "You are a routing classifier for an AI assistant. Classify the user's "
    "message into EXACTLY ONE category. Output only the category name, "
    "nothing else.\n\n"
    "Categories:\n"
    "- chat: informational/explanatory questions or requests for opinions "
    "the assistant can answer directly from its own knowledge (no live "
    "data, no tools, no code writing).\n"
    "- code_task: the user wants code written, refactored, debugged, or "
    "reviewed.\n"
    "- tool_call_needed: the user needs a live/external action -- web "
    "search, fetching a URL, reading/searching local files, running code, "
    "or saving/recalling a memory. Anything requiring real-time or "
    "external data/state, even if phrased casually.\n"
    "- reasoning_task: an explicit multi-step logic/math puzzle or a "
    "request to think step by step / reason through something before "
    "answering, where the deliberation itself is the point.\n"
    "- vision_task: the message references an attached image, screenshot, "
    "photo, or diagram that must be visually interpreted.\n"
    "- chit_chat: a short conversational reply with no informational "
    "request -- acknowledgments, thanks, greetings, filler.\n\n"
    "Examples:\n"
    "Message: \"What's the difference between a list and a tuple in Python?\"\n"
    "Category: chat\n\n"
    "Message: \"Write a Python function to reverse a linked list.\"\n"
    "Category: code_task\n\n"
    "Message: \"Search the web for the latest Node.js LTS version.\"\n"
    "Category: tool_call_needed\n\n"
    "Message: \"Remember that I prefer 2-space indentation from now on.\"\n"
    "Category: tool_call_needed\n\n"
    "Message: \"What's the current stock price of Nvidia?\"\n"
    "Category: tool_call_needed\n"
    "(real-time/live data the assistant can't know from training -- tool_call_needed "
    "even with no tool named explicitly, NOT chat)\n\n"
    "Message: \"Find every file in the app/ directory that imports os.\"\n"
    "Category: tool_call_needed\n"
    "(searching/reading existing files -- tool_call_needed, NOT code_task; "
    "code_task is only for writing/modifying/reviewing code)\n\n"
    "Message: \"A farmer has 17 sheep. All but 9 die. How many are left? "
    "Show your reasoning.\"\n"
    "Category: reasoning_task\n\n"
    "Message: \"How many objects are in this image?\"\n"
    "Category: vision_task\n\n"
    "Message: \"haha nice, thanks!\"\n"
    "Category: chit_chat\n\n"
    "Now classify:\n"
    "Message: \"{message}\"\n"
    "Category:"
)


def extract_label(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip().lower()
    for cat in _CATEGORIES_BY_LEN:
        if re.search(rf"\b{re.escape(cat)}\b", text):
            return cat
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", default=None, help="'none' to force CPU")
    ap.add_argument("--ctx", type=int, default=4096)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--boot-timeout", type=int, default=120)
    ap.add_argument("--request-timeout", type=int, default=30)
    ap.add_argument("--n-predict", type=int, default=48)
    ap.add_argument("--no-think", action="store_true", help="append a literal /no_think line to each prompt")
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / "classifier_accuracy.jsonl"

    items = json.loads(DATA.read_text())

    if not Path(args.model).is_file():
        log(f"FAIL {args.label}: model not found: {args.model}")
        sys.exit(2)

    cmd = [BIN, "--port", str(args.port), "-m", args.model, "--ctx-size", str(args.ctx),
           "-ngl", "999", "--flash-attn", "on", "--jinja", "--host", "127.0.0.1", "--temp", "0",
           "--reasoning", "off"]
    if args.device:
        cmd += ["--device", args.device, "-ngl", "0"]

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

    ok, err = wait_healthy(args.port, proc, args.boot_timeout)
    if not ok:
        kill_proc("boot failed")
        log(f"FAIL {args.label}: {err}")
        sys.exit(3)
    log(f"HEALTHY {args.label}")

    confusion = defaultdict(Counter)
    correct = 0
    per_category_total = Counter()
    per_category_correct = Counter()

    try:
        for item in items:
            prompt = PROMPT_TEMPLATE.format(message=item["message"])
            if args.no_think:
                prompt += "\n/no_think"
            expected = item["expected_intent"]
            per_category_total[expected] += 1
            t0 = time.perf_counter()
            try:
                body, wall = http_json(
                    f"http://127.0.0.1:{args.port}/v1/chat/completions",
                    {"messages": [{"role": "user", "content": prompt}],
                     "max_tokens": args.n_predict, "temperature": 0, "stream": False},
                    args.request_timeout,
                )
                raw = body.get("choices", [{}])[0].get("message", {}).get("content", "")
                pred = extract_label(raw)
            except Exception as e:
                raw, pred, wall = "", None, time.perf_counter() - t0
                log(f"  error on {item['id']}: {e}")

            is_correct = pred == expected
            if is_correct:
                correct += 1
                per_category_correct[expected] += 1
            confusion[expected][pred or "NONE"] += 1

            rec = {
                "ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
                "item_id": item["id"], "message": item["message"], "expected": expected,
                "predicted": pred, "raw_response": raw, "latency_s": wall, "correct": is_correct,
            }
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            log(f"  {'OK' if is_correct else 'WRONG'} {item['id']}: expected={expected} predicted={pred}")
    finally:
        kill_proc("run complete")

    n = len(items)
    per_cat_acc = {
        cat: round(per_category_correct[cat] / max(per_category_total[cat], 1), 4)
        for cat in per_category_total
    }
    summary = {
        "event": "summary", "label": args.label, "n": n,
        "overall_accuracy": round(correct / max(n, 1), 4),
        "per_category_accuracy": per_cat_acc,
        "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
    }
    log(f"SUMMARY {json.dumps(summary)}")
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(summary) + "\n")


if __name__ == "__main__":
    main()
