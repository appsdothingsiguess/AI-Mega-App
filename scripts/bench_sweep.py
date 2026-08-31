#!/usr/bin/env python3
"""Run a sequential benchmark matrix from a reusable profile."""
import argparse, itertools, json, subprocess, sys, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "scripts" / "benchmark_profiles"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, help="profile name or JSON path")
    ap.add_argument("--matrix", action="append", required=True, metavar="KEY=V1,V2")
    ap.add_argument("--matrix-env", action="append", default=[], metavar="KEY=V1,V2",
                    help="matrix an environment variable")
    ap.add_argument("--label", default=None)
    ap.add_argument("--port", type=int, default=8898)
    args = ap.parse_args()
    path = Path(args.profile)
    if not path.is_file():
        path = PROFILE_DIR / (args.profile + ".json")
    profile = json.loads(path.read_text())
    axes = []
    for spec in args.matrix:
        key, sep, values = spec.partition("=")
        if not sep or not key or not values:
            ap.error(f"--matrix must be KEY=V1,V2, got {spec!r}")
        axes.append((key, values.split(",")))
    env_axes = []
    for spec in args.matrix_env:
        key, sep, values = spec.partition("=")
        if not sep or not key or not values:
            ap.error(f"--matrix-env must be KEY=V1,V2, got {spec!r}")
        env_axes.append((key, values.split(",")))
    base = args.label or path.stem
    summary = []
    all_axes = [("set:" + k, v) for k, v in axes] + [("env:" + k, v) for k, v in env_axes]
    for values in itertools.product(*(v for _, v in all_axes)):
        changes = dict(zip((k for k, _ in all_axes), values))
        settings_changes = {k[4:]: v for k, v in changes.items() if k.startswith("set:")}
        env_changes = {k[4:]: v for k, v in changes.items() if k.startswith("env:")}
        label = base + "-" + "-".join(f"{k.replace('-','')}{v.replace('.','p')}" for k, v in changes.items())
        cmd = [sys.executable, str(ROOT / "scripts" / "bench_server.py"), "--label", label,
               "--model", profile["model"], "--model-class", profile.get("model_class", "chat-default"),
               "--ctx", str(profile.get("ctx", 8192)), "--repeats", str(profile.get("repeats", 4)),
               "--n-predict", str(profile.get("n_predict", 256)), "--port", str(args.port)]
        settings = dict(profile.get("set", {})); settings.update(settings_changes)
        for key, value in settings.items(): cmd += ["--set", f"{key}={value}"]
        env = dict(profile.get("env", {})); env.update(env_changes)
        for key, value in env.items(): cmd += ["--set-env", f"{key}={value}"]
        print(f"==> {label}: {changes}", flush=True)
        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode not in (0, 4):
            print(f"FAILED {label}: exit {result.returncode}", file=sys.stderr)
        result_file = ROOT / "logs" / "benchmarks" / "server" / f"{label}.jsonl"
        speeds = []
        if result_file.exists():
            for line in result_file.read_text().splitlines():
                try:
                    row = json.loads(line)
                    if row.get("event") == "chat" and row.get("gen_tok_s"):
                        speeds.append(row["gen_tok_s"])
                except json.JSONDecodeError:
                    pass
        summary.append({"label": label, "changes": changes, "gen_tok_s": speeds,
                        "average_gen_tok_s": statistics.mean(speeds) if speeds else None,
                        "exit_code": result.returncode})
    summary.sort(key=lambda row: row["average_gen_tok_s"] or -1, reverse=True)
    out = ROOT / "logs" / "benchmarks" / "server" / f"{base}-sweep.json"
    out.write_text(json.dumps({"profile": str(path), "results": summary}, indent=2) + "\n")
    print("==> Sweep ranking:")
    for row in summary:
        print(f"    {row['label']}: {row['average_gen_tok_s'] or 'n/a'} tok/s")
    print(f"==> Sweep summary: {out}")

if __name__ == "__main__": main()
