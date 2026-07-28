#!/usr/bin/env bash
# Installs and enables systemd units so llama-swap and the AI-Mega-App
# backend start automatically on boot on this box (ailab).
#
# Run once, as root:
#   sudo bash scripts/install_startup_services.sh
#
# Idempotent: safe to re-run after pulling changes to this script.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Must run as root: sudo bash $0" >&2
  exit 1
fi

REPO_DIR="/home/john/AI-Mega-App"
RUN_USER="john"
LLAMA_SWAP_BIN="/home/john/llm-stack/serving/llama-swap/llama-swap"
LLAMA_SWAP_CONFIG="/home/john/llm-stack/serving/llama-swap/config.yaml"
UV_BIN="/home/john/.local/bin/uv"

if [[ ! -x "$LLAMA_SWAP_BIN" ]]; then
  echo "error: llama-swap binary not found at $LLAMA_SWAP_BIN" >&2
  exit 1
fi
if [[ ! -f "$LLAMA_SWAP_CONFIG" ]]; then
  echo "error: llama-swap config not found at $LLAMA_SWAP_CONFIG" >&2
  exit 1
fi
if [[ ! -x "$UV_BIN" ]]; then
  echo "error: uv not found at $UV_BIN" >&2
  exit 1
fi

cat > /etc/systemd/system/llama-swap.service <<EOF
[Unit]
Description=llama-swap (model proxy/scheduler for llama.cpp)
After=network.target

[Service]
Type=simple
User=${RUN_USER}
ExecStart=${LLAMA_SWAP_BIN} --config ${LLAMA_SWAP_CONFIG}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/ai-mega-app.service <<EOF
[Unit]
Description=AI Mega App backend (FastAPI)
After=network.target llama-swap.service
Wants=llama-swap.service

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${UV_BIN} run uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now llama-swap.service
systemctl enable --now ai-mega-app.service

echo
echo "Installed and started:"
systemctl --no-pager status llama-swap.service ai-mega-app.service | grep -E "^\S|Active:"
echo
echo "Check with: systemctl status llama-swap ai-mega-app"
echo "Logs with:  journalctl -u llama-swap -u ai-mega-app -f"
