#!/bin/bash
# verify-build-fresh.sh — check that the running Quarterdeck app matches the
# source tree before claiming any fix is done.
#
# Exit codes:
#   0 — build is fresh (stale: false)
#   1 — build is stale or backend unreachable
#
# Usage: call at the end of any turn that claims a Quarterdeck bug is fixed.
# A non-zero exit makes the claim invalid by construction.

set -euo pipefail

PORT=19418
ENDPOINT="http://127.0.0.1:${PORT}/api/health/build"

# ── Fetch ────────────────────────────────────────────────────────────────────
RESPONSE=$(curl -sf --max-time 5 "$ENDPOINT" 2>/dev/null || true)

if [ -z "$RESPONSE" ]; then
    echo ""
    echo "════════════════════════════════════════════════════════════════"
    echo "  ⚠  FRESHNESS CHECK FAILED — backend not reachable at port ${PORT}"
    echo "     The app may not be running, or a rebuild is in progress."
    echo "     Do not claim this fix is done until the app is running"
    echo "     and this check passes."
    echo "════════════════════════════════════════════════════════════════"
    echo ""
    exit 1
fi

# ── Parse ─────────────────────────────────────────────────────────────────────
STALE=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('stale', True))" 2>/dev/null || echo "True")
REASON=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('stale_reason',''))" 2>/dev/null || echo "")
SHA=$(echo "$RESPONSE"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('git_short','?'))" 2>/dev/null || echo "?")
FILES=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); f=d.get('changed_files',[]); print(', '.join(f) if f else '')" 2>/dev/null || echo "")
UPTIME=$(echo "$RESPONSE"| python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('uptime_s',0))" 2>/dev/null || echo "0")

# ── Report ───────────────────────────────────────────────────────────────────
if [ "$STALE" = "True" ] || [ "$STALE" = "true" ]; then
    echo ""
    echo "════════════════════════════════════════════════════════════════"
    echo "  ✗  STALE BUILD — the running app does not match the source tree"
    echo "     Reason:  ${REASON:-source changed}"
    [ -n "$FILES" ] && echo "     Files:   $FILES"
    echo "     SHA:     $SHA  |  uptime: ${UPTIME}s"
    echo ""
    echo "     Run:  cd $(git rev-parse --show-toplevel 2>/dev/null || echo '.')  &&  ./build-app.sh --install"
    echo "     Then: open -a Quarterdeck"
    echo ""
    echo "     A claim that a Quarterdeck fix is done is INVALID while this"
    echo "     check reports stale: true."
    echo "════════════════════════════════════════════════════════════════"
    echo ""
    exit 1
else
    echo "  ✓  Build fresh — sha=${SHA}  uptime=${UPTIME}s"
fi
