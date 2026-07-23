#!/usr/bin/env python3
"""
Phase-0.5 Test 7 item 4: embedding retrieval quality (recall@k), never
measured before (only latency has been benched so far, see phase0-measurements
.md §8/Test 1). Boots the `embed` server once, embeds the fixed corpus
(scripts/eval_data/embed_corpus.json) and each labeled query
(scripts/eval_data/embed_retrieval_set.json), ranks the corpus by cosine
similarity per query, and checks whether the labeled relevant_doc_ids land in
the top-k.

Usage:
  eval_embed_retrieval.py --model /path/embed.gguf [--device none] \
      [--port 8899] [--k 1,5,10]

Writes per-query hits + aggregate recall@k to
logs/benchmarks/quality/embed_retrieval.jsonl
"""
import argparse, json, math, os, signal, subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import BIN, wait_healthy, http_json, log  # noqa: E402

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "quality"
CORPUS_PATH = REPO / "scripts" / "eval_data" / "embed_corpus.json"
QUERIES_PATH = REPO / "scripts" / "eval_data" / "embed_retrieval_set.json"


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def embed(port, text, timeout):
    body, wall = http_json(f"http://127.0.0.1:{port}/v1/embeddings", {"input": text}, timeout)
    return body["data"][0]["embedding"], wall


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", default=None, help="'none' to force CPU")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--boot-timeout", type=int, default=120)
    ap.add_argument("--request-timeout", type=int, default=60)
    ap.add_argument("--k", default="1,5,10")
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / "embed_retrieval.jsonl"
    ks = [int(x) for x in args.k.split(",")]

    corpus = json.loads(CORPUS_PATH.read_text())
    queries = json.loads(QUERIES_PATH.read_text())

    if not Path(args.model).is_file():
        log(f"FAIL embed_retrieval: model not found: {args.model}")
        sys.exit(2)

    cmd = [BIN, "--port", str(args.port), "-m", args.model, "--ctx-size", "2048",
           "-ngl", "999", "--embeddings", "--host", "127.0.0.1"]
    if args.device:
        cmd += ["--device", args.device, "-ngl", "0"]

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = "/home/john/llm-stack/engine/llama.cpp/build/bin:" + env.get("LD_LIBRARY_PATH", "")

    log(f"START embed_retrieval: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def kill_proc(reason):
        log(f"KILL embed_retrieval: {reason}")
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    ok, err = wait_healthy(args.port, proc, args.boot_timeout)
    if not ok:
        kill_proc("boot failed")
        log(f"FAIL embed_retrieval: {err}")
        sys.exit(3)
    log("HEALTHY embed_retrieval")

    hits_at_k = {k: 0 for k in ks}
    try:
        corpus_vecs = []
        for doc in corpus:
            vec, _ = embed(args.port, doc["text"], args.request_timeout)
            corpus_vecs.append((doc["doc_id"], vec))
        log(f"Embedded {len(corpus_vecs)} corpus docs")

        for q in queries:
            qvec, wall = embed(args.port, q["query"], args.request_timeout)
            scored = sorted(
                ((cosine(qvec, v), doc_id) for doc_id, v in corpus_vecs),
                key=lambda x: -x[0],
            )
            ranked_ids = [doc_id for _, doc_id in scored]
            relevant = set(q["relevant_doc_ids"])
            per_k_hit = {}
            for k in ks:
                hit = bool(relevant & set(ranked_ids[:k]))
                per_k_hit[f"hit_at_{k}"] = hit
                if hit:
                    hits_at_k[k] += 1
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(), "query_id": q["id"],
                "query": q["query"], "relevant_doc_ids": q["relevant_doc_ids"],
                "top10_ranked": ranked_ids[:10], "latency_s": wall, **per_k_hit,
            }
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            log(f"  {q['id']}: {per_k_hit} top3={ranked_ids[:3]}")
    finally:
        kill_proc("run complete")

    n = len(queries)
    summary = {"event": "summary", "n_queries": n, "n_corpus": len(corpus),
               **{f"recall_at_{k}": round(hits_at_k[k] / n, 4) for k in ks}}
    log(f"SUMMARY {json.dumps(summary)}")
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(summary) + "\n")


if __name__ == "__main__":
    main()
