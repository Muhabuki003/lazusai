#!/usr/bin/env bash
# Install the LazusAI Whisper service on the Hetzner VPS as a systemd unit.
# Idempotent: safe to re-run. Run as root (or with sudo).
set -euo pipefail

SERVICE_DIR="/opt/lazusai/whisper"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing system deps (ffmpeg, python venv)"
if command -v apt-get >/dev/null; then
  apt-get update -y
  apt-get install -y python3-venv python3-pip ffmpeg
fi

echo "==> Setting up $SERVICE_DIR"
mkdir -p "$SERVICE_DIR"
cp "$HERE/app.py" "$SERVICE_DIR/app.py"
cp "$HERE/requirements.txt" "$SERVICE_DIR/requirements.txt"

python3 -m venv "$SERVICE_DIR/.venv"
"$SERVICE_DIR/.venv/bin/pip" install --upgrade pip
"$SERVICE_DIR/.venv/bin/pip" install -r "$SERVICE_DIR/requirements.txt"

echo "==> Installing systemd unit"
sed "s#__SERVICE_DIR__#$SERVICE_DIR#g" "$HERE/whisper.service" > /etc/systemd/system/lazusai-whisper.service
systemctl daemon-reload
systemctl enable lazusai-whisper
systemctl restart lazusai-whisper

echo "==> Done. Check: systemctl status lazusai-whisper && curl localhost:8002/health"
