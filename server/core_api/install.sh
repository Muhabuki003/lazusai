#!/usr/bin/env bash
# Install the LazusAI Core API on the Hetzner VPS as a systemd unit.
# Idempotent. Run as root (or with sudo) from the repo's server/core_api dir.
set -euo pipefail

SERVICE_DIR="/opt/lazusai/core"
DATA_DIR="/opt/lazusai/data"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$(cd "$HERE/../lib" && pwd)"

echo "==> Deps"
if command -v apt-get >/dev/null; then
  apt-get update -y && apt-get install -y python3-venv python3-pip
fi

echo "==> $SERVICE_DIR"
mkdir -p "$SERVICE_DIR/lib" "$DATA_DIR/clients" "$DATA_DIR/leads"
cp "$HERE/app.py" "$SERVICE_DIR/app.py"
cp "$HERE/requirements.txt" "$SERVICE_DIR/requirements.txt"
cp "$LIB"/*.py "$SERVICE_DIR/lib/"

python3 -m venv "$SERVICE_DIR/.venv"
"$SERVICE_DIR/.venv/bin/pip" install --upgrade pip
"$SERVICE_DIR/.venv/bin/pip" install -r "$SERVICE_DIR/requirements.txt"

echo "==> systemd unit"
cp "$HERE/lazusai-core.service" /etc/systemd/system/lazusai-core.service
mkdir -p /etc/lazusai
[ -f /etc/lazusai/core.env ] || cat > /etc/lazusai/core.env <<EOF
LAZUSAI_CORE_KEY=change-me
LAZUSAI_DATA_DIR=$DATA_DIR
CHROMA_HOST=127.0.0.1
CHROMA_PORT=8001
NIM_BASE_URL=http://127.0.0.1:8000
# Optional: push routing to Cloudflare KV automatically
# CF_ACCOUNT_ID=
# CF_KV_NAMESPACE_ID=
# CF_API_TOKEN=
EOF

systemctl daemon-reload
systemctl enable lazusai-core
systemctl restart lazusai-core
echo "==> Done. Edit /etc/lazusai/core.env then: curl localhost:8003/health"
