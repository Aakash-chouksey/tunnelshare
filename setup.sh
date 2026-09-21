#!/usr/bin/env bash
# TunnelShare one-shot setup and run.
#
# One command does everything an agent or a human needs:
#   ./setup.sh --run
#
# It detects the OS, ensures Python 3, creates .venv, installs deps,
# installs cloudflared when missing, starts the Flask app detached,
# opens a Cloudflare quick tunnel, and prints the public share URL.
#
# Background services are fully detached (setsid, stdio redirected), so
# this script is safe to run from automation that waits on pipes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_LOG="/tmp/tunnelshare.log"
TUNNEL_LOG="/tmp/cloudflared_tunnel.log"
URL_FILE="$SCRIPT_DIR/.tunnel-url"

# Stable link. The quick tunnel URL changes on every restart, so the link a
# sender hands out points at the Workers host instead. setup.sh publishes the
# live tunnel URL there, and the worker redirects.
STABLE_HOST="${TS_STABLE_HOST:-tunnelshare.billuu-probe.workers.dev}"
PUBLISH_ENDPOINT="${TS_PUBLISH_ENDPOINT:-https://$STABLE_HOST/publish}"
STABLE_URL_FILE="$SCRIPT_DIR/.stable-url"
PUBLISH_TOKEN_FILE="$SCRIPT_DIR/.publish-token"

usage() {
  cat <<'EOF'
Usage: ./setup.sh [--run] [--help]

  --run    Full one-shot flow: setup, start app, open tunnel, print share URL.
  --help   Show this help and exit.

With no flags this only sets up .venv and installs dependencies.
EOF
}

MODE="setup"
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi
if [ "${1:-}" = "--run" ]; then
  MODE="run"
elif [ $# -gt 0 ]; then
  echo "Unknown argument: $1" >&2
  usage >&2
  exit 1
fi

# All progress chatter goes to stderr so stdout stays clean for the URL.
log() { echo "$@" >&2; }

detect_os() {
  local uname_out
  uname_out="$(uname -a 2>/dev/null || echo unknown)"
  case "$uname_out" in
    *icrosoft*|*Microsoft*|*WSL*) echo "windows-wsl"; return ;;
  esac
  if [ -n "${windir:-}" ] || [ -n "${WSL_DISTRO_NAME:-}" ]; then
    echo "windows-wsl"; return
  fi
  case "$(uname -s 2>/dev/null)" in
    Darwin) echo "macos"; return ;;
    MINGW*|MSYS*|CYGWIN*) echo "windows"; return ;;
    Linux) echo "linux"; return ;;
  esac
  echo "unknown"
}

OS="$(detect_os)"

ensure_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    log "Error: python3 not found. Install Python 3.10+ first."
    case "$OS" in
      macos) log "On macOS: brew install python" ;;
      windows*) log "On Windows: install from https://www.python.org/downloads/ (tick Add to PATH)." ;;
      *) log "On Linux: sudo apt-get install -y python3 python3-venv  (or the dnf/yum equivalent)." ;;
    esac
    exit 1
  fi
  python3 -m pip --version >/dev/null 2>&1 || {
    log "Error: pip missing. Reinstall Python with pip (python.org installer or python3-pip package)."
    exit 1
  }
  python3 -c "import venv" 2>/dev/null || {
    log "Error: venv module missing. On Debian/Ubuntu: sudo apt-get install -y python3-venv"
    exit 1
  }
}

install_cloudflared() {
  command -v cloudflared >/dev/null 2>&1 && { log "cloudflared already installed."; return 0; }
  log "cloudflared not found, attempting install..."
  case "$OS" in
    macos)
      command -v brew >/dev/null 2>&1 || { log "Error: Homebrew missing. Install from https://brew.sh then rerun."; exit 1; }
      brew install cloudflared
      ;;
    linux)
      distro="unknown"
      if [ -f /etc/os-release ]; then
        distro="$( ( . /etc/os-release; printf '%s' "$ID" ) 2>/dev/null | tr -d '"' || echo unknown )"
      fi
      case "$distro" in
        debian|ubuntu|raspbian|pop|linuxmint)
          if sudo -n true 2>/dev/null; then
            sudo apt-get update && sudo apt-get install -y cloudflared
          else
            log "Need sudo for apt-get. Either rerun where sudo works, or install manually:"
            log "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
            exit 1
          fi
          ;;
        rhel|centos|fedora|rocky|almalinux|ol)
          if sudo -n true 2>/dev/null; then
            sudo dnf install -y cloudflared 2>/dev/null || sudo yum install -y cloudflared
          else
            log "Need sudo for dnf/yum, or install manually from the Cloudflare downloads page."
            exit 1
          fi
          ;;
        arch|manjaro|endeavouros)
          if sudo -n true 2>/dev/null; then
            sudo pacman -Sy --noconfirm cloudflared
          else
            log "Need sudo for pacman, or install cloudflared manually."
            exit 1
          fi
          ;;
        *)
          log "Unsupported distro ($distro). Install cloudflared manually:"
          log "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
          exit 1
          ;;
      esac
      ;;
    windows|windows-wsl)
      log "On Windows install cloudflared from:"
      log "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/"
      log "or: winget install --id Cloudflare.cloudflared"
      log "Then rerun this script."
      exit 1
      ;;
    *)
      log "Unknown OS. Install cloudflared manually, then rerun."
      exit 1
      ;;
  esac
  command -v cloudflared >/dev/null 2>&1 || { log "Install finished but cloudflared still not on PATH."; exit 1; }
}

setup_venv() {
  if [ ! -d .venv ]; then
    log "Creating virtualenv (.venv)..."
    python3 -m venv .venv
  fi
  log "Installing dependencies..."
  .venv/bin/pip install -q -r requirements.txt
}

app_running() {
  curl -sf http://127.0.0.1:8080/ >/dev/null 2>&1
}

start_app() {
  if app_running; then
    log "App already responding on http://127.0.0.1:8080, reusing it."
    return 0
  fi
  log "Starting Flask app detached..."
  setsid .venv/bin/python app.py >"$APP_LOG" 2>&1 < /dev/null &
  for _ in $(seq 1 30); do
    app_running && { log "App is ready."; return 0; }
    sleep 1
  done
  log "Error: app did not answer on 127.0.0.1:8080. Tail of $APP_LOG:"
  tail -20 "$APP_LOG" >&2 || true
  exit 1
}

open_tunnel() {
  log "Opening Cloudflare quick tunnel (HTTP/2 transport: reliable through VPNs)..."
  # HTTP/2 over TCP survives networks where QUIC/UDP stalls (measured:
  # QUIC gave 90s stalls and 7s tiny responses through a VPN, HTTP/2
  # gives 1-7s uploads up to 2MB on the same path).
  TUNNEL_TRANSPORT_PROTOCOL=http2 setsid cloudflared tunnel --url http://127.0.0.1:8080 >"$TUNNEL_LOG" 2>&1 < /dev/null &
  local url=""
  for _ in $(seq 1 60); do
    if [ -f "$TUNNEL_LOG" ]; then
      url="$(grep -a -o -E 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)"
      [ -n "$url" ] && break
    fi
    sleep 2
  done
  if [ -z "$url" ]; then
    log "Error: no tunnel URL appeared in $TUNNEL_LOG. Tail:"
    tail -20 "$TUNNEL_LOG" >&2 || true
    exit 1
  fi
  printf '%s' "$url" > "$URL_FILE"
  printf '%s' "$url"
}

ensure_publish_token() {
  [ -f "$PUBLISH_TOKEN_FILE" ] && return 0
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24 > "$PUBLISH_TOKEN_FILE"
  else
    .venv/bin/python -c 'import secrets;print(secrets.token_hex(24))' > "$PUBLISH_TOKEN_FILE"
  fi
  chmod 600 "$PUBLISH_TOKEN_FILE" 2>/dev/null || true
  log "Created $PUBLISH_TOKEN_FILE"
  log "Set the same value on the worker once, then stable links start working:"
  log "  cd workers-site && wrangler secret put PUBLISH_TOKEN < ../.publish-token"
}

publish_live_url() {
  local url="$1"
  [ -f "$PUBLISH_TOKEN_FILE" ] || { log "No publish token, skipping the stable-link publish."; return 0; }
  local token
  token="$(cat "$PUBLISH_TOKEN_FILE" 2>/dev/null || true)"
  [ -n "$token" ] || { log "Empty publish token, skipping the stable-link publish."; return 0; }
  if curl -sf -m 20 -X POST "$PUBLISH_ENDPOINT" \
      -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
      -d "{\"url\":\"$url\"}" >/dev/null 2>&1; then
    log "Stable link published to $PUBLISH_ENDPOINT"
  else
    log "Warning: could not publish the stable link. The tunnel URL still works."
  fi
}

ensure_python
setup_venv

if [ "$MODE" != "run" ]; then
  log ""
  log "Setup done. To start everything: ./setup.sh --run"
  exit 0
fi

install_cloudflared
start_app
SHARED_URL="$(open_tunnel)"

printf 'https://%s' "$STABLE_HOST" > "$STABLE_URL_FILE"
ensure_publish_token
publish_live_url "$SHARED_URL"

OWNER_TOKEN_FILE="$SCRIPT_DIR/.owner-token"
if [ -f "$OWNER_TOKEN_FILE" ]; then
  OWNER_TOKEN="$(cat "$OWNER_TOKEN_FILE" 2>/dev/null || true)"
  if [ -n "${OWNER_TOKEN:-}" ]; then
    log "Owner console (private, local only): http://127.0.0.1:8080/?token=$OWNER_TOKEN"
  else
    log "Owner token file empty; check $APP_LOG for the Owner console line."
  fi
else
  log "Owner token file not found; check $APP_LOG for the Owner console line."
fi

echo "$SHARED_URL"
log ""
log "TunnelShare is running."
log "Stable link (survives a restart): https://$STABLE_HOST/s/<id>"
log "Live tunnel URL right now: $SHARED_URL"
log "Local app: http://127.0.0.1:8080  (logs: $APP_LOG, tunnel: $TUNNEL_LOG)"
log "Stop with: pkill -x cloudflared; pkill -f '[a]pp.py'"
