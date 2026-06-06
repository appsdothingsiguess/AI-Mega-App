# Prompter Sample Project

Prompter is a local project assistant inspired by Claude Projects.

## Features

- Persistent project instructions (`instructions.md` in each project folder)
- Document folder (`docs/`) ingested into chunks for retrieval
- Per-thread chat history under `.prompter/threads/`
- LM Studio backend (native `/api/v1/chat` or OpenAI-compatible REST)

## Workflow

1. Run `python -m app.main init "My Project"`.
2. Edit `projects/my-project/instructions.md`.
3. Drag `.txt`, `.md`, or `.pdf` files into `projects/my-project/docs/`.
4. Run `python -m app.main chat my-project`.

## LM Studio

Run LM Studio locally on port 1234 and load your model (default config: `gemma-4-e4b-it`).

Switch modes with `LMSTUDIO_MODE=llm` (native API) or `LMSTUDIO_MODE=rest` (`/v1/chat/completions`).
