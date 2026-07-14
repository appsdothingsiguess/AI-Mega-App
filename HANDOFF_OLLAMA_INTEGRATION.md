# Handoff: Ollama Integration Status

Date: 2026-07-14

## Current state

- **Backend box**: Ollama runs as a systemd service on a dedicated RTX 3090 LAN box
  (`llm-stack/ollama`, `http://192.168.0.240:11434`). 20 models installed and confirmed
  reachable (see that repo's `CLAUDE.md` for the full tag table). Models auto-load into VRAM on
  first request and auto-unload after idle — nothing is resident at rest by design.
- **Prompter → Ollama**: wired up via LiteLLM (`litellm_config.yaml`, `local/*` aliases →
  `ollama_chat/<tag>`). Router (`app/router.py`) dispatches each of the 9 intents to a model
  alias; the classifier itself (`ollama/qwen2.5:1.5b-32k`) runs CPU-only on the same box. This
  path is confirmed working end-to-end (verified `qwen3:8b-32k` load + chat completion this
  session).
- **README** (`README.md`) documents both connectivity paths:
  - "In Prompter (current)" — how the app already connects, how to verify, how to change via
    Settings → Infrastructure.
  - "In opencode CLI (planned/future)" — draft config for pointing the opencode terminal coding
    agent directly at the Ollama box's `/v1` endpoint. **Not yet implemented or tested.**

## What's NOT done yet

1. **opencode CLI integration is undocumented-in-practice** — the README section is a config
   draft based on opencode's documented custom-provider support, not something we've actually
   run against this box. Needs a real install + smoke test before trusting it.
2. **No opencode ↔ Prompter router integration** — opencode, once working, is a fully separate
   CLI workflow. If the goal is to have Prompter's `coding_advanced`/`coding_basic` intents
   delegate to opencode (rather than calling Ollama directly via LiteLLM), that dispatch path
   doesn't exist and hasn't been designed.
3. **No decision made** on whether opencode replaces the `local/qwen3-coder-30b` style direct
   LiteLLM path for coding intents, or runs alongside it as a separate manual tool.
4. **Model availability check**: only `qwen3:8b-32k` was smoke-tested this session load a fresh
   test. Not all 20 installed tags have been re-verified against the current `litellm_config.yaml`
   aliases in *this* app (as opposed to the standalone Ollama box tests documented in
   `llm-stack/ollama/CLAUDE.md`).

## Next steps (suggested order)

1. Install opencode CLI and actually run the config from the README's "In opencode CLI"
   section against `192.168.0.240:11434/v1` — confirm it lists models and completes a request.
2. Decide whether opencode is a manual side-tool for the user, or should be wired into
   `chat_orchestrator.py` / `app/router.py` as a dispatch target for coding intents.
3. If wired in: design how intent routing picks opencode vs. direct Ollama-via-LiteLLM for
   `coding_basic` / `coding_advanced`, and how tool-calling/streaming semantics reconcile between
   opencode's own agent loop and Prompter's `chat_orchestrator`.
4. Add a test/health check for opencode reachability parallel to the existing `/health` endpoint
   check for Ollama/OpenCode Go.

## References

- `llm-stack/ollama/CLAUDE.md` — Ollama box operational details, model table, GPU pinning notes.
- `README.md` (this repo) — "Connecting to the Ollama box" section, both current and planned
  paths.
- `litellm_config.yaml` — current alias → model mapping for the working Prompter↔Ollama path.
