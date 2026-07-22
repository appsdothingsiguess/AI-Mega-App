#!/bin/bash
# Watches for each pending download to land, runs its bench job, then waits
# for the next. One model at a time (GPU jobs must not overlap).
set -u
cd /home/john/AI-Mega-App
BLOBS=/home/john/llm-stack/models/blobs
LOG=/tmp/auto_bench_watcher.log

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

wait_for() {
    local f="$1"
    while [[ ! -f "$f" ]] || pgrep -f "wget.*$(basename "$f")" >/dev/null 2>&1; do
        sleep 15
    done
}

run_gpu_bench() {
    local label="$1" model="$2" cls="$3" ctx="$4" extra="$5" mmproj="${6:-}"
    log "RUN $label"
    bash scripts/bench_models.sh "$label" "$model" $extra >>"$LOG" 2>&1
    local sargs=(--label "$label" --model "$model" --model-class "$cls" --ctx "$ctx" --port 8899 --repeats 3 --n-predict 300)
    [[ -n "$extra" ]] && sargs+=($extra)
    [[ -n "$mmproj" ]] && sargs+=(--mmproj "$mmproj" --image "$model")
    python3 scripts/bench_server.py "${sargs[@]}" >>"$LOG" 2>&1
    log "DONE $label"
}

# Q6_K coder
wait_for "$BLOBS/Qwen3-Coder-30B-A3B-Instruct-Q6_K.gguf"
run_gpu_bench coder-Q6_K "$BLOBS/Qwen3-Coder-30B-A3B-Instruct-Q6_K.gguf" coder 32768 "--tensor-split 3,1"

# DeepSeek R1 re-fetch
wait_for "$BLOBS/DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf"
run_gpu_bench reasoner-r1-distill-32b-refetch "$BLOBS/DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf" reasoner 16384 "--tensor-split 3,1"

IMG=/home/john/llm-stack/ollama/test_files/vision_count.png

# Vision A: Qwen3-VL-32B
wait_for "$BLOBS/Qwen3-VL-32B-Instruct-Q4_K_M.gguf"
wait_for "$BLOBS/Qwen3-VL-32B-Instruct-mmproj-BF16.gguf"
log "RUN vision-qwen3vl-32b-q4"
bash scripts/bench_models.sh vision-qwen3vl-32b-q4 "$BLOBS/Qwen3-VL-32B-Instruct-Q4_K_M.gguf" --tensor-split 3,1 >>"$LOG" 2>&1
python3 scripts/bench_server.py --label vision-qwen3vl-32b-q4 --model "$BLOBS/Qwen3-VL-32B-Instruct-Q4_K_M.gguf" --model-class vision --ctx 16384 --tensor-split 3,1 --mmproj "$BLOBS/Qwen3-VL-32B-Instruct-mmproj-BF16.gguf" --image "$IMG" --prompt-file <(echo "How many objects are in this image?") >>"$LOG" 2>&1
log "DONE vision-qwen3vl-32b-q4"

# Vision B: gemma-3-27b
wait_for "$BLOBS/gemma-3-27b-it-Q4_K_M.gguf"
wait_for "$BLOBS/gemma-3-27b-it-mmproj-BF16.gguf"
log "RUN vision-gemma3-27b-q4"
bash scripts/bench_models.sh vision-gemma3-27b-q4 "$BLOBS/gemma-3-27b-it-Q4_K_M.gguf" --tensor-split 3,1 >>"$LOG" 2>&1
python3 scripts/bench_server.py --label vision-gemma3-27b-q4 --model "$BLOBS/gemma-3-27b-it-Q4_K_M.gguf" --model-class vision --ctx 16384 --tensor-split 3,1 --mmproj "$BLOBS/gemma-3-27b-it-mmproj-BF16.gguf" --image "$IMG" --prompt-file <(echo "How many objects are in this image?") >>"$LOG" 2>&1
log "DONE vision-gemma3-27b-q4"

# classifier
wait_for "$BLOBS/Qwen3-1.7B-Q8_0.gguf"
log "RUN classifier-qwen3-1.7b-q8-cpu"
python3 scripts/bench_server.py --label classifier-qwen3-1.7b-q8-cpu --model "$BLOBS/Qwen3-1.7B-Q8_0.gguf" --model-class classifier --ctx 4096 --device none --port 8899 --repeats 5 --n-predict 32 >>"$LOG" 2>&1
log "DONE classifier-qwen3-1.7b-q8-cpu"

# embed-B
wait_for "$BLOBS/nomic-embed-text-v2-moe.Q4_K_M.gguf"
log "RUN embed-nomic-v2-cpu"
python3 scripts/bench_server.py --label embed-nomic-v2-cpu --model "$BLOBS/nomic-embed-text-v2-moe.Q4_K_M.gguf" --model-class embed --device none --port 8899 --repeats 10 >>"$LOG" 2>&1
log "DONE embed-nomic-v2-cpu"

log "ALL PENDING BENCHMARKS COMPLETE"
