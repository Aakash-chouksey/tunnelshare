#!/usr/bin/env bash
# TunnelShare helper: install deps, start Flask, open a Cloudflare quick tunnel.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

.venv/bin/python app.py &
APP_PID=$!
trap "kill $APP_PID" EXIT INT TERM

echo "Waiting for app on 127.0.0.1:8080…"
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8080/ >/dev/null; then break; fi
  sleep 0.5
done

echo "Starting Cloudflare quick tunnel (HTTP/2 transport for VPN reliability)…"
exec env TUNNEL_TRANSPORT_PROTOCOL=http2 cloudflared tunnel --url http://localhost:8080
