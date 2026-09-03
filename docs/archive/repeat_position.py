#!/usr/bin/env python3
"""
Does an agent repeat itself because a session is LONG, or because it is LATE
in that session?

Long-session explanation: more tool calls means more chances to collide by
accident. Purely mechanical, no degradation.

Late-in-session explanation: the agent loses track as context fills. Each
session is its own control, so length cannot explain it.

Output A distinguishes them. Flat histogram means the quartile gradient was
opportunity. Rising histogram means degradation.

Usage:
    python3 repeat_position.py ~/.kiro/sessions/cli
"""

import json
import sys
from pathlib import Path
from collections import Counter, defaultdict


def norm(obj):
    """Stable string for a tool input, so equal inputs compare equal."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def tool_calls(path):
    """Yield (name, normalized_input) in order. Streams: file may be 500+ MB."""
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("kind") != "AssistantMessage":
                continue
            for item in (evt.get("data") or {}).get("content") or []:
                if item.get("kind") == "toolUse":
                    d = item.get("data") or {}
                    yield d.get("name"), norm(d.get("input"))


def main(root):
    files = sorted(Path(root).glob("*.jsonl"))
    if not files:
        print(f"no .jsonl files under {root}")
        return

    BUCKETS = 10
    hist = [0] * BUCKETS            # position of every repeat occurrence
    per_session = []                # (turns, n_repeats)
    tool_repeats = Counter()

    for path in files:
        calls = list(tool_calls(path))
        total = len(calls)
        if total < 2:
            continue

        seen = defaultdict(int)
        n_rep = 0
        for i, key in enumerate(calls):
            seen[key] += 1
            if seen[key] > 1:       # every occurrence after the first
                n_rep += 1
                pos = i / (total - 1) if total > 1 else 0.0
                hist[min(int(pos * BUCKETS), BUCKETS - 1)] += 1
                tool_repeats[key[0]] += 1

        per_session.append((total, n_rep))

    total_rep = sum(hist)
    print(f"sessions with >=2 tool calls : {len(per_session)}")
    print(f"repeat occurrences found     : {total_rep}\n")

    if total_rep == 0:
        print("No repeats. Nothing to test.")
        return

    # ---- A. THE ACTUAL TEST -------------------------------------------
    print("A. WHERE IN THE SESSION DO REPEATS HAPPEN")
    print("   (each session normalised to 0-100% of its own tool calls)\n")
    expected = total_rep / BUCKETS
    for b in range(BUCKETS):
        lo, hi = b * 10, (b + 1) * 10
        n = hist[b]
        bar = "#" * round(n / max(hist) * 40) if max(hist) else ""
        print(f"   {lo:3d}-{hi:3d}%  {n:6d}  {bar}")
    print(f"\n   flat would be ~{expected:.0f} per bucket")

    first_half = sum(hist[:5])
    last_half = sum(hist[5:])
    ratio = last_half / first_half if first_half else float("inf")
    print(f"   first half {first_half}  |  last half {last_half}  "
          f"|  ratio {ratio:.2f}")
    if ratio > 1.5:
        print("   -> repeats cluster LATE. Degradation signal. Real.")
    elif ratio < 0.67:
        print("   -> repeats cluster EARLY. Not degradation.")
    else:
        print("   -> roughly FLAT. The quartile gradient was opportunity,")
        print("      not degradation. M2 does not support the hypothesis.")

    # ---- B. re-run quartiles on TURN COUNT, not characters ------------
    print("\nB. REPEAT RATE BY TURN-COUNT QUARTILE")
    print("   (characters are distorted by one 545 MB session)\n")
    per_session.sort(key=lambda r: r[0])
    n = len(per_session)
    for q in range(4):
        chunk = per_session[q * n // 4:(q + 1) * n // 4]
        if not chunk:
            continue
        with_rep = sum(1 for turns, r in chunk if r > 0)
        print(f"   Q{q+1}  tool calls {chunk[0][0]:>5}-{chunk[-1][0]:>6}  "
              f"sessions {len(chunk):>4}  with repeats {with_rep:>4}  "
              f"({with_rep/len(chunk)*100:.1f}%)")
    print("\n   If this gradient survives here too, size effect is real.")

    # ---- C. which tools ----------------------------------------------
    print("\nC. TOOLS MOST REPEATED")
    for name, cnt in tool_repeats.most_common(8):
        print(f"   {cnt:6d}  {name}")
    print("\n   A read-only tool dominating means harmless re-reading.")
    print("   A mutating tool dominating means real wasted work.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         str(Path.home() / ".kiro" / "sessions" / "cli"))
