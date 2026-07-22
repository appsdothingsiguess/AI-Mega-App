#!/usr/bin/env python3
"""
Coder-quality eval: fires a fixed set of small coding prompts (spanning
several languages) at an already-running llama-server, extracts the code
block from each response, writes it to a temp file, and actually
compiles/runs it in a subprocess to see if the model's code works — not
just whether it "looks" right.

Reuses bench_server.py's http_json() request helper. Toolchains that
aren't installed on this box (checked via shutil.which) are reported as
"toolchain not found" and skipped rather than failing the run.

Usage:
  eval_coder_compile.py --prompts scripts/eval_data/coding_prompts.json \
      --model-label qwen2.5-coder-14b --port 8899 [--n-predict 512]

Writes one JSONL row per (model, language/prompt) pair to
  logs/benchmarks/quality/coder_compile.jsonl
with the extracted code, exit code, stdout, stderr, and a pass/fail flag
(pass = process exited 0; a "toolchain not found" row is neither pass nor
fail and is marked skipped=true).
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import http_json, log  # noqa: E402

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "quality"

CODE_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)

# Each entry: (required_binaries, file extension, fn(binaries, filepath) -> argv)
RUNNERS = {
    "python": {
        "bins": ["python3"],
        "ext": ".py",
        "cmd": lambda b, f: [b["python3"], str(f)],
    },
    "javascript": {
        "bins": ["node"],
        "ext": ".js",
        "cmd": lambda b, f: [b["node"], str(f)],
    },
    "bash": {
        "bins": ["bash"],
        "ext": ".sh",
        "cmd": lambda b, f: [b["bash"], str(f)],
    },
    "go": {
        "bins": ["go"],
        "ext": ".go",
        "cmd": lambda b, f: [b["go"], "run", str(f)],
    },
    "rust": {
        "bins": ["rustc"],
        "ext": ".rs",
        # compile then run the produced binary
        "compile_and_run": True,
    },
    "c": {
        "bins": ["gcc"],
        "ext": ".c",
        "compile_and_run": True,
    },
}


def extract_code(text):
    m = CODE_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()  # fall back to raw text if no fenced block found


def which_map(bins):
    return {b: shutil.which(b) for b in bins}


def run_case(language, code, request_timeout):
    spec = RUNNERS.get(language)
    if spec is None:
        return {"skipped": True, "reason": f"no runner configured for language '{language}'"}

    bins = which_map(spec["bins"])
    missing = [b for b, p in bins.items() if p is None]
    if missing:
        return {"skipped": True, "reason": f"toolchain not found: {', '.join(missing)}"}

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        src = tdp / f"solution{spec['ext']}"
        src.write_text(code)

        try:
            if language == "rust":
                out_bin = tdp / "solution_bin"
                compile_proc = subprocess.run(
                    [bins["rustc"], str(src), "-o", str(out_bin)],
                    capture_output=True, text=True, timeout=request_timeout,
                )
                if compile_proc.returncode != 0:
                    return {"skipped": False, "exit_code": compile_proc.returncode,
                            "stdout": compile_proc.stdout, "stderr": compile_proc.stderr,
                            "passed": False, "stage": "compile"}
                run_proc = subprocess.run([str(out_bin)], capture_output=True, text=True,
                                           timeout=request_timeout)
            elif language == "c":
                out_bin = tdp / "solution_bin"
                compile_proc = subprocess.run(
                    [bins["gcc"], str(src), "-o", str(out_bin)],
                    capture_output=True, text=True, timeout=request_timeout,
                )
                if compile_proc.returncode != 0:
                    return {"skipped": False, "exit_code": compile_proc.returncode,
                            "stdout": compile_proc.stdout, "stderr": compile_proc.stderr,
                            "passed": False, "stage": "compile"}
                run_proc = subprocess.run([str(out_bin)], capture_output=True, text=True,
                                           timeout=request_timeout)
            else:
                argv = spec["cmd"](bins, src)
                run_proc = subprocess.run(argv, capture_output=True, text=True,
                                           timeout=request_timeout, cwd=td)
            return {"skipped": False, "exit_code": run_proc.returncode,
                    "stdout": run_proc.stdout, "stderr": run_proc.stderr,
                    "passed": run_proc.returncode == 0, "stage": "run"}
        except subprocess.TimeoutExpired as e:
            return {"skipped": False, "exit_code": None, "stdout": e.stdout or "",
                    "stderr": (e.stderr or "") + "\n[TIMEOUT]", "passed": False, "stage": "timeout"}
        except Exception as e:
            return {"skipped": False, "exit_code": None, "stdout": "", "stderr": str(e),
                    "passed": False, "stage": "exception"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", default=str(REPO / "scripts" / "eval_data" / "coding_prompts.json"),
                     help="JSON file: [{\"language\": ..., \"prompt\": ...}, ...]")
    ap.add_argument("--model-label", required=True)
    ap.add_argument("--model-path", default=None)
    ap.add_argument("--port", type=int, required=True, help="port of the already-running llama-server")
    ap.add_argument("--n-predict", type=int, default=512)
    ap.add_argument("--request-timeout", type=int, default=180, help="HTTP request timeout (s)")
    ap.add_argument("--exec-timeout", type=int, default=15, help="subprocess run/compile timeout (s)")
    args = ap.parse_args()

    cases = json.loads(Path(args.prompts).read_text())

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / "coder_compile.jsonl"

    url = f"http://127.0.0.1:{args.port}/v1/chat/completions"
    n_pass = n_fail = n_skip = 0

    for case in cases:
        language = case["language"]
        prompt = case["prompt"]
        req_t0 = time.perf_counter()
        try:
            body, wall = http_json(
                url,
                {"messages": [{"role": "user", "content": prompt}],
                 "max_tokens": args.n_predict, "stream": False},
                args.request_timeout,
            )
            msg = body.get("choices", [{}])[0].get("message", {})
            raw_response = msg.get("content", "")
            code = extract_code(raw_response)
            result = run_case(language, code, args.exec_timeout)
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "model_label": args.model_label, "model_path": args.model_path,
                "language": language, "prompt": prompt,
                "response": raw_response, "extracted_code": code,
                "latency_s": wall, **result,
            }
        except Exception as e:
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "model_label": args.model_label, "model_path": args.model_path,
                "language": language, "prompt": prompt,
                "error": str(e), "wall_s": time.perf_counter() - req_t0,
                "skipped": False, "passed": False,
            }
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

        if rec.get("skipped"):
            n_skip += 1
            log(f"SKIP {args.model_label}/{language}: {rec.get('reason')}")
        elif rec.get("passed"):
            n_pass += 1
            log(f"PASS {args.model_label}/{language}")
        else:
            n_fail += 1
            log(f"FAIL {args.model_label}/{language}: exit={rec.get('exit_code')}")

    log(f"DONE {args.model_label}: {n_pass} pass, {n_fail} fail, {n_skip} skipped -> {jsonl_path}")
    sys.exit(0 if n_fail == 0 else 4)


if __name__ == "__main__":
    main()
