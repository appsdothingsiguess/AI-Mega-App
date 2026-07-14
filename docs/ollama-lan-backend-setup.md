# Backend setup: Ollama LAN box

Prompter routes local model aliases through **LiteLLM** to an Ollama host on the LAN (default `http://192.168.0.240:11434`, RTX 3090). Remote aliases use **OpenCode Go** and need `OPENCODE_API_KEY` in `.env`.

No app-code change is required if that host is reachable — `litellm_config.yaml` already sets `api_base` for `local/*` aliases.

## Quick check

```bash
curl -s http://192.168.0.240:11434/api/tags
python -m app.main health
# or with the API up:
curl http://127.0.0.1:8000/health
```

If `curl` to the box fails, fix LAN/Ollama first (often `ollama.service` down, or a stray `ollama serve` fighting the systemd unit for the port). Host-side ops: `llm-stack/ollama/CLAUDE.md` on that machine.

## Config map

| Concern | Where |
|---------|--------|
| Intent → model alias | `settings.json` / `app/config.py` (`ModelsConfig`) |
| Alias → provider + host | `litellm_config.yaml` (`model_list`, `api_base`) |
| Runtime override (URL, keep-alive, scheduler) | Web UI ⚙ → **Infrastructure** → writes `settings.json` |
| Classifier (CPU-only, `num_gpu: 0`) | `app/adapters/classifier_qwen.py` |
| Keyword fast-path (skip classifier) | `RouterSettings.rules` in settings / `app/config.py` |

## Change the Ollama host

1. Edit `api_base` under each `local/*` entry in `litellm_config.yaml`, **or**
2. Set the Ollama base URL in Settings → Infrastructure (persists to `settings.json`).

```bash
# See current bases
grep -A2 "api_base" litellm_config.yaml
```

Use LiteLLM’s `ollama_chat/<tag>` (not bare `ollama/`) so tool-calling works.

## Per-intent model override

Any alias in `litellm_config.yaml` works. Example — send advanced coding to OpenCode Go:

```json
{ "models": { "coding_advanced": "remote/deepseek-v4-pro" } }
```

Remote intents need `OPENCODE_API_KEY` in `.env`. Defaults can be local-only; see the handoff note below if your `settings.json` already points everything at the LAN box.

## Add a local model

1. Pull/create the tag on the Ollama box.
2. Add a `model_list` entry in `litellm_config.yaml` (`model: ollama_chat/<tag>`, same `api_base`).
3. Point the intent at the new alias in `settings.json` (or Settings → Models).

## Classifier

The intent classifier hits Ollama directly (`/api/generate`), pinned **CPU-only** so it never competes with task models for VRAM. On timeout/failure the router falls back to `general_chat`.

## Optional: opencode CLI (not Prompter)

[opencode](https://opencode.ai) (terminal coding agent — **not** “OpenCode Go”) can talk to the same box via Ollama’s OpenAI-compatible `/v1` API. This is **independent** of Prompter’s router / LiteLLM.

```bash
curl -fsSL https://opencode.ai/install | bash
# or: npm install -g opencode-ai
```

Example `~/.config/opencode/config.json` (or project `opencode.json`):

```json
{
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://192.168.0.240:11434/v1"
      },
      "models": {
        "qwen3-coder:30b-16k": {},
        "qwen3:8b-32k": {}
      }
    }
  },
  "model": "ollama/qwen3-coder:30b-16k"
}
```

`baseURL` must include `/v1`. No API key required; if the CLI insists, use a placeholder. Verify with `curl -s http://192.168.0.240:11434/v1/models`.

## Notes

- Single GPU: first request after a model swap is slower while weights load.
- Thinking models may emit reasoning tokens; truncation issues are usually `max_tokens` vs thinking headroom (see host `CLAUDE.md`).
- Default routing may already be LAN-local (no OpenCode key). Remotes remain available when you select a `remote/*` alias.
