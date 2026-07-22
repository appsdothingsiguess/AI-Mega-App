#!/usr/bin/env python3
"""Check whether the finetuned checkpoint memorized its own training
examples (if it matches training data exactly but not fresh phrasings,
that's 'too little data' per the tool's own warning, not a checkpoint bug)."""
import json, subprocess, sys, time, urllib.request
from pathlib import Path

NEEDLE_DIR = Path("/home/john/llm-stack/engine/needle")
lines = Path("/home/john/AI-Mega-App/scripts/needle_training/data.jsonl").read_text().splitlines()
examples = [json.loads(l) for l in lines[:5]]

cmd = [str(NEEDLE_DIR / ".venv" / "bin" / "python"), str(NEEDLE_DIR / "serve.py"),
       "--checkpoint", "checkpoints/needle_finetuned_20260721231004_32018_12_512_best.pkl",
       "--port", "7864", "--host", "127.0.0.1"]
import os
env = dict(os.environ)
env["JAX_PLATFORMS"] = "cpu"
proc = subprocess.Popen(cmd, cwd=str(NEEDLE_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
deadline = time.time() + 60
while time.time() < deadline:
    if proc.poll() is not None:
        print(proc.stdout.read()); sys.exit(1)
    try:
        with urllib.request.urlopen("http://127.0.0.1:7864/", timeout=2) as r:
            if r.status == 200: break
    except Exception:
        time.sleep(1)

correct = 0
for ex in examples:
    req = urllib.request.Request(
        "http://127.0.0.1:7864/generate",
        data=json.dumps({"query": ex["query"], "tools": ex["tools"], "max_gen_len": 128}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read())
    got = body.get("result", "")
    expected = ex["answers"]
    match = json.loads(got) == json.loads(expected) if got else False
    correct += match
    print(f"[{'OK' if match else 'DIFF'}] {ex['query']}")
    print(f"   expected: {expected}")
    print(f"   got:      {got}")

print(f"\n{correct}/{len(examples)} exact match on TRAINING-SET examples")
proc.send_signal(15)
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()
