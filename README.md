# Prompter

Personal AI platform (Prompter X) that replaces a hosted chat UI with **filesystem projects**, **intent routing**, and **streamed replies** over local and remote models.

Python/FastAPI backend, React/Vite UI, LiteLLM → **Ollama** (LAN GPU box) and **OpenCode Go** (remote). Projects live under `projects/<id>/` (`instructions.md`, `docs/`, threads) — not a remote workspace DB.

Spec / agents: [`prompter_x_complete_spec.md`](prompter_x_complete_spec.md) · [`AGENTS.md`](AGENTS.md) · [`docs/`](docs/)

---

## How it works

One chat turn, high level:

```
user message
    → HybridRouter (keyword rules, then classifier)
    → intent + tools + model alias (+ confidence)
    → optional RAG (Qdrant + nomic embeddings over project docs/)
    → ChatOrchestrator streams the reply (SSE)
         · local/*  → Ollama via ModelScheduler (VRAM swap)
         · remote/* → OpenCode Go
```

**Router.** Config keyword rules can short-circuit known phrases. Everything else hits the **classifier** (`router.classifier`, default `ollama/qwen2.5:3b`): a small Ollama model that returns JSON `{intent, model, tools, confidence}` only — it does not answer the user. Live prompt is mut12 (`DEFAULT_CLASSIFIER_PROMPT` / `settings.json` `router.classifier_prompt`) with `{{MODEL:<intent>}}` filled from `settings.models`. Call options: `num_ctx=8192` (prompt is ~4.4k tokens of boundaries + few-shots — **4096 truncates and the model answers as chat**), `num_predict=250`, full GPU offload (`num_gpu=999`). Parse failure → fallback `general_chat` / `confidence=0` / no tools (then the chat model for `general_chat` loads — often `local/qwen3-8b`).

**Intents → model / tools (defaults).** Aliases resolve through `settings.json` + `ollama_model_names` + `litellm_config.yaml` — never hardcode underlying Ollama tags in app logic.

| Intent | Typical tools | Default model alias |
|--------|---------------|---------------------|
| `general_chat` | [] | `local/qwen3-8b` |
| `coding_basic` | [] | `local/coding-light` |
| `coding_advanced` | [] | `local/coding-heavy` |
| `web_search` | `web_search` | `local/tool-calling-medium` |
| `deep_research` | `web_search` | `local/reasoning-heavy` |
| `bash` / `pdf_gen` / `file_ops` | matching tool | `local/tool-calling-medium` |
| `vision` | `vision` | `local/vision-medium` |
| `reasoning_medium` / `reasoning_heavy` | web_search, bash, pdf_gen, file_ops | `local/reasoning-*` |

**Resident small models.** Startup warms (1) the classifier via `QwenClassifierAdapter.warmup()` with the options above, and (2) the embedding model `nomic-embed-text` via Ollama `/api/embed` (scheduler must not warm the classifier with a bare `/api/generate`). Pull the embed tag on the Ollama host if missing — otherwise RAG warmup 404s.

**UI.** Web chat streams SSE; debug traces show `ROUTE` (classifier decision) separately from `LLM REQUEST` (the reply model). Those are different models by design.

Classifier eval harness / ledger: `scripts/eval_classifier.py`, `eval/classifier/`, [`docs/classifier_prompt.md`](docs/classifier_prompt.md).

---

## Prerequisites

| Tool | Version | Required when |
|------|---------|----------------|
| **Python** | 3.11+ (3.12 recommended) | Always (CLI, API, tests) |
| **Node.js** (+ `npm`) | 18+ | Web UI — build (`web/dist`) or Vite dev server |
| **Qdrant** | optional | RAG over project `docs/` (vector search) |
| **Docker** | optional | Easiest way to run Qdrant (and optional local Ollama) |
| **Ollama box** | LAN reachable | Local model intents (see [Models](#models-ollama--remote)) |

Check:

```bash
python --version    # 3.11+
node --version      # 18+ if you want the UI
npm --version
```

Install Node from [nodejs.org](https://nodejs.org) if needed. **Vite and React are not installed globally** — `npm install` inside `web/` pulls them as project deps (`vite`, `react`, etc.).

---

## Setup

Work from the **repo root** (must contain `app/`, `web/`, `pyproject.toml`).

### 1. Python virtualenv + backend deps

```bash
# Create venv (once)
python -m venv .venv

# Activate every new shell
#   Git Bash (Windows):   source .venv/Scripts/activate
#   PowerShell:           .\.venv\Scripts\Activate.ps1
#   Cmd:                  .venv\Scripts\activate.bat
#   macOS / Linux:        source .venv/bin/activate

# Prompt should show (.venv)
pip install -U pip
pip install -e ".[dev]"          # editable app + pytest — FROM REPO ROOT, not app/

python -c "import app, pytest; print('ok')"
cp .env.example .env             # Cmd: copy .env.example .env
```

Optional (Windows CLI image/file paste): `pip install -e ".[windows]"`.

With **uv** instead of pip:

```bash
uv venv && source .venv/Scripts/activate   # adjust activate path for your shell
uv pip install -e ".[dev]"
```

### 2. Web UI deps (Node / npm / Vite) — skip if CLI/API/tests only

```bash
cd web
npm install          # installs Vite, React, TypeScript, etc. into web/node_modules/
cd ..
```

| Command | What it does |
|---------|----------------|
| `cd web && npm install` | One-time (or after `package.json` changes) |
| `cd web && npm run dev` | Vite HMR at http://localhost:5173 (proxies API → :8000) |
| `cd web && npm run build` | Production bundle → `web/dist/` (served by FastAPI) |

Do **not** run `npm` in `app/` — there is no `package.json` there.

### 3. Secrets / config

Edit `.env`: `OPENCODE_API_KEY` (remote models), `TAVILY_API_KEY` (optional deep research). Model aliases and router config live in `settings.json` / `litellm_config.yaml` — never put API keys there.

| Install | Where | Command | For |
|---------|--------|---------|-----|
| Python + pytest | **repo root** | `pip install -e ".[dev]"` | CLI, API, tests |
| Node packages | **`web/`** | `npm install` | UI (Vite ships in that install) |
| Qdrant | always-on host (recommended) | see [Qdrant (RAG)](#qdrant-rag--optional) | Doc retrieval |

---

## Qdrant (RAG — optional)

Qdrant stores embeddings of project docs so chat can retrieve relevant chunks. Chat still works when it is down; RAG is skipped and `/health` shows `qdrant:down` / overall **degraded**.

**Where to run it:** prefer an always-on **Ubuntu server** (guaranteed uptime) over a laptop that sleeps. Qdrant is **CPU + RAM only** — it does **not** use GPU/VRAM. A few hundred MB RAM idle is typical; disk grows with how many docs you ingest.

Default URL in Prompter: `http://localhost:6333`. If Qdrant runs on another LAN box, set **either**:

- `QDRANT_URL=http://<server-lan-ip>:6333` in `.env` (wins over JSON), or
- `qdrant.url` in `settings.json`, or
- ⚙ → **Infrastructure** → Qdrant URL

Example for this lab’s Qdrant host: `http://192.168.0.240:6333`.

### Ubuntu 26.04 LTS (server) — Docker + Qdrant

On the server (not required on the laptop that only runs the Prompter UI):

**1. Install Docker Engine** ([official Ubuntu guide](https://docs.docker.com/engine/install/ubuntu/); 26.04 / Resolute is supported):

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# One-liner avoids a hung `tee <<EOF` when pasting over SSH
. /etc/os-release
sudo bash -c "cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF"

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # log out/in afterward
```

If a previous `sudo tee …` is still sitting there with no prompt, press **Ctrl+C**, then continue from the sources step above. Confirm with `cat /etc/apt/sources.list.d/docker.sources`.

**2. Run Qdrant** ([quickstart](https://qdrant.tech/documentation/quickstart/)) with persistent storage and auto-restart:

```bash
sudo mkdir -p /var/lib/qdrant/storage
docker pull qdrant/qdrant
docker run -d \
  --name qdrant \
  --restart unless-stopped \
  -p 6333:6333 -p 6334:6334 \
  -v /var/lib/qdrant/storage:/qdrant/storage:z \
  qdrant/qdrant
```

**3. Verify from the server, then from your Prompter machine:**

```bash
curl -s http://127.0.0.1:6333/collections   # on the Qdrant host
curl -s http://<server-lan-ip>:6333/collections
```

Then set Prompter’s `qdrant.url` to `http://<server-lan-ip>:6333` and check `python -m app.main health` (expect `qdrant` **up**).

**LAN note:** by default Qdrant has no auth. Bind to a trusted LAN (or firewall port 6333 to your client only). Do **not** expose it to the public internet without [Qdrant security](https://qdrant.tech/documentation/guides/security/) (API key / TLS).

**Laptop alternative:** from this repo, `docker compose up -d qdrant` starts only the Qdrant service (skip the compose `ollama` service if models already run on your LAN GPU box).

---

## Models (Ollama + remote)

Local intents go to **Ollama** on the LAN (`http://192.168.0.240:11434` by default). Remote aliases go through **OpenCode Go** (`OPENCODE_API_KEY`).

On the Ollama box you need at least:

| Tag | Role |
|-----|------|
| `qwen2.5:3b` | Intent classifier (GPU, always-on) |
| `nomic-embed-text` | Doc embeddings for RAG |
| Plus the chat / coding / reasoning / vision tags mapped in `settings.json` `ollama_model_names` | Reply models swapped by `ModelScheduler` |

```bash
curl -s http://192.168.0.240:11434/api/tags   # box up? tags present?
python -m app.main health                     # Prompter’s view
```

| Task | How |
|------|-----|
| Wrong IP / port | Edit `api_base` in `litellm_config.yaml`, or ⚙ → Infrastructure |
| Change model per intent | `settings.json` or ⚙ → Models |
| Change classifier tag / prompt | `settings.json` `router.classifier` / `router.classifier_prompt` |
| Full setup (add models, opencode CLI) | [`docs/ollama-lan-backend-setup.md`](docs/ollama-lan-backend-setup.md) |
| Classifier prompt eval | [`docs/classifier_prompt.md`](docs/classifier_prompt.md), [`docs/classifier_prompt_handoff.md`](docs/classifier_prompt_handoff.md) |

---

## Run

Activate the venv first (every terminal): `source .venv/Scripts/activate` (Git Bash) or `.\.venv\Scripts\Activate.ps1` (PowerShell).

**Windows one-click:** double-click `Start Prompter.bat` — creates `.venv` if missing, runs `npm install` + `npm run build` in `web/` when `web/dist/` is missing (needs Node on PATH), then opens http://127.0.0.1:8000.

**API + built UI:**

```bash
# If web/dist is missing:
cd web && npm install && npm run build && cd ..

python -m app.main serve                # opens browser
# or: uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**UI hot-reload (Vite):**

```bash
# Terminal 1 — repo root, venv on
python -m app.main serve --no-browser

# Terminal 2 — Node, not the venv
cd web && npm run dev                   # http://localhost:5173 → proxies :8000
```

Swagger: http://127.0.0.1:8000/docs

---

## Projects

```bash
python -m app.main init "My Research"   # → projects/my-research/
# edit projects/my-research/instructions.md
# drop .txt / .md / .pdf into projects/my-research/docs/
python -m app.main chat my-research     # syncs docs, then chats
```

```
projects/<id>/
  project.yaml        instructions.md        docs/
  .prompter/          # chunks, threads — app-managed
```

Useful commands: `list-projects`, `show-project`, `sync`, `add-file`, `new-thread`.

**CLI chat:** Enter = newline · **Alt+Enter** / **Ctrl+J** = send · `/exit` to quit · Ctrl+V = smart paste. Optional Windows clipboard extras: `pip install -e ".[windows]"`.

---

## Tests

Python only — **no npm**. Always from **repo root** with the venv active:

```bash
mkdir -p .pytest-tmp
python -m pytest -q --basetemp=.pytest-tmp/run
```

Use `python -m pytest`, not bare `pytest`. On Windows, create `.pytest-tmp` first or you get mass `FileNotFoundError` / `PermissionError`. Baseline on `main`: **~240 passed, 2 known failed**.

| Problem | Fix |
|---------|-----|
| `collected 0` | `cd` to repo root |
| Mass ERROR / path errors | `mkdir -p .pytest-tmp` + `--basetemp=.pytest-tmp/run` |
| `No module named 'app'` | `pip install -e ".[dev]"` at repo root |
| npm ENOENT under `app/` | Wrong folder — use `web/` |

---

## Layout

```
app/                  # FastAPI + CLI + orchestrator / router / adapters
web/                  # React + Vite (npm here only)
tests/                # pytest
projects/             # your projects (filesystem-first)
litellm_config.yaml   # alias → provider / api_base
settings.json         # runtime overlay (gitignored; created on first run)
.env                  # secrets (from .env.example)
docs/                 # handoffs & backend notes
```

Intent labels: `general_chat` · `web_search` · `deep_research` · `coding_basic` · `coding_advanced` · `bash` · `pdf_gen` · `file_ops` · `vision` · `reasoning_medium` · `reasoning_heavy`

---

## More

| Topic | Where |
|-------|--------|
| Architecture / phase contracts | `prompter_x_complete_spec.md`, `AGENTS.md` |
| How a turn is routed | [How it works](#how-it-works) |
| Ollama / model backends | [`docs/ollama-lan-backend-setup.md`](docs/ollama-lan-backend-setup.md) |
| Classifier prompt / eval | [`docs/classifier_prompt.md`](docs/classifier_prompt.md) |
| Qdrant / RAG | [Qdrant (RAG)](#qdrant-rag--optional) |
| Settings UI | Web → ⚙ (models, router, infra); secrets stay in `.env` |
| API surface | `/docs` while the server is running |
| Phase handoffs / known bugs | `docs/phase1-*.md` |

LM Studio is removed; leftover `LMSTUDIO_*` env vars are no-ops.

## License

MIT (add a `LICENSE` file if you distribute this).
