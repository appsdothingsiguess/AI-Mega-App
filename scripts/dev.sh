#!/usr/bin/env bash
# Local dev: fake llama-swap + uvicorn (reload) + tsc --watch, one command.
# No GPU, no network to model hosts (PLAN.md §4.10). tsc --watch is skipped
# gracefully until p1/web-shell lands tsconfig.json.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FAKE_PORT="${DEV_FAKE_PORT:-8080}"
APP_PORT="${DEV_APP_PORT:-8000}"

PIDS=()

cleanup() {
  echo "dev.sh: shutting down..." >&2
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

run_py() {
  if command -v uv >/dev/null 2>&1; then
    uv run "$@"
  else
    "$@"
  fi
}

echo "dev.sh: starting fake llama-swap on :${FAKE_PORT}" >&2
run_py uvicorn e2e.fake_backend:app --host 127.0.0.1 --port "${FAKE_PORT}" &
PIDS+=("$!")

echo "dev.sh: starting app.main:app on :${APP_PORT} (--reload)" >&2
run_py uvicorn app.main:app --host 127.0.0.1 --port "${APP_PORT}" --reload &
PIDS+=("$!")

if [[ -f "${REPO_ROOT}/tsconfig.json" ]]; then
  echo "dev.sh: starting tsc --watch" >&2
  npx tsc --watch &
  PIDS+=("$!")
else
  echo "dev.sh: no tsconfig.json yet (p1/web-shell not merged) — skipping tsc --watch" >&2
fi

wait
