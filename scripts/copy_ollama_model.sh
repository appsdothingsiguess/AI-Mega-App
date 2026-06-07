#!/usr/bin/env bash
set -euo pipefail

# Usage: copy_ollama_model.sh <library/path>
# Example: copy_ollama_model.sh nomic-embed-text/latest
# Example: copy_ollama_model.sh qwen2.5/1.5b

MODEL_REF="$1"
WIN_SRC="/mnt/c/Users/john/.ollama/models"
STAGING="/tmp/ollama-staging"
MANIFEST_SRC="$WIN_SRC/manifests/registry.ollama.ai/library/$MODEL_REF"

if [[ ! -f "$MANIFEST_SRC" ]]; then
  echo "Manifest not found: $MANIFEST_SRC" >&2
  exit 1
fi

rm -rf "$STAGING"
mkdir -p "$STAGING/manifests/registry.ollama.ai/library/$(dirname "$MODEL_REF")"
mkdir -p "$STAGING/blobs"

cp "$MANIFEST_SRC" "$STAGING/manifests/registry.ollama.ai/library/$MODEL_REF"

mapfile -t DIGESTS < <(python3 - <<'PY' "$MANIFEST_SRC"
import json, sys
m = json.load(open(sys.argv[1]))
digests = [m["config"]["digest"]]
digests += [layer["digest"] for layer in m.get("layers", [])]
for d in digests:
    print(d.removeprefix("sha256:"))
PY
)

for hash in "${DIGESTS[@]}"; do
  src="$WIN_SRC/blobs/sha256-$hash"
  if [[ ! -f "$src" ]]; then
    echo "Missing blob: $src" >&2
    exit 1
  fi
  cp "$src" "$STAGING/blobs/sha256-$hash"
done

docker cp "$STAGING/manifests" ollama-gpu:/root/.ollama/models/
docker cp "$STAGING/blobs/." ollama-gpu:/root/.ollama/models/blobs/

echo "Copied $MODEL_REF. Blobs: ${#DIGESTS[@]}"
docker exec ollama-gpu ollama list
