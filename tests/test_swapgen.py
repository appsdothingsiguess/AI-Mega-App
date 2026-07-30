"""Golden-file and property tests for app/gpu/swapgen.generate().

Golden test: generate(real config.yaml) must be byte-identical to GOLDEN.
This makes any unexpected change to flag order, flag spelling, group
membership, or deduplication logic an immediate test failure.

Property tests cover the Phase-0 acceptance criteria enumerated in swapgen.py:
  1. groups: block always present, correct membership
  2. No --tensor-split anywhere in output
  3. --reasoning off on every reasoning_off:true alias
  4. CUDA_VISIBLE_DEVICES="" on every CPU-placed entry
  5. --embedding (singular) on embed class
  6. --mmproj present for models with mmproj configured
  7. Disabled and file-deduped models absent
"""

from __future__ import annotations

import pytest

from app.config import load_config
from app.gpu.swapgen import generate

# ---------------------------------------------------------------------------
# Golden YAML for the current checked-in config.yaml roster.
# Trace: models emitted in config list order after dedup+enabled filter.
#   chat-default  (gpu=0, resident, ttl:0)
#   coder         (gpu=0, no ttl)
#   coder-small   (gpu=0, no ttl)
#   vision        (gpu=0, mmproj, no ttl)
#   dispatcher    (gpu=1, resident, ttl:0, --temp 0)
#   utility       (gpu=cpu, resident, ttl:0)
#   embed         (gpu=cpu, resident, ttl:0, --embedding)
#   classifier    (gpu=cpu, resident, ttl:0, --reasoning off, --temp 0)
# Dropped: reasoner (same file as chat-default, which is resident — request-
# layer thinking, not a swap entry); reasoner-alt (enabled:false).
# ---------------------------------------------------------------------------
_BASE = "/home/john/llm-stack/models/gguf"
_BIN = "/home/john/llm-stack/engine/llama.cpp/build/bin/llama-server"

GOLDEN = f"""\
# generated — do not hand-edit
healthCheckTimeout: 120
macros:
  llama: {_BIN} --host 0.0.0.0 --port ${{PORT}} --jinja
models:
  chat-default:
    cmd: ${{llama}} -m {_BASE}/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf -ngl 999 -c 32768
    env: ["CUDA_VISIBLE_DEVICES=0"]
    ttl: 0
  coder:
    cmd: ${{llama}} -m {_BASE}/Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf -ngl 999 -c 16384
    env: ["CUDA_VISIBLE_DEVICES=0"]
  coder-small:
    cmd: ${{llama}} -m {_BASE}/qwen2.5-coder-7b.gguf -ngl 999 -c 8192
    env: ["CUDA_VISIBLE_DEVICES=0"]
  vision:
    cmd: ${{llama}} -m {_BASE}/Qwen3-VL-32B-Instruct-Q4_K_M.gguf -ngl 999 -c 8192 --mmproj {_BASE}/Qwen3-VL-32B-Instruct-mmproj-BF16.gguf
    env: ["CUDA_VISIBLE_DEVICES=0"]
  dispatcher:
    cmd: ${{llama}} -m {_BASE}/Hammer2.1-1.5b-Q4_K_M.gguf -ngl 999 -c 4096 --temp 0
    env: ["CUDA_VISIBLE_DEVICES=1"]
    ttl: 0
  utility:
    cmd: ${{llama}} -m {_BASE}/qwen3-8b.gguf --device none -ngl 0 -c 8192
    env: ["CUDA_VISIBLE_DEVICES="]
    ttl: 0
  embed:
    cmd: ${{llama}} -m {_BASE}/nomic-embed-text-v2-moe.Q4_K_M.gguf --device none -ngl 0 --embedding -c 2048
    env: ["CUDA_VISIBLE_DEVICES="]
    ttl: 0
  classifier:
    cmd: ${{llama}} -m {_BASE}/Qwen3-1.7B-Q8_0.gguf --device none -ngl 0 -c 4096 --reasoning off --temp 0
    env: ["CUDA_VISIBLE_DEVICES="]
    ttl: 0
groups:
  resident: {{ swap: false, exclusive: false, members: [dispatcher, utility, embed, classifier] }}
  gpu0-main: {{ swap: true, members: [chat-default, coder, coder-small, vision] }}
"""


@pytest.fixture(scope="module")
def real_config():
    return load_config()


@pytest.fixture(scope="module")
def generated(real_config):
    return generate(real_config)


# ---------------------------------------------------------------------------
# Golden byte-equality test
# ---------------------------------------------------------------------------

def test_golden(generated):
    """generate() output must be byte-identical to GOLDEN for a stable config."""
    assert generated == GOLDEN


# ---------------------------------------------------------------------------
# Phase-0 acceptance criteria (each was a real observed defect)
# ---------------------------------------------------------------------------

def test_groups_block_present(generated):
    """Defect #1: groups: block must always be emitted."""
    assert "groups:" in generated
    assert "resident:" in generated
    assert "gpu0-main:" in generated


def test_no_tensor_split(generated):
    """Defect #2: --tensor-split must never appear (measured ~3x slower, PLAN §4.1)."""
    assert "--tensor-split" not in generated


def test_reasoning_off_on_classifier(generated):
    """Defect #3: --reasoning off required for classifier (91.76% accuracy needs it)."""
    lines = generated.splitlines()
    in_classifier = False
    for line in lines:
        if line.strip().startswith("classifier:"):
            in_classifier = True
        if in_classifier and line.strip().startswith("cmd:"):
            assert "--reasoning off" in line
            break


def test_cuda_visible_devices_empty_on_cpu(generated):
    """Defect #4: CUDA_VISIBLE_DEVICES="" on CPU-placed entries reclaims ~150-256 MB."""
    lines = generated.splitlines()
    cpu_entry_active = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("cmd:") and "--device none" in stripped:
            cpu_entry_active = True
        if cpu_entry_active and stripped.startswith("env:"):
            assert 'CUDA_VISIBLE_DEVICES="' in stripped or "CUDA_VISIBLE_DEVICES=" in stripped
            # Must be the empty-value form
            assert 'CUDA_VISIBLE_DEVICES="]' in stripped
            cpu_entry_active = False


def test_embedding_flag_singular(generated):
    """--embedding (singular) on embed class — not --embeddings (PLAN §4.1 sample)."""
    assert "--embedding " in generated or generated.endswith("--embedding")
    assert "--embeddings" not in generated


def test_mmproj_on_vision(generated):
    """--mmproj present for vision model which has mmproj configured."""
    lines = generated.splitlines()
    in_vision = False
    for line in lines:
        if line.strip().startswith("vision:"):
            in_vision = True
        if in_vision and line.strip().startswith("cmd:"):
            assert "--mmproj" in line
            break


def test_reasoner_deduped(generated):
    """reasoner shares chat-default's file; only chat-default is emitted as a swap entry."""
    lines = generated.splitlines()
    model_keys = [l.strip().rstrip(":") for l in lines if l.startswith("  ") and l.strip().endswith(":") and not l.startswith("   ")]
    assert "reasoner" not in model_keys
    assert "chat-default" in model_keys


def test_reasoner_alt_disabled(generated):
    """reasoner-alt is disabled in config.yaml; must not appear in output."""
    assert "reasoner-alt" not in generated


def test_resident_group_membership(generated):
    """resident group: CPU + GPU1 residents (dispatcher, utility, embed, classifier)."""
    for name in ["dispatcher", "utility", "embed", "classifier"]:
        assert name in generated.split("resident:")[1].split("\n")[0]


def test_gpu0_main_membership(generated):
    """gpu0-main group: all enabled, deduplicated GPU0 models."""
    gpu0_line = [l for l in generated.splitlines() if "gpu0-main:" in l][0]
    for name in ["chat-default", "coder", "coder-small", "vision"]:
        assert name in gpu0_line
    assert "reasoner" not in gpu0_line
