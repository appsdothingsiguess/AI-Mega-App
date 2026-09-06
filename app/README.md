# Surviving service modules

The active `app/` package now contains the service-side configuration and GPU
modules used by operational scripts:

- `config.py` — checked-in service configuration loading;
- `gpu/` — GPU discovery and swap generation.

The retired chat/UI harness is preserved under `retired/app/`. The inference
engine remains outside this repository at `/home/john/llm-stack/engine`.
