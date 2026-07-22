#!/usr/bin/env python3
"""
Data-plane gate (docs/BENCHMARK_PLAN.md §6): inserts 100k x 768-dim vectors
into a sqlite-vec vec0 table (WAL) and measures top-10 KNN + hybrid FTS5
latency. Pass: interactive-fast (<~50ms p95) at 100k. Fail -> Qdrant.

Usage: bench_sqlitevec.py [--n 100000] [--dim 768] [--queries 200]
"""
import argparse, json, random, sqlite3, statistics, struct, time
from pathlib import Path
from datetime import datetime, timezone

import sqlite_vec

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks"


def rand_vec(dim, rng):
    v = [rng.gauss(0, 1) for _ in range(dim)]
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


def pack(v):
    return struct.pack(f"{len(v)}f", *v)


def percentile(vals, p):
    vals = sorted(vals)
    k = (len(vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--dim", type=int, default=768)
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--db", default=str(OUTDIR / "sqlitevec_bench.db"))
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db)
    db_path.unlink(missing_ok=True)
    for suf in ("-wal", "-shm"):
        Path(str(db_path) + suf).unlink(missing_ok=True)

    rng = random.Random(42)
    con = sqlite3.connect(str(db_path))
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.execute("PRAGMA journal_mode=WAL")

    con.execute(f"CREATE VIRTUAL TABLE vec_items USING vec0(embedding float[{args.dim}])")
    con.execute("CREATE VIRTUAL TABLE fts_items USING fts5(body)")

    print(f"Inserting {args.n} x {args.dim}-dim vectors...")
    t0 = time.perf_counter()
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "invoice", "contract",
              "receipt", "ledger", "quarterly", "revenue", "customer", "support"]
    with con:
        for i in range(args.n):
            v = rand_vec(args.dim, rng)
            con.execute("INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)", (i, pack(v)))
            body = " ".join(rng.choice(words) for _ in range(8))
            con.execute("INSERT INTO fts_items(rowid, body) VALUES (?, ?)", (i, body))
    insert_s = time.perf_counter() - t0
    print(f"Insert done in {insert_s:.1f}s ({args.n / insert_s:.0f} rows/s)")

    knn_lat, hybrid_lat = [], []
    for _ in range(args.queries):
        qv = pack(rand_vec(args.dim, rng))
        t0 = time.perf_counter()
        con.execute(
            "SELECT rowid, distance FROM vec_items WHERE embedding MATCH ? AND k = 10 ORDER BY distance",
            (qv,),
        ).fetchall()
        knn_lat.append((time.perf_counter() - t0) * 1000)

        term = rng.choice(words)
        t0 = time.perf_counter()
        # Prefilter via rowid-in-subquery rather than a JOIN: lets sqlite-vec
        # apply the FTS candidate set as a partition key on the KNN scan
        # instead of the planner materializing a full cross join (measured
        # >10s/query at n=5000 with the JOIN form; this form is the
        # documented sqlite-vec hybrid-search pattern).
        con.execute(
            """
            SELECT rowid, distance FROM vec_items
            WHERE embedding MATCH ? AND k = 10
              AND rowid IN (SELECT rowid FROM fts_items WHERE body MATCH ?)
            ORDER BY distance
            """,
            (qv, term),
        ).fetchall()
        hybrid_lat.append((time.perf_counter() - t0) * 1000)

    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "n": args.n, "dim": args.dim, "queries": args.queries,
        "insert_s": insert_s, "insert_rows_per_s": args.n / insert_s,
        "knn_p50_ms": percentile(knn_lat, 0.50), "knn_p95_ms": percentile(knn_lat, 0.95),
        "hybrid_p50_ms": percentile(hybrid_lat, 0.50), "hybrid_p95_ms": percentile(hybrid_lat, 0.95),
        "verdict": "PASS (sqlite-vec)" if percentile(knn_lat, 0.95) < 50 else "FAIL -> Qdrant",
    }
    out_path = OUTDIR / "sqlitevec_verdict.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
