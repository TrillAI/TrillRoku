#!/usr/bin/env bash
set -euo pipefail

# Quick Tunnel: forwards the public trycloudflare.com URL to the local Flask API.
# Start the API separately first with: python -m backend.app
port="${TRILLROKU_PORT:-8000}"
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is required; install Cloudflare Tunnel for this machine first." >&2
  exit 127
fi
exec cloudflared tunnel --url "http://127.0.0.1:${port}"
