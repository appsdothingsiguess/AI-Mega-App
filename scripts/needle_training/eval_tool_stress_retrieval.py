#!/usr/bin/env python3
"""
Tool-registry stress test, retrieval-filtered variant. The raw stress test
(eval_tool_stress.py) hands Hammer all 13 overlapping-name tools at once and
measures a real ~25-point call_f1 drop vs. the narrow 6-tool baseline. Big
platforms don't hand a small dispatcher an ambiguous 13-tool registry either
-- they retrieve a small relevant candidate set first (via the embed model
already in the roster), then let the dispatcher choose among only those.

This script: embeds each of the 13 tool descriptions once, embeds every
query, cosine-ranks tools per query, keeps only the top --top-k as the
candidate set Hammer actually sees (same few-shot SYSTEM_PROMPT as
eval_tool_stress.py), and scores with the same call_f1/exact_match/
json_parse_rate metric for a direct, fair comparison against the
un-filtered stress number.

Also logs retrieval_hit (was the ground-truth tool even in the retrieved
subset?) as a diagnostic -- if retrieval itself misses the right tool, no
dispatcher downstream can recover it.

Usage:
  eval_tool_stress_retrieval.py --embed-model /path/embed.gguf --embed-device none \
      --hammer-model /path/Hammer2.1-1.5b.gguf --top-k 5 \
      [--embed-port 8899] [--hammer-port 8900]

Writes per-item + summary to
logs/benchmarks/server/tool-stress-retrieval-hammer.jsonl
"""
import argparse, json, math, os, signal, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_llm_tool_calling import extract_json_array, call_key, wait_healthy, http_json, log  # noqa: E402

BIN = "/home/john/llm-stack/engine/llama.cpp/build/bin/llama-server"
REPO = Path("/home/john/AI-Mega-App")
DATA = REPO / "scripts" / "needle_training" / "data_stress.jsonl"
OUTDIR = REPO / "logs" / "benchmarks" / "server"

SYSTEM_PROMPT = (
    "You are a function-calling dispatcher. Given a user query and a list of "
    "available tools, respond with ONLY a JSON array containing exactly one "
    "object: {\"name\": <tool_name>, \"arguments\": {<arg>: <value>, ...}}. "
    "Some tool names are similar -- pick the ONE that most precisely matches "
    "the user's request. Use only tools/arguments from the provided list. "
    "No explanation, no markdown fences, no <think> tags -- output the JSON "
    "array and nothing else.\n\n"
    "Worked examples of confusable-name disambiguation:\n\n"
    "Query: \"how big is app/main.py and when was it last modified?\"\n"
    "-> [{\"name\": \"read_file_metadata\", \"arguments\": {\"path\": \"app/main.py\"}}]\n"
    "(metadata-only request: size/mtime, not the contents -- NOT file_read)\n\n"
    "Query: \"show me lines 10 through 20 of config.yaml\"\n"
    "-> [{\"name\": \"file_read_lines\", \"arguments\": {\"path\": \"config.yaml\", \"start_line\": 10, \"end_line\": 20}}]\n"
    "(a specific line range -- NOT file_read, which is for the whole file)\n\n"
    "Query: \"find out the newest Rust release\"\n"
    "-> [{\"name\": \"web_search\", \"arguments\": {\"query\": \"newest Rust release\"}}]\n"
    "(general web lookup -- NOT web_search_news unless the query explicitly asks for news/articles)\n\n"
    "Query: \"any breaking news on the merger between the two chip companies?\"\n"
    "-> [{\"name\": \"web_search_news\", \"arguments\": {\"query\": \"merger between the two chip companies\"}}]\n"
    "(explicitly asks for news -- NOT plain web_search)\n\n"
    "Query: \"find images of the RTX 3090 GPU\"\n"
    "-> [{\"name\": \"web_search_images\", \"arguments\": {\"query\": \"RTX 3090 GPU\"}}]\n"
    "(explicitly asks for images -- NOT plain web_search)\n\n"
    "Query: \"get the raw HTML of https://example.com/api/status\"\n"
    "-> [{\"name\": \"fetch_url_raw\", \"arguments\": {\"url\": \"https://example.com/api/status\"}}]\n"
    "(asks for raw/unprocessed body -- NOT fetch_url, which returns cleaned text)\n"
)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def boot(cmd, env, port, boot_timeout, label):
    log(f"START {label}: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ok, err = wait_healthy(port, proc, boot_timeout)
    if not ok:
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log(f"FAIL {label}: {err}")
        sys.exit(3)
    log(f"HEALTHY {label}")
    return proc


def kill_proc(proc, reason, label):
    log(f"KILL {label}: {reason}")
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed-model", required=True)
    ap.add_argument("--embed-device", default=None, help="'none' to force CPU")
    ap.add_argument("--embed-port", type=int, default=8899)
    ap.add_argument("--hammer-model", required=True)
    ap.add_argument("--hammer-port", type=int, default=8900)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--boot-timeout", type=int, default=120)
    ap.add_argument("--request-timeout", type=int, default=60)
    ap.add_argument("--n-predict", type=int, default=200)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / "tool-stress-retrieval-hammer.jsonl"
    if jsonl_path.exists():
        jsonl_path.unlink()

    test = [json.loads(l) for l in DATA.read_text().splitlines() if l.strip()]
    log(f"Loaded {len(test)} stress examples")
    all_tools = json.loads(test[0]["tools"])
    log(f"Registry has {len(all_tools)} tools; retrieving top-{args.top_k} per query")

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = "/home/john/llm-stack/engine/llama.cpp/build/bin:" + env.get("LD_LIBRARY_PATH", "")

    # --- Phase 1: embed all tool descriptions + all queries while embed server is up ---
    embed_cmd = [BIN, "--port", str(args.embed_port), "-m", args.embed_model, "--ctx-size", "2048",
                 "-ngl", "999", "--embeddings", "--host", "127.0.0.1"]
    if args.embed_device:
        embed_cmd += ["--device", args.embed_device, "-ngl", "0"]
    embed_proc = boot(embed_cmd, env, args.embed_port, args.boot_timeout, "embed")

    def embed_text(text):
        body, _ = http_json(f"http://127.0.0.1:{args.embed_port}/v1/embeddings", {"input": text}, args.request_timeout)
        return body["data"][0]["embedding"]

    tool_vecs = []
    query_vecs = {}
    try:
        for t in all_tools:
            desc = f"{t['name']}: {t['description']}"
            tool_vecs.append((t, embed_text(desc)))
        log(f"Embedded {len(tool_vecs)} tool descriptions")
        for i, ex in enumerate(test):
            query_vecs[i] = embed_text(ex["query"])
        log(f"Embedded {len(query_vecs)} queries")
    finally:
        kill_proc(embed_proc, "embeddings done", "embed")

    # --- Phase 2: retrieve top-k tools per query ---
    retrieved = {}
    for i in range(len(test)):
        qvec = query_vecs[i]
        scored = sorted(
            ((cosine(qvec, tvec), t) for t, tvec in tool_vecs),
            key=lambda x: -x[0],
        )
        retrieved[i] = [t for _, t in scored[:args.top_k]]

    # --- Phase 3: run Hammer against only the retrieved subset per query ---
    hammer_cmd = [BIN, "--port", str(args.hammer_port), "-m", args.hammer_model, "--ctx-size", "4096",
                  "-ngl", "999", "--flash-attn", "on", "--jinja", "--host", "127.0.0.1", "--temp", "0"]
    hammer_proc = boot(hammer_cmd, env, args.hammer_port, args.boot_timeout, "hammer")

    tp_calls = fp_calls = fn_calls = 0
    exact = 0
    parse_ok = 0
    retrieval_hits = 0
    latencies = []

    try:
        for i, ex in enumerate(test):
            ref_calls = json.loads(ex["answers"])
            ref_name = ref_calls[0]["name"] if ref_calls else None
            candidate_tools = retrieved[i]
            retrieval_hit = any(t["name"] == ref_name for t in candidate_tools)
            if retrieval_hit:
                retrieval_hits += 1

            user_content = f"Tools:\n{json.dumps(candidate_tools)}\n\nQuery: {ex['query']}\n/no_think"
            payload = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": args.n_predict, "temperature": 0, "stream": False,
            }
            try:
                body, wall = http_json(f"http://127.0.0.1:{args.hammer_port}/v1/chat/completions", payload, args.request_timeout)
                out_text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
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
            tp_calls += len(pred_keys & ref_keys)
            fp_calls += len(pred_keys - ref_keys)
            fn_calls += len(ref_keys - pred_keys)
            is_exact = ref_keys == pred_keys and len(ref_keys) > 0
            if is_exact:
                exact += 1

            rec = {
                "ts": datetime.now(timezone.utc).isoformat(), "i": i, "query": ex["query"],
                "ref": ref_calls, "pred": pred_calls, "raw_output": out_text,
                "candidate_tool_names": [t["name"] for t in candidate_tools],
                "retrieval_hit": retrieval_hit, "latency_s": wall, "exact": is_exact,
            }
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            log(f"  [{i}] {'OK' if is_exact else 'WRONG'} retrieval_hit={retrieval_hit} "
                f"query={ex['query'][:50]!r} ref={ref_calls} pred={pred_calls}")
    finally:
        kill_proc(hammer_proc, "done", "hammer")

    n = len(test)
    precision = tp_calls / max(tp_calls + fp_calls, 1)
    recall = tp_calls / max(tp_calls + fn_calls, 1)
    call_f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    avg_lat = sum(latencies) / len(latencies) if latencies else None

    summary = {
        "label": "hammer-retrieval", "n_test": n, "top_k": args.top_k, "registry_size": len(all_tools),
        "retrieval_recall": round(retrieval_hits / n, 4),
        "call_f1": round(call_f1, 4), "exact_match": round(exact / max(n, 1), 4),
        "json_parse_rate": round(parse_ok / max(n, 1), 4),
        "avg_latency_s": round(avg_lat, 3) if avg_lat else None,
    }
    log(f"SUMMARY {json.dumps(summary)}")
    with open(jsonl_path, "a") as f:
        f.write(json.dumps({"event": "summary", **summary}) + "\n")


if __name__ == "__main__":
    main()
