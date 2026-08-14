#!/usr/bin/env bash
# Serve Quarterdeck on the tailnet so sessions can be driven from a phone.
#
# Binds to the Tailscale interface only — never 0.0.0.0 — and keeps the Mac
# awake for as long as it runs, since a sleeping Mac drops off the tailnet and
# takes every managed session with it.
set -euo pipefail

cd "$(dirname "$0")"

# REMOTE_PORT, not the local one: this binds the Tailscale address, so it does
# not collide with a local backend on the same number, and a phone's saved URL
# keeps working whichever Quarterdeck is running on this Mac.
PORT="${PORT:-$(python3 -c 'from backend.config import REMOTE_PORT; print(REMOTE_PORT)' 2>/dev/null || echo 19418)}"
TOKEN_FILE="$HOME/.osa-kiro/token"

# --- token ---------------------------------------------------------------
if [ ! -s "$TOKEN_FILE" ]; then
  mkdir -p "$(dirname "$TOKEN_FILE")"
  openssl rand -hex 32 > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  echo "[remote] generated a new token at $TOKEN_FILE"
fi
chmod 600 "$TOKEN_FILE"

# --- address -------------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
  echo "[remote] tailscale not found. Install it, or this would have to bind to a wider address." >&2
  exit 1
fi

TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
if [ -z "$TS_IP" ]; then
  echo "[remote] no Tailscale IPv4 address — is tailscale up?" >&2
  exit 1
fi

# --- frontend ------------------------------------------------------------
if [ ! -d frontend/dist ]; then
  echo "[remote] frontend/dist missing — building it, remote clients are served from there"
  (cd frontend && npm run build)
fi

# --- run -----------------------------------------------------------------
if [ -d venv ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

echo
echo "  Open on the phone:  http://$TS_IP:$PORT/app/"
echo "  Token:              $(cat "$TOKEN_FILE")"
echo
echo "  The token is asked for once and kept in a cookie for 30 days."
echo "  Mac stays awake while this runs; Ctrl-C lets it sleep again."
echo

# `caffeinate -si` holds off system and idle sleep for the lifetime of the
# child. On a laptop running on battery the system-sleep assertion is ignored,
# so keep it on power if you want it reachable with the lid shut.
exec caffeinate -si uvicorn backend.api:app --host "$TS_IP" --port "$PORT"
