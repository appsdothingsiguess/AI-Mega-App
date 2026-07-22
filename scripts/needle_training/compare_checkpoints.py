#!/usr/bin/env python3
"""One-off: compare base vs finetuned needle checkpoint on app-specific
queries the base model got wrong (file_read, file_grep, run_code, web_search
-- 0/10 or near-0 in BASE_EVAL)."""
import json, subprocess, sys, time, urllib.request
from pathlib import Path

NEEDLE_DIR = Path("/home/john/llm-stack/engine/needle")
TOOLS = json.loads(Path("/home/john/AI-Mega-App/scripts/needle_training/data.jsonl").read_text().splitlines()[0])["tools"]

TEST_QUERIES = [
    ("open README.md for me", "file_read"),
    ("what does app/main.py contain?", "file_read"),
    ("search for 'FIXME' in app/tools/", "file_grep"),
    ("where does api_key show up in src/?", "file_grep"),
    ("can you run `print(2**10)` in python?", "run_code"),
    ("search the web for the current price of Bitcoin", "web_search"),
    ("google who won the last F1 race", "web_search"),
]


def run_checkpoint(ckpt, port):
    cmd = [str(NEEDLE_DIR / ".venv" / "bin" / "python"), str(NEEDLE_DIR / "serve.py"),
           "--checkpoint", ckpt, "--port", str(port), "--host", "127.0.0.1"]
    import os
    env = dict(os.environ)
    env["JAX_PLATFORMS"] = "cpu"
    proc = subprocess.Popen(cmd, cwd=str(NEEDLE_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            print(proc.stdout.read())
            sys.exit(1)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(1)

    results = []
    for query, expected_tool in TEST_QUERIES:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/generate",
            data=json.dumps({"query": query, "tools": TOOLS, "max_gen_len": 128}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read())
        out = body.get("result", "")
        try:
            got_tool = json.loads(out)[0]["name"]
        except Exception:
            got_tool = "<unparseable>"
        results.append((query, expected_tool, got_tool, out))

    proc.send_signal(15)
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    return results


if __name__ == "__main__":
    print("=== BASE checkpoint (checkpoints/needle.pkl) ===")
    base = run_checkpoint("checkpoints/needle.pkl", 7862)
    correct_base = sum(1 for _, exp, got, _ in base if exp == got)
    for q, exp, got, out in base:
        mark = "OK" if exp == got else "WRONG"
        print(f"[{mark}] expected={exp} got={got} | {q} -> {out}")
    print(f"Base: {correct_base}/{len(base)} correct tool selection\n")

    print("=== FINETUNED checkpoint (best) ===")
    finetuned = run_checkpoint(
        "checkpoints/needle_finetuned_20260721231004_32018_12_512_best.pkl", 7863)
    correct_ft = sum(1 for _, exp, got, _ in finetuned if exp == got)
    for q, exp, got, out in finetuned:
        mark = "OK" if exp == got else "WRONG"
        print(f"[{mark}] expected={exp} got={got} | {q} -> {out}")
    print(f"Finetuned: {correct_ft}/{len(finetuned)} correct tool selection")
