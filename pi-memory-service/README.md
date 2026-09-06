# pi-memory-service

`pi-memory-service` is a small, Ubuntu-hosted durable-claim store for the future Windows Pi extension. It owns a separate SQLite WAL database and deterministic FTS5 recall. It does not invoke GooseDump, BGE, llama-swap, the relay, or any other inference model.

The Pi-side fork retains GooseDump session behavior: `goose_search`, `goose_grep`, `goose_get`, compaction, and session management. Its replacement memory flow is:

```text
goose_remember / successful post-compaction hook
  -> ctx.modelRegistry.complete(ctx.model, ...) through Pi's selected model
     provider (normally relay :8081; isolated Qwen3.6 mode uses :8082)
  -> structured durable claims
  -> pi-memory-service store/search/tombstone API
```

Thus extraction uses whichever model Pi already has selected and loaded on GPU0; this service receives claims only.

## Base URL

The production user unit binds exactly to **`http://192.168.0.89:8091`**. It accepts the configured Windows Pi host (`192.168.0.246`) and localhost only, and every endpoint—including health—requires a bearer token.

## Setup

The service has no dependency on AI Mega App application code. Its only Python packages are FastAPI and Uvicorn:

```bash
cd /home/john/AI-Mega-App/pi-memory-service
/home/john/AI-Mega-App/.venv/bin/pip install -r requirements.txt
install -m 600 /dev/null /home/john/.config/pi-memory-service.env
```

Set these values in `/home/john/.config/pi-memory-service.env`; do not commit this file or print its token:

```ini
PI_MEMORY_TOKEN=replace-with-a-long-random-secret
PI_MEMORY_DB=/home/john/.local/share/pi-memory-service/memory.sqlite3
PI_MEMORY_ALLOWED_CLIENT=192.168.0.246
```

Install the unit after this branch is integrated at `/home/john/AI-Mega-App`:

```bash
install -D -m 644 ops/pi-memory-service.service ~/.config/systemd/user/pi-memory-service.service
systemctl --user daemon-reload
systemctl --user enable --now pi-memory-service.service
systemctl --user status pi-memory-service.service
```

This creates `pi-memory-service.service`; it neither restarts nor changes llama-swap, AI Mega App, either Pi relay, or Qwen3.6.

## API examples

Use `Authorization: Bearer $PI_MEMORY_TOKEN` on every request. The token is deliberately referenced only through an environment variable here.

```bash
curl -H "Authorization: Bearer $PI_MEMORY_TOKEN" http://192.168.0.89:8091/health

curl -X POST http://192.168.0.89:8091/v1/memories \
  -H "Authorization: Bearer $PI_MEMORY_TOKEN" -H 'content-type: application/json' \
  --data '{"project_key":"C:\\work\\demo","session_id":"pi-session-123","source_hash":"sha256-of-summary","claims":[{"content":"Use SQLite WAL for durable claims.","type":"decision","tags":["sqlite","storage"]}]}'

curl -X POST http://192.168.0.89:8091/v1/memories/search \
  -H "Authorization: Bearer $PI_MEMORY_TOKEN" -H 'content-type: application/json' \
  --data '{"project_key":"C:\\work\\demo","query":"SQLite WAL","types":["decision"],"limit":5}'

curl -X DELETE -H "Authorization: Bearer $PI_MEMORY_TOKEN" http://192.168.0.89:8091/v1/memories/1

curl -H "Authorization: Bearer $PI_MEMORY_TOKEN" 'http://192.168.0.89:8091/v1/memories/status?project_key=C%3A%5Cwork%5Cdemo'
```

`POST /v1/memories` accepts at most 50 claims and returns `created_ids` and `updated_ids`. Active claims deduplicate on normalized `project_key + content`; repeated claims merge tags, retain their active ID, and add unique `session_id`/`source_hash` provenance. Deletes are tombstones, so search excludes them and status reports both active and tombstoned counts.

Search is lexical FTS5 only: query words are combined with `AND`, FTS rank ties are ordered by claim ID, and no embeddings are created or queried.
