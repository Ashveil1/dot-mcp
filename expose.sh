#!/usr/bin/env bash
# Start dot-mcp and expose it through a tunnel.
#
# Default (stable random tunnl.gg URL; no provider account or key registration):
#   ./expose.sh
#
# The SSH key is generated once and stored in ~/.config/dot-mcp.
# Bearer/OAuth authentication is required; keep credentials private.
#
# Stable ngrok URL (one-time setup in your shell environment):
#   export TUNNEL_PROVIDER=ngrok
#   export NGROK_AUTHTOKEN='...'
#   export NGROK_DOMAIN='your-assigned-domain.ngrok-free.app'
#   ./expose.sh
#
# NGROK_DOMAIN must be the domain assigned to your ngrok account.
set -Eeuo pipefail
cd "$(dirname "$0")"

PORT="${MCP_PORT:-33041}"
PROVIDER="${TUNNEL_PROVIDER:-tunnl}"
mkdir -p logs bin

if [[ "$PROVIDER" != "tunnl" && "$PROVIDER" != "cloudflare" && "$PROVIDER" != "ngrok" && "$PROVIDER" != "serveo" ]]; then
  echo "Unsupported TUNNEL_PROVIDER='$PROVIDER'. Use tunnl, cloudflare, ngrok, or serveo." >&2
  exit 2
fi

if [[ "$PROVIDER" == "tunnl" || "$PROVIDER" == "serveo" ]]; then
  command -v ssh >/dev/null 2>&1 || { echo "ssh client not found; install OpenSSH client first." >&2; exit 1; }
fi

if [[ "$PROVIDER" == "ngrok" ]]; then
  if [[ -z "${NGROK_AUTHTOKEN:-}" ]]; then
    echo "Missing NGROK_AUTHTOKEN. Create an ngrok account and set its auth token first." >&2
    exit 2
  fi
  if [[ -z "${NGROK_DOMAIN:-}" ]]; then
    echo "Missing NGROK_DOMAIN. Set it to the static dev domain assigned in your ngrok dashboard." >&2
    exit 2
  fi
  if [[ ! "$NGROK_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "Invalid NGROK_DOMAIN. Pass only the hostname, without https:// or a path." >&2
    exit 2
  fi
fi

# Ensure Python environment exists.
if [[ ! -x .venv/bin/python ]]; then
  echo "Python environment not found; run ./setup.sh first." >&2
  exit 1
fi

# Serialize startup with the CLI and other instances of this script.
mkdir -p logs
exec 9>logs/instance.lock
flock -n 9 || { echo "another start/stop operation is in progress" >&2; exit 1; }
stop_owned_tunnel() {
  PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'from pathlib import Path; from dot_mcp.lifecycle import stop; stop(Path("logs/tunnel.json"))'
}
record_tunnel() {
  PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'import sys; from pathlib import Path; from dot_mcp.lifecycle import record; record(Path("logs/tunnel.json"), int(sys.argv[1]))' "$1"
}
cleanup_failed_start() {
  PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'from pathlib import Path; from dot_mcp.lifecycle import stop; stop(Path("logs/server.json")); stop(Path("logs/tunnel.json"))'
}
trap 'code=$?; if (( code != 0 )); then cleanup_failed_start; fi' EXIT

# Owner secret: stable per machine (persisted 0600; env wins if exported).
# Local scripts + OAuth consent share it; OAuth login tokens live separately in oauth.json.
STATE_DIR="${DOT_MCP_STATE_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/dot-mcp}"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
if [[ -z "${MCP_PATH_SECRET:-}" ]]; then
  if [[ -s "$STATE_DIR/owner_secret" ]]; then
    MCP_PATH_SECRET="$(cat "$STATE_DIR/owner_secret")"
  else
    MCP_PATH_SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    (umask 077; printf '%s\n' "$MCP_PATH_SECRET" > "$STATE_DIR/owner_secret")
    echo "Generated and saved owner secret at $STATE_DIR/owner_secret"
  fi
fi
export MCP_PATH_SECRET
unset MCP_AUTH_TOKEN
export MCP_PORT="$PORT"
# Logs may contain the secret URL — keep them owner-only.
touch mcp.log tunnel.log
chmod 600 mcp.log tunnel.log 2>/dev/null || true

# Never adopt a server started by another instance or an unrelated process.
if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'from pathlib import Path; from dot_mcp.lifecycle import owned; import sys; sys.exit(0 if owned(Path("logs/server.json")) else 1)' || {
    echo "port already has an unowned server; stop it yourself before exposing it" >&2
    trap - EXIT
    exit 1
  }
fi
# Start local MCP server if it is not already healthy.
health="$(curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health" 2>/dev/null || true)"
if ! grep -q '"tools"' <<<"$health"; then
  PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'from pathlib import Path; from dot_mcp.lifecycle import stop; stop(Path("logs/server.json"))'
  sleep 1
  nohup env PYTHONPATH="$(pwd)/src" .venv/bin/python -m dot_mcp.server > mcp.log 2>&1 9>&- &
  SERVER_PID=$!
  PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'import sys; from pathlib import Path; from dot_mcp.lifecycle import record; record(Path("logs/server.json"), int(sys.argv[1]))' "$SERVER_PID"
  for _ in $(seq 1 20); do
    health="$(curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null || true)"
    grep -q '"tools"' <<<"$health" && break
    sleep 1
  done
fi
if ! grep -q '"tools"' <<<"$health"; then
  echo "MCP server failed to start. Recent log:" >&2
  tail -30 mcp.log 2>/dev/null || true
  exit 1
fi

# Start selected tunnel.
stop_owned_tunnel
if [[ "$PROVIDER" == "ngrok" ]]; then
  if command -v ngrok >/dev/null 2>&1; then
    NGROK_BIN="$(command -v ngrok)"
  elif [[ -x ./bin/ngrok ]]; then
    NGROK_BIN="./bin/ngrok"
  else
    echo "ngrok binary not found. Install ngrok for this OS/architecture and retry." >&2
    echo "On Android/Termux, a Linux ngrok binary may not run directly; check compatibility." >&2
    exit 1
  fi
  stop_owned_tunnel
  nohup "$NGROK_BIN" http "http://127.0.0.1:$PORT" --url="$NGROK_DOMAIN" --authtoken="$NGROK_AUTHTOKEN" > tunnel.log 2>&1 9>&- &
  record_tunnel "$!"
  URL="https://$NGROK_DOMAIN"
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "$URL/health" >/dev/null 2>&1; then break; fi
    sleep 2
  done
elif [[ "$PROVIDER" == "tunnl" ]]; then
  # tunnl.gg assigns a stable random hostname based on this SSH key. No
  # provider account or key registration is required. Free tunnels expire
  # after 24h or 2h idle, so reconnect with the same key when needed.
  KEY_DIR="$STATE_DIR/ssh"
  KEY_FILE="$KEY_DIR/id_ed25519"
  mkdir -p "$KEY_DIR"
  chmod 700 "$KEY_DIR"
  if [[ ! -s "$KEY_FILE" || ! -s "$KEY_FILE.pub" ]]; then
    command -v ssh-keygen >/dev/null 2>&1 || { echo "ssh-keygen not found; install OpenSSH client." >&2; exit 1; }
    if [[ -e "$KEY_FILE" || -e "$KEY_FILE.pub" ]]; then
      echo "Incomplete SSH key pair at $KEY_FILE; refusing to overwrite it. Back it up or remove both files and retry." >&2
      exit 1
    fi
    ssh-keygen -q -t ed25519 -N '' -f "$KEY_FILE" -C dot-mcp-tunnl
    chmod 600 "$KEY_FILE"
    chmod 644 "$KEY_FILE.pub"
    echo "Created persistent tunnl.gg SSH key at $KEY_FILE"
  fi
  PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'from pathlib import Path; from dot_mcp.lifecycle import stop; stop(Path("logs/tunnel.json"))'
  : > tunnel.log
  nohup ssh -tt -i "$KEY_FILE" -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    -o ServerAliveInterval=20 \
    -o ServerAliveCountMax=3 \
    -R "80:127.0.0.1:$PORT" stable@proxy.tunnl.gg \
    > tunnel.log 2>&1 </dev/null 9>&- &
  TUNNEL_PID=$!
  echo "$TUNNEL_PID" > logs/tunnl.pid
  PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'import sys; from pathlib import Path; from dot_mcp.lifecycle import record; record(Path("logs/tunnel.json"), int(sys.argv[1]))' "$TUNNEL_PID"
  URL=""
  for _ in $(seq 1 35); do
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
      echo "tunnl.gg SSH tunnel exited unexpectedly. Recent log:" >&2
      tail -40 tunnel.log >&2 || true
      exit 1
    fi
    URL="$(grep -oE 'https://[a-z-]+-[a-f0-9]+\.tunnl\.gg' tunnel.log 2>/dev/null | head -1 || true)"
    [[ -n "$URL" ]] && break
    sleep 1
  done
  if [[ -z "$URL" ]]; then
    echo "Could not read the stable tunnl.gg URL from tunnel output. Recent log:" >&2
    tail -40 tunnel.log >&2 || true
    exit 1
  fi
elif [[ "$PROVIDER" == "serveo" ]]; then
  # Random, readable hostname; Serveo may reject a collision, so verify the
  # actual MCP endpoint before displaying it as ready.
  SERVEO_SUBDOMAIN="$(.venv/bin/python -c 'import secrets; print("dot-mcp-" + secrets.token_hex(5))')"
  URL="https://$SERVEO_SUBDOMAIN.serveo.net"
  : > tunnel.log
  nohup ssh -N -T \
    -o StrictHostKeyChecking=accept-new \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -R "$SERVEO_SUBDOMAIN:80:127.0.0.1:$PORT" serveo.net \
    > tunnel.log 2>&1 9>&- &
  TUNNEL_PID=$!
  record_tunnel "$TUNNEL_PID"
  echo "$TUNNEL_PID" > logs/serveo.pid
  # Wait for DNS/HTTPS to come up. Final MCP initialize below is authoritative.
  for _ in $(seq 1 30); do
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
      echo "Serveo SSH tunnel exited unexpectedly. Recent log:" >&2
      tail -30 tunnel.log >&2 || true
      exit 1
    fi
    if curl -fsS --max-time 3 "$URL/health" >/dev/null 2>&1; then break; fi
    sleep 2
  done
else
  CF=./bin/cloudflared
  if [[ ! -x "$CF" ]]; then
    mkdir -p bin
    case "$(uname -m)" in
      aarch64|arm64) CF_ASSET="cloudflared-linux-arm64" ;;
      x86_64|amd64) CF_ASSET="cloudflared-linux-amd64" ;;
      *)
        echo "Unsupported architecture for automatic cloudflared download: $(uname -m)" >&2
        exit 1
        ;;
    esac
    if ! curl -fsSL -o "$CF" "https://github.com/cloudflare/cloudflared/releases/latest/download/$CF_ASSET"; then
      echo "Could not download cloudflared. Install it manually and put it at $CF." >&2
      exit 1
    fi
    chmod +x "$CF"
  fi
  stop_owned_tunnel
  nohup "$CF" tunnel --url "http://127.0.0.1:$PORT" > tunnel.log 2>&1 9>&- &
  record_tunnel "$!"
  URL=""
  for _ in $(seq 1 45); do
    URL="$(grep -oE 'https://[A-Za-z0-9-]+\.trycloudflare\.com' tunnel.log 2>/dev/null | head -1 || true)"
    [[ -n "$URL" ]] && break
    sleep 2
  done
  if [[ -z "$URL" ]]; then
    echo "Cloudflare Quick Tunnel failed. Recent log:" >&2
    tail -30 tunnel.log >&2 || true
    exit 1
  fi
fi

HOST_ONLY="${URL#https://}"
export MCP_PUBLIC_HOST="$HOST_ONLY"
# Restart server so DNS-rebinding protection allows the public tunnel hostname.
PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'from pathlib import Path; from dot_mcp.lifecycle import stop; stop(Path("logs/server.json"))'
sleep 1
nohup env PYTHONPATH="$(pwd)/src" .venv/bin/python -m dot_mcp.server > mcp.log 2>&1 9>&- &
SERVER_PID=$!
PYTHONPATH="$(pwd)/src" .venv/bin/python -c 'import sys; from pathlib import Path; from dot_mcp.lifecycle import record; record(Path("logs/server.json"), int(sys.argv[1]))' "$SERVER_PID"
sleep 3

# Verify the public endpoint using MCP initialize, not only /health.
# Respect $TMPDIR (Android has no writable /tmp).
_TMP="${TMPDIR:-/tmp}"
_INIT_OUT="$_TMP/dot-mcp-initialize.out"
_MCP_URL="$URL/mcp/"
_AUTH_HEADER=()
if [[ -n "${MCP_PATH_SECRET:-}" ]]; then
  _AUTH_HEADER=(-H "Authorization: Bearer $MCP_PATH_SECRET")
fi
CODE="$(curl -sS --max-time 15 -o "$_INIT_OUT" -w '%{http_code}' -X POST "$_MCP_URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  "${_AUTH_HEADER[@]}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"dot-mcp-check","version":"1"}}}' || true)"
if [[ "$CODE" != "200" ]] || ! grep -q '"jsonrpc"' "$_INIT_OUT"; then
  echo "Public MCP initialize check failed (HTTP $CODE); endpoint may be unavailable or blocked by a tunnel interstitial." >&2
  tail -30 mcp.log >&2 || true
  [[ "$PROVIDER" == "ngrok" || "$PROVIDER" == "serveo" ]] && tail -20 tunnel.log >&2 || true
  head -c 600 "$_INIT_OUT" >&2 || true
  echo >&2
  exit 1
fi

echo
echo "============== COPY TO CHATGPT =============="
echo "MCP URL: $_MCP_URL"
echo "(auth = OAuth — approve with owner secret at $URL/authorize; local scripts use Bearer secret)"
echo "Health:  $URL/health"
if [[ "$PROVIDER" == "tunnl" ]]; then
  echo "Note: URL is tied to the saved SSH key and stays the same on reconnect. Free tunnel expires after 24h or 2h idle."
  echo "Persistent state: $STATE_DIR (keep it safe; it contains the SSH private key)."
elif [[ "$PROVIDER" == "cloudflare" ]]; then
  echo "Note: Cloudflare Quick Tunnel URL is temporary and may change next run."
elif [[ "$PROVIDER" == "ngrok" ]]; then
  echo "Note: ngrok URL remains the same while you keep using this assigned domain."
else
  echo "Note: Serveo hostname is random for this run; the next run generates another one."
fi
echo "============================================="
echo "Keep this process running while using the connector."

trap - EXIT
