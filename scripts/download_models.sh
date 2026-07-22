#!/usr/bin/env bash
# Fetches every GGUF the Phase-0 test matrix (docs/BENCHMARK_PLAN.md §2) needs
# that isn't already on the models mount. wget -c so a re-run resumes safely.
set -uo pipefail

BLOBS=/home/john/llm-stack/models/blobs
LOG=/home/john/AI-Mega-App/logs/benchmarks/download_models.log
mkdir -p "$BLOBS" "$(dirname "$LOG")"
cd "$BLOBS"

BASE=https://huggingface.co

fetch() {
  local repo="$1" file="$2" out="$3"
  if [[ -f "$out" ]]; then
    echo "SKIP (exists) $out"
    return 0
  fi
  echo "FETCH $out <- $repo/$file"
  wget -c -O "$out" "$BASE/$repo/resolve/main/$file"
}

# --- chat-default (MoE, re-download: file vanished from disk) ---
fetch unsloth/Qwen3.6-35B-A3B-GGUF Qwen3.6-35B-A3B-UD-Q4_K_M.gguf Qwen3.6-35B-A3B-UD-Q4_K_M.gguf

# --- coder (MoE, three quants for the adopt-highest-that-clears-bar test) ---
fetch unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf
fetch unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf
fetch unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF Qwen3-Coder-30B-A3B-Instruct-Q6_K.gguf   Qwen3-Coder-30B-A3B-Instruct-Q6_K.gguf

# --- reasoner A: dense R1-distill ---
fetch unsloth/DeepSeek-R1-Distill-Qwen-32B-GGUF DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf

# --- reasoner B: NOT a separate download. Qwen3.6-35B-A3B (fetched above
# for chat-default) natively supports think/no-think mode switching -- it IS
# the "Qwen3.6-thinking / QwQ-successor MoE" reasoner-B candidate from
# BENCHMARK_PLAN.md §2, tested via a reasoning prompt against the same file.
# Qwen3-32B dense is explicitly on the plan's skip list (§2 line 65: already
# measured, dominated by the 35B-A3B MoE) -- do not fetch it.

# --- vision A: Qwen3-VL-32B + mmproj ---
fetch unsloth/Qwen3-VL-32B-Instruct-GGUF Qwen3-VL-32B-Instruct-Q4_K_M.gguf Qwen3-VL-32B-Instruct-Q4_K_M.gguf
fetch unsloth/Qwen3-VL-32B-Instruct-GGUF mmproj-BF16.gguf Qwen3-VL-32B-Instruct-mmproj-BF16.gguf

# --- vision B: Gemma3-27B + mmproj ---
fetch unsloth/gemma-3-27b-it-GGUF gemma-3-27b-it-Q4_K_M.gguf gemma-3-27b-it-Q4_K_M.gguf
fetch unsloth/gemma-3-27b-it-GGUF mmproj-BF16.gguf gemma-3-27b-it-mmproj-BF16.gguf

# --- classifier: Qwen3-1.7B Q8 ---
fetch unsloth/Qwen3-1.7B-GGUF Qwen3-1.7B-Q8_0.gguf Qwen3-1.7B-Q8_0.gguf

# --- embed B: nomic-embed-text-v2-moe (compare vs existing v1.5 ollama blob) ---
fetch nomic-ai/nomic-embed-text-v2-moe-GGUF nomic-embed-text-v2-moe.Q4_K_M.gguf nomic-embed-text-v2-moe.Q4_K_M.gguf

# needle: Cactus-Compute/needle ships only safetensors + a proprietary
# "needle-cq4.zip" Cactus-runtime format, no upstream GGUF and a custom
# (non-llama.cpp) architecture. Not fetchable for a llama.cpp-only harness --
# left out deliberately, see docs/phase0-measurements.md gap note.

echo DOWNLOAD_ALL_DONE | tee -a "$LOG"
