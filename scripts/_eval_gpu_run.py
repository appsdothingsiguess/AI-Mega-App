#!/usr/bin/env python3
"""Ad hoc wrapper: eval classifier on GPU/VRAM.

Patches CLASSIFIER_OLLAMA_OPTIONS in the eval harness only — does not touch
app/adapters/classifier_qwen.py (production stays CPU num_gpu=0).

Defaults for this compare path: num_gpu=999, num_ctx=4096, num_predict=250.

Usage: python scripts/_eval_gpu_run.py --ids a,b,c --models qwen2.5:3b --variant local
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.eval_classifier as ec  # noqa: E402

ec.CLASSIFIER_OLLAMA_OPTIONS = dict(ec.CLASSIFIER_OLLAMA_OPTIONS)
ec.CLASSIFIER_OLLAMA_OPTIONS["num_gpu"] = 999
ec.CLASSIFIER_OLLAMA_OPTIONS["num_ctx"] = 4096
ec.CLASSIFIER_OLLAMA_OPTIONS["num_predict"] = 250

if __name__ == "__main__":
    raise SystemExit(ec.main())
