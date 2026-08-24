#!/usr/bin/env bash
set -Eeuo pipefail

# Restart the backend, regenerate/apply llama-swap configuration, and run the
# repository verification gate. Run from the repository root or any directory.
# The restart requires sudo; the apply endpoint is intentionally called only
# after the backend is back up so it uses the current checked-in/overlay config.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
app_url="${AI_MEGA_APP_URL:-http://127.0.0.1:8000}"
ready_timeout_s="${AI_MEGA_APP_READY_TIMEOUT_S:-90}"

if [[ -x "$repo_root/.venv/bin/python" ]]; then
  python_bin="$repo_root/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  python_bin="$(command -v python)"
else
  python_bin="$(command -v python3)"
fi

cd "$repo_root"

echo "==> Restarting ai-mega-app"
sudo systemctl restart ai-mega-app

echo "==> Waiting for ai-mega-app readiness"
health_url="${app_url%/}/health"
ready=0
for ((elapsed=0; elapsed<ready_timeout_s; elapsed++)); do
  if curl --fail --silent --show-error --max-time 2 "$health_url" >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done

if ((ready == 0)); then
  echo "ai-mega-app did not become ready within ${ready_timeout_s}s: ${health_url}" >&2
  exit 1
fi

echo "==> Re-applying generated llama-swap configuration"
curl --fail-with-body --silent --show-error \
  --request POST \
  --header 'Accept: application/json' \
  "${app_url%/}/api/gpu/apply"
printf '\n'

echo "==> Running pytest"
"$python_bin" -m pytest -q --basetemp=.pytest-tmp/run

echo "==> Running TypeScript check"
npx tsc --noEmit

echo "==> Restart, config apply, and verification completed"
