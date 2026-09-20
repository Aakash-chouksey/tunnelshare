#!/usr/bin/env bash
# TunnelShare one-shot local setup.
#
# Creates a venv, installs dependencies, and prints the tunnel command.
# Does NOT start the app or the tunnel — you run those yourself.
#
# Usage:
#   ./setup.sh            set up venv + install deps
#   ./setup.sh --help     show this help
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./setup.sh [--help]

One-shot local setup for TunnelShare:

  1. Creates .venv (python3 -m venv) if missing.
  2. Installs dependencies from requirements.txt.
  3. Prints how to run the app and open a Cloudflare tunnel.

Options:
  -h, --help    Show this help and exit.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ $# -gt 0 ]; then
  echo "Unknown argument: $1" >&2
  usage >&2
  exit 1
fi

cd "$(dirname "$0")"

command -v python3 >/dev/null 2>&1 || {
  echo "Error: python3 not found. Install Python 3.10+ first." >&2
  exit 1
}

if [ ! -d .venv ]; then
  echo "Creating virtualenv (.venv)..."
  python3 -m venv .venv
fi

echo "Installing dependencies..."
.venv/bin/pip install -r requirements.txt

echo ""
echo "TunnelShare is ready."
echo ""
echo "Run the app:"
echo "  .venv/bin/python app.py   # serves http://127.0.0.1:8080"
echo ""
echo "Then, in another terminal, open a public URL:"
echo "  cloudflared tunnel --url http://127.0.0.1:8080"
