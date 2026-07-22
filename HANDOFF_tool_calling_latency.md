# Handoff: Tool-calling latency bug (10s+ vs 1-3s)

## Symptom
User-observed: requests that don't need tool calls (routed to `general_chat`)
return in 1-3s. Requests that trigger tool calls (bash, web_search, pdf_gen,
file_ops) take 10+ seconds on the same 3090 box.

## Root cause (confirmed via live server + log capture, 2026-07-16)

`settings.json` has two separate model-alias maps that are supposed to stay in
sync but don't:

- `models.*` — maps intents to LiteLLM aliases (used for the actual chat
  completion call, via `litellm_config.yaml`). Correct.
- `ollama_model_names` — maps the same LiteLLM aliases to real Ollama tags
  (used by `app/model_scheduler.py`'s `ensure_loaded()` to warm/evict models
  directly against Ollama's `/api/generate`, for VRAM management).

`local/tool-calling-medium` (the alias behind `bash`, `web_search`,
`pdf_gen`, `file_ops`) has:

```json
"ollama_model_names": {
  "local/tool-calling-medium": ""
}
```

An **empty string**. `litellm_config.yaml` correctly points the same alias at
`ollama_chat/qwen3:8b-32k` for the completion call, but `ensure_loaded()`
never sees that — it resolves the ollama-side name to `""` and tries to warm
up a model literally named `""` against Ollama directly.

### What actually happens on every tool-routed turn (from `logs/prompter.log`)

```
prompter.scheduler WARNING Ollama warmup attempt 1 failed: 404 Not Found ... Retrying in 1.0s
prompter.scheduler WARNING Ollama warmup attempt 2 failed: 404 Not Found ... Retrying in 2.0s
prompter.llm INFO ensure_loaded_elapsed_ms=3044.9 ensure_loaded_swapped=False model=
```

~3 seconds wasted (1s + 2s exponential backoff) per `ensure_loaded()` call,
every time, because the warmup target is always an empty/invalid model name
and always 404s. This is not model/context cost — `prompt_eval_duration` and
raw Ollama load times were separately benchmarked (see below) and are not the
driver here.

This tax is paid **once per LLM iteration** in a turn. Multi-step tool-calling
turns (initial call → tool result → follow-up call, sometimes several rounds)
pay it repeatedly, which is why the compounded latency reaches 10s+ while
`general_chat` (routed to `local/qwen3-8b`, which *does* have a valid
`ollama_model_names` entry: `"qwen3:8b-32k"`) never hits this path and stays
at 1-3s.

### Scope — not just bash

Same empty-string bug exists for every other "virtual" alias in
`ollama_model_names`:

```
local/coding-light, local/coding-medium, local/coding-heavy,
local/reasoning-medium, local/reasoning-heavy,
local/vision-light, local/vision-medium, local/vision-heavy,
local/tool-calling-medium
```

All `""`. Any intent routed to these pays the same ~3s tax per iteration —
this isn't unique to bash/tool-calling, tool-calling intents were just the
ones the user happened to be exercising.

Also present as the same broken default in `app/config.py` (line ~163) — so a
fresh install without a customized `settings.json` would hit this too.

## The fix (NOT applied — read-only diagnosis session)

Populate the empty `ollama_model_names` entries in `settings.json` to match
their real `litellm_config.yaml` targets, e.g.:

```json
"local/tool-calling-medium": "qwen3:8b-32k"
```

Repeat for the other empty aliases above, matching whatever real Ollama tag
`litellm_config.yaml` already resolves each one to (see that file for the
authoritative mapping — e.g. `local/coding-light` → check its
`litellm_params.model` entry, etc.). Once populated, `ensure_loaded()` will
correctly recognize the model as already resident (or load the right one)
instead of always 404ing on `""`.

Consider also fixing the default in `app/config.py` so a fresh
`settings.json` doesn't regenerate the same bug.

## Supporting benchmark data (raw Ollama, bypassing the app)

Ran directly against Ollama's `/api/generate` to rule out inherent
model/context cost as an alternative explanation:

- `prompt_eval_duration` stayed under ~170ms even with a full system prompt +
  tool-schema appendix (~300 tokens) — prompt size is not the bottleneck.
- Cold model load is real but separate: `qwen3-coder:30b-24k` costs ~10.1-10.2s
  `load_duration` cold, `qwen3:8b-32k` ~3.6s cold. Once warm, models stay
  resident across repeated calls on this 24GB 3090 (19GB + 1.7GB coexist
  fine, no eviction observed across 3 back-to-back calls).
- `qwen3:8b` independently burns 300-700+ completion tokens of `eval_ms`
  (2-5s) on trivial prompts due to reasoning/thinking-token behavior — a
  known, separate cost driver, not related to the alias bug above.

None of this explains the tool-call-specific 10s+ pattern by itself — the
`ensure_loaded_elapsed_ms=3044.9` / empty-model-name 404 loop is the specific,
reproducible cause of the tool-vs-no-tool latency gap.

## Environment notes for next session

- System Python is 3.14; litellm's Rust extension (pyo3) only supports up to
  3.13. Venv must be built with `uv venv --python 3.12` (already installed
  via `uv python install 3.12`), not system Python.
- `.venv` at repo root now has all deps installed (`uv pip install -e ".[dev]"`
  succeeded under Python 3.12).
- Test project `latency_test` / thread `latency-bench` created under
  `projects/latency_test/` for reproducing this — reusable for further
  testing.
- Server startable via `python -m app.main serve --no-browser` from repo root
  with `.venv` activated. Logs to `logs/prompter.log`.
- No code was modified this session — diagnosis only.
