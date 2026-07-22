#!/usr/bin/env bash
# Throughput benchmark wrapper around llama-bench.
# Usage: bench_models.sh <label> <gguf-path> [extra llama-bench args...]
# Logs raw JSON to logs/benchmarks/throughput/<label>.json and appends a
# summary row to logs/benchmarks/throughput_summary.csv
set -euo pipefail

BIN=/home/john/llm-stack/engine/llama.cpp/build/bin
REPO=/home/john/AI-Mega-App
OUTDIR="$REPO/logs/benchmarks/throughput"
SUMMARY="$REPO/logs/benchmarks/throughput_summary.csv"
mkdir -p "$OUTDIR"

LABEL="${1:?usage: bench_models.sh <label> <gguf-path> [extra args]}"
MODEL="${2:?usage: bench_models.sh <label> <gguf-path> [extra args]}"
shift 2
EXTRA_ARGS=("$@")

if [[ ! -f "$MODEL" ]]; then
  echo "FAIL $LABEL: model file not found: $MODEL" >&2
  exit 2
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
JSONOUT="$OUTDIR/${LABEL}_${TS}.json"

export LD_LIBRARY_PATH="$BIN:${LD_LIBRARY_PATH:-}"

# 120s hard timeout on the whole llama-bench invocation; a stuck load/bench
# gets SIGKILL rather than hanging the suite.
timeout -k 10 300 "$BIN/llama-bench" \
      -m "$MODEL" \
      -ngl 999 \
      -p 512 -n 128 \
      -o json \
      "${EXTRA_ARGS[@]}" \
      > "$JSONOUT" 2> "${JSONOUT%.json}.stderr.log"
RC=$?
if [[ $RC -ne 0 ]]; then
  echo "FAIL $LABEL (rc=$RC) — see ${JSONOUT%.json}.stderr.log" >&2
  echo "$TS,$LABEL,$MODEL,FAIL,,,," >> "$SUMMARY"
  exit "$RC"
fi

# Pull prompt/gen tok-s out of the JSON (llama-bench -o json emits an array
# of {n_prompt,n_gen,avg_ts,...} rows — one for pp, one for tg).
PP_TS=$(python3 -c "
import json,sys
d=json.load(open('$JSONOUT'))
rows=[r for r in d if r.get('n_prompt',0)>0]
print(rows[0]['avg_ts'] if rows else '')
" 2>/dev/null || echo "")
TG_TS=$(python3 -c "
import json,sys
d=json.load(open('$JSONOUT'))
rows=[r for r in d if r.get('n_gen',0)>0]
print(rows[0]['avg_ts'] if rows else '')
" 2>/dev/null || echo "")

SIZE_BYTES=$(stat -c%s "$MODEL")
echo "$TS,$LABEL,$MODEL,OK,$PP_TS,$TG_TS,$SIZE_BYTES" >> "$SUMMARY"
echo "OK $LABEL: prompt=${PP_TS} tok/s gen=${TG_TS} tok/s -> $JSONOUT"
