#!/usr/bin/env python3
"""Check every configured MCP server actually starts, and flag stale pins.

kiro-cli reports a broken MCP server only as "1 MCP failure — see /mcp", and
only inside a running agent. A server pinned to @latest can break with no local
change: awslabs.aws-api-mcp-server declares `mcp>=1.23.0` with no upper bound,
so uvx resolved mcp 2.x, which renamed McpError to MCPError, and the server
crashed on import at every agent start for days.

The workaround is a `--with 'mcp<2'` constraint in the uvx args. That lives in
~/.kiro/settings/mcp.json, which is not in any repo, so nothing would ever
remind you to drop it once upstream ships a fix. This script is that reminder:
for any server carrying a `--with` pin it also probes the unpinned form, and
says so when the pin is no longer needed.

Usage:
    python scripts/mcp-health.py            # probe every enabled server
    python scripts/mcp-health.py --all      # include disabled ones too

Exit code is the number of failing servers, so CI or a hook can gate on it.

SAFETY: server env blocks hold live credentials. They are passed to the child
process because the server needs them, and never printed.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

CONFIG = Path.home() / ".kiro" / "settings" / "mcp.json"
TIMEOUT = 40

HANDSHAKE = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "mcp-health", "version": "1"}},
}) + "\n"


def probe(spec: dict, drop_pin: bool = False) -> tuple[bool, str]:
    """Start the server and see whether it answers `initialize`.

    Returns (ok, first stderr line). A server that answers and keeps running is
    healthy — staying open is what a stdio MCP server is supposed to do, so a
    timeout with a reply already in hand still counts as OK.
    """
    args = list(spec.get("args", []))
    if drop_pin:
        # Strip `--with <constraint>` pairs to test the unconstrained form.
        out, i = [], 0
        while i < len(args):
            if args[i] == "--with":
                i += 2
                continue
            out.append(args[i])
            i += 1
        args = out

    env = {**os.environ, **(spec.get("env") or {})}
    try:
        proc = subprocess.Popen([spec["command"], *args],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env)
    except FileNotFoundError:
        return False, f"command not found: {spec['command']}"

    try:
        out, err = proc.communicate(HANDSHAKE, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    finally:
        if proc.poll() is None:
            proc.kill()

    first_err = next((line for line in (err or "").splitlines() if line.strip()), "")
    return '"result"' in (out or ""), first_err


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="probe disabled servers too")
    opts = parser.parse_args()

    try:
        servers = json.loads(CONFIG.read_text())["mcpServers"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"cannot read {CONFIG}: {exc}", file=sys.stderr)
        return 1

    failures, stale_pins = 0, []
    for name, spec in servers.items():
        if spec.get("disabled") and not opts.all:
            print(f"{name:<38} skipped (disabled)")
            continue

        ok, err = probe(spec)
        pinned = "--with" in spec.get("args", [])
        note = ""

        if ok and pinned:
            # The pin is a workaround for an upstream break. Check whether the
            # break is still there; if not, the pin is dead weight.
            unpinned_ok, _ = probe(spec, drop_pin=True)
            if unpinned_ok:
                stale_pins.append(name)
                note = "  ← PIN NO LONGER NEEDED, drop --with"
            else:
                note = "  (pin still required)"

        if ok:
            print(f"{name:<38} OK{note}")
        else:
            failures += 1
            print(f"{name:<38} FAILED   {err[:100]}")

    print()
    if stale_pins:
        print(f"{len(stale_pins)} stale pin(s): {', '.join(stale_pins)}")
    print(f"{failures} failing server(s)" if failures else "all probed servers healthy")
    return failures


if __name__ == "__main__":
    sys.exit(main())
