#!/usr/bin/env bash
# Master Phase-0 benchmark runner (docs/BENCHMARK_PLAN.md). Ordered smallest
# model -> largest so cheap/fast jobs report first and GPU-heavy jobs run
# last. Every job has its own timeout and each job's server process is torn
# down before the next starts (bench_server.py always kills its llama-server
# / needle server on exit, success or failure) -- no model stays resident
# across jobs. Progress is written to logs/benchmarks/suite_progress.log.
set -uo pipefail

# Optional: restrict to specific classes. Usage:
#   run_benchmark_suite.sh                        # everything (default)
#   run_benchmark_suite.sh needle,embed            # only these classes
#   ONLY=needle,embed run_benchmark_suite.sh       # same, via env var
# Valid classes: needle embed classifier utility coder reasoner chat-default vision
ONLY="${ONLY:-${1:-}}"
want_class() {
  [[ -z "$ONLY" ]] && return 0
  [[ ",${ONLY}," == *",$1,"* ]]
}

REPO=/home/john/AI-Mega-App
GGUF=/home/john/llm-stack/models/gguf
BLOBS=/home/john/llm-stack/models/blobs
PROGRESS="$REPO/logs/benchmarks/suite_progress.log"
IMAGE_COUNT=/home/john/llm-stack/ollama/test_files/vision_count.png
mkdir -p "$(dirname "$PROGRESS")"

note() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$PROGRESS"; }

run_job() {
  # run_job <timeout_s> <cmd...>
  local to="$1"; shift
  note "RUN (timeout ${to}s): $*"
  if timeout -k 15 "$to" "$@"; then
    note "OK: $*"
  else
    note "FAIL/TIMEOUT (rc=$?): $*"
  fi
}

note "=== SUITE START ===${ONLY:+ (ONLY=$ONLY)}"

########################################
# needle (26M) -- own runtime (JAX, now CUDA-enabled -- see
# engine/needle/.venv), not llama.cpp (explicit sign-off, docs/BENCHMARK_PLAN.md
# §2). Own /generate HTTP API, same analytics schema as bench_server.py.
# Smallest model in the matrix -- runs first.
########################################
if want_class needle; then
  if [[ -x /home/john/llm-stack/engine/needle/.venv/bin/python && -f /home/john/llm-stack/engine/needle/checkpoints/needle.pkl ]]; then
    run_job 120 python3 scripts/bench_needle.py --label needle-26m-cpu --repeats 10
  else
    note "SKIP needle: engine/needle venv or checkpoint missing"
  fi
fi

########################################
# embed (~275-305MB GGUF) -- CPU-resident by design, this is the §5 CPU
# placement gate, not a "should it be on CPU" question like utility.
########################################
if want_class embed; then
  run_job 180 python3 scripts/bench_server.py --label embed-nomic-v1-cpu \
    --model "$GGUF/nomic-embed-text-latest.gguf" --model-class embed \
    --device none --port 8899 --repeats 10 --boot-timeout 120
  if [[ -f "$BLOBS/nomic-embed-text-v2-moe.Q4_K_M.gguf" ]]; then
    run_job 180 python3 scripts/bench_server.py --label embed-nomic-v2-cpu \
      --model "$BLOBS/nomic-embed-text-v2-moe.Q4_K_M.gguf" --model-class embed \
      --device none --port 8899 --repeats 10 --boot-timeout 120
  else
    note "SKIP embed-nomic-v2-cpu: $BLOBS/nomic-embed-text-v2-moe.Q4_K_M.gguf not present"
  fi
fi

########################################
# classifier (~1.8GB Q8) -- CPU per plan (small model, short output)
########################################
if want_class classifier; then
  if [[ -f "$BLOBS/Qwen3-1.7B-Q8_0.gguf" ]]; then
    run_job 180 python3 scripts/bench_server.py --label classifier-qwen3-1.7b-q8-cpu \
      --model "$BLOBS/Qwen3-1.7B-Q8_0.gguf" --model-class classifier --ctx 4096 \
      --device none --port 8899 --repeats 5 --n-predict 32 --boot-timeout 120
  else
    note "SKIP classifier-qwen3-1.7b-q8-cpu: $BLOBS/Qwen3-1.7B-Q8_0.gguf not present"
  fi
fi

########################################
# utility (Qwen3-8B, ~5GB) -- GPU only. An 8B model has no business being
# CPU-bound; the CPU throughput bench already timed out at 180s on the
# 512p/128g pass (logs/benchmarks/throughput_summary.csv, label
# utility-qwen3-8b-cpu) -- that result stands as the Config-A-disqualifying
# data point for this class, no need to keep re-running it on CPU.
########################################
if want_class utility; then
  run_job 180 bash scripts/bench_models.sh utility-qwen3-8b-gpu "$GGUF/qwen3-8b.gguf"
  run_job 180 python3 scripts/bench_server.py --label utility-qwen3-8b-gpu \
    --model "$GGUF/qwen3-8b.gguf" --model-class utility --ctx 8192 \
    --port 8899 --repeats 3 --n-predict 128
fi

########################################
# coder (MoE, 30B total/3B active -- fast despite size; three quants,
# adopt highest that clears the >=100 tok/s bar)
########################################
if want_class coder; then
  run_job 300 bash scripts/bench_models.sh coder-q4-existing "$GGUF/qwen3-coder-30b-a3b-q4_k_m.gguf" --tensor-split 3,1
  run_job 240 python3 scripts/bench_server.py --label coder-q4-existing \
    --model "$GGUF/qwen3-coder-30b-a3b-q4_k_m.gguf" --model-class coder \
    --ctx 32768 --tensor-split 3,1 --port 8899 --repeats 3 --n-predict 300

  for q in Q4_K_M Q5_K_M Q6_K; do
    f="$BLOBS/Qwen3-Coder-30B-A3B-Instruct-${q}.gguf"
    if [[ -f "$f" ]]; then
      run_job 300 bash scripts/bench_models.sh "coder-${q}" "$f" --tensor-split 3,1
      run_job 240 python3 scripts/bench_server.py --label "coder-${q}" \
        --model "$f" --model-class coder --ctx 32768 --tensor-split 3,1 \
        --port 8899 --repeats 3 --n-predict 300
    else
      note "SKIP coder-${q}: $f not present"
    fi
  done
fi

########################################
# reasoner A vs B (dense 32B ~19.8GB / MoE 35B-A3B ~22GB)
########################################
if want_class reasoner; then
  run_job 300 bash scripts/bench_models.sh reasoner-r1distill-32b-existing "$GGUF/deepseek-r1-32b.gguf" --tensor-split 3,1
  run_job 240 python3 scripts/bench_server.py --label reasoner-r1distill-32b-existing \
    --model "$GGUF/deepseek-r1-32b.gguf" --model-class reasoner --ctx 16384 \
    --tensor-split 3,1 --port 8899 --repeats 3 --n-predict 400

  path="$BLOBS/DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf"
  if [[ -f "$path" ]]; then
    run_job 300 bash scripts/bench_models.sh reasoner-r1-distill-32b "$path" --tensor-split 3,1
    run_job 240 python3 scripts/bench_server.py --label reasoner-r1-distill-32b \
      --model "$path" --model-class reasoner --ctx 16384 --tensor-split 3,1 \
      --port 8899 --repeats 3 --n-predict 400
  else
    note "SKIP reasoner-r1-distill-32b: $path not present"
  fi

  # reasoner B: same Qwen3.6-35B-A3B weights fetched for chat-default, tested
  # via a reasoning prompt -- it natively supports think/no-think switching,
  # so this IS the "Qwen3.6-thinking / QwQ-successor MoE" candidate from the
  # plan, not a separate dense Qwen3-32B download (that's plan-skip-listed).
  path="$BLOBS/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
  if [[ -f "$path" ]]; then
    run_job 240 python3 scripts/bench_server.py --label reasoner-qwen3.6-35b-a3b-thinking \
      --model "$path" --model-class reasoner --ctx 16384 --tensor-split 3,1 \
      --port 8899 --repeats 3 --n-predict 400
  else
    note "SKIP reasoner-qwen3.6-35b-a3b-thinking: $path not present"
  fi
fi

########################################
# chat-default (MoE 35B-A3B, ~22GB)
########################################
if want_class chat-default; then
  if [[ -f "$BLOBS/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" ]]; then
    run_job 300 bash scripts/bench_models.sh chat-default-q4 "$BLOBS/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" --tensor-split 3,1
    run_job 240 python3 scripts/bench_server.py --label chat-default-q4 \
      --model "$BLOBS/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" --model-class chat-default \
      --ctx 32768 --tensor-split 3,1 --port 8899 --repeats 3 --n-predict 300
  else
    note "SKIP chat-default-q4: $BLOBS/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf not present"
  fi
fi

########################################
# vision A vs B (dense 32B/27B, ~17-19GB each -- largest, run last)
########################################
if want_class vision; then
  if [[ -f "$BLOBS/Qwen3-VL-32B-Instruct-Q4_K_M.gguf" ]]; then
    run_job 300 bash scripts/bench_models.sh vision-qwen3vl-32b-q4 "$BLOBS/Qwen3-VL-32B-Instruct-Q4_K_M.gguf" --tensor-split 3,1
    run_job 240 python3 scripts/bench_server.py --label vision-qwen3vl-32b-q4 \
      --model "$BLOBS/Qwen3-VL-32B-Instruct-Q4_K_M.gguf" --model-class vision --ctx 16384 \
      --tensor-split 3,1 --mmproj "$BLOBS/Qwen3-VL-32B-Instruct-mmproj-BF16.gguf" \
      --image "$IMAGE_COUNT" --port 8899 --repeats 2 --n-predict 200 \
      --prompt-file <(echo "How many objects are in this image? Answer in one sentence.")
  else
    note "SKIP vision-qwen3vl-32b-q4: $BLOBS/Qwen3-VL-32B-Instruct-Q4_K_M.gguf not present"
  fi
  if [[ -f "$BLOBS/gemma-3-27b-it-Q4_K_M.gguf" ]]; then
    run_job 300 bash scripts/bench_models.sh vision-gemma3-27b-q4 "$BLOBS/gemma-3-27b-it-Q4_K_M.gguf" --tensor-split 3,1
    run_job 240 python3 scripts/bench_server.py --label vision-gemma3-27b-q4 \
      --model "$BLOBS/gemma-3-27b-it-Q4_K_M.gguf" --model-class vision --ctx 16384 \
      --tensor-split 3,1 --mmproj "$BLOBS/gemma-3-27b-it-mmproj-BF16.gguf" \
      --image "$IMAGE_COUNT" --port 8899 --repeats 2 --n-predict 200 \
      --prompt-file <(echo "How many objects are in this image? Answer in one sentence.")
  else
    note "SKIP vision-gemma3-27b-q4: $BLOBS/gemma-3-27b-it-Q4_K_M.gguf not present"
  fi
fi

note "=== SUITE DONE ==="
