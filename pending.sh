#!/bin/bash
# Inspect and cancel spawns that never reported a kiro session id.
#
#   ./pending.sh                 list them, with the last lines of each pane
#   ./pending.sh cancel <nonce>  give up on one (kills its tmux session)
#   ./pending.sh cancel --all    give up on all of them
#   ./pending.sh attach <nonce>  attach to one in this terminal
#
# Why this exists: a pending card reads "Starting…" whether the spawn is stuck
# or sitting at a prompt waiting for a human. Two of them turned out to be
# `kiro-cli login` — one holding an authorized device code and an unanswered IAM
# profile picker — and none of that was visible from the UI, which offered only
# a × to throw it away. The pane tail is the difference between killing a stalled
# process and killing your own half-finished login.
#
# Goes through backend.tmux_manager rather than editing managed.json directly,
# so the state write is the same atomic one the app uses.
set -e
cd "$(dirname "$0")"

PY=venv/bin/python
[ -x "$PY" ] || PY=python3

case "${1:-list}" in
  list)
    "$PY" - <<'EOF'
import sys, time
sys.path.insert(0, ".")
from backend import tmux_manager as tmux

pending = tmux.load_state()["pending"]
if not pending:
    print("No pending spawns.")
    raise SystemExit
live = set(tmux.list_tmux_sessions())
print(f"{len(pending)} pending spawn(s):\n")
for nonce, entry in pending.items():
    name = entry.get("tmux", "")
    age = (time.time() - entry.get("spawned_at", 0)) / 60
    mark = "tmux alive" if name in live else "tmux GONE"
    print(f"  {nonce}  {mark}  {age:.0f}m old")
    print(f"    task: {entry.get('task') or '(none)'}")
    print(f"    cwd : {entry.get('cwd')}")
    if name in live:
        pane = tmux._tmux("capture-pane", "-p", "-t", name, "-S", "-6", check=False)
        tail = [l for l in pane.strip().split("\n") if l.strip()][-4:]
        # The point of the whole script: what is it actually waiting for?
        for line in tail:
            print(f"    | {line[:150]}")
    print(f"    cancel: ./pending.sh cancel {nonce}")
    print(f"    attach: ./pending.sh attach {nonce}")
    print()
EOF
    ;;
  cancel)
    [ -n "${2:-}" ] || { echo "Usage: ./pending.sh cancel <nonce>|--all" >&2; exit 2; }
    "$PY" - "$2" <<'EOF'
import sys
sys.path.insert(0, ".")
from backend import tmux_manager as tmux

target = sys.argv[1]
nonces = list(tmux.load_state()["pending"]) if target == "--all" else [target]
if not nonces:
    print("No pending spawns.")
    raise SystemExit
for nonce in nonces:
    result = tmux.cancel_pending(nonce)
    if result.get("ok"):
        killed = "killed its tmux session" if result.get("killed_tmux") else "tmux was already gone"
        print(f"{nonce}: abandoned, {killed}")
    else:
        print(f"{nonce}: {result.get('error')}", file=sys.stderr)
EOF
    ;;
  attach)
    [ -n "${2:-}" ] || { echo "Usage: ./pending.sh attach <nonce>" >&2; exit 2; }
    exec tmux attach -t "osa-pending-$2"
    ;;
  *)
    sed -n '2,8p' "$0"
    exit 2
    ;;
esac
