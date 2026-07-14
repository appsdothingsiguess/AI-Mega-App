# Backend setup: routing to the RTX 3090 Ollama box

**Audience:** anyone running Prompter and wondering why chat requests work without OpenCode Go
keys, or where the actual model inference happens.

## What changed

Prompter's router (`app/router.py`, `app/config.py`, `app/litellm_resolver.py`) is unchanged
code-wise — this was a **configuration-only** change. All 9 routed intents
(`general_chat, web_search, deep_research, coding_basic, coding_advanced, bash, pdf_gen,
file_ops, vision`) now resolve to local models served by Ollama on a dedicated LAN box
(`192.168.0.240:11434`, RTX 3090, see `~/llm-stack/ollama/CLAUDE.md` on that machine), instead of
the OpenCode Go placeholders (`remote/deepseek-v4-pro`, `remote/kimi-k2-6`).

No `OPENCODE_API_KEY` is required for the default routing to work anymore — remote OpenCode
aliases are still defined in `litellm_config.yaml` and can be pointed at from `settings.json` for
any intent that should use a remote model instead.

## How to use it

1. **No setup needed on the app side** if the Ollama box is reachable — `litellm_config.yaml`'s
   `api_base: http://192.168.0.240:11434` and `app/config.py`'s `OllamaSettings.base_url` already
   point there.
2. **Check the backend is up** before debugging "empty response" issues:
   ```bash
   curl -s http://192.168.0.240:11434/api/tags | jq -r '.models[].name'
   ```
   If this fails, the Ollama box's `ollama.service` (systemd) may be down — see
   `~/llm-stack/ollama/CLAUDE.md` on that host for recovery steps (a manually-started
   `ollama serve` process fighting the systemd unit for the port is the most common cause).
3. **Override the model per-intent** via `settings.json` (not code) — any alias defined in
   `litellm_config.yaml`'s `model_list` works, local or remote:
   ```json
   { "models": { "coding_advanced": "remote/deepseek-v4-pro" } }
   ```
4. **The classifier** (`local/qwen2.5:1.5b-32k` equivalent, hit directly via `/api/generate`, not
   through `litellm_config.yaml`) always runs with `num_gpu: 0` — it's pinned CPU-only so it never
   competes with task models for the 3090's VRAM. ~1.3s average latency.
5. **Adding a new local model**: pull/create the Ollama tag on the 3090 box, add a `model_list`
   entry in `litellm_config.yaml` (`ollama_chat/<tag>` — not bare `ollama/`, which breaks
   tool-calling — plus the same `api_base`), then point the relevant intent at the new alias in
   `settings.json`.

## Where things live

| Concern | File |
|---|---|
| Intent → alias defaults | `app/config.py` (`ModelsConfig`) |
| Alias → actual model/host | `litellm_config.yaml` (`model_list`) |
| Classifier model + CPU pin | `app/adapters/classifier_qwen.py` |
| Keyword fast-path rules (skip classifier entirely) | `app/config.py` (`RouterSettings.rules`) |
| Ollama box itself (systemd, GPU pinning, tags) | `~/llm-stack/ollama/CLAUDE.md` (separate machine) |
