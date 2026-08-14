#!/bin/bash
# verify-claim.sh — stop hook: detects unverified completion claims.
#
# Fires on every `stop` event. Reads the session JSONL tail to check whether:
#   1. The final assistant message contains a completion claim keyword
#   2. No observation tool was run after the last user message
#
# If both: write an unverified_claim record and fire a notification.
#
# Install: add to kiro agent config as a `stop` hook alongside the existing
# deck-turn-end hook, or replace it:
#
#   "stop": "KIRO_SESSION_ID=$KIRO_SESSION_ID ~/.osa-kiro/hooks/verify-claim.sh; echo '{}'"
#
# Tuning:
#   - Claim keywords: CLAIM_PATTERNS below
#   - Observation tools: OBSERVATION_TOOLS below (presence of any suppresses the flag)
#   - Only fires when the turn also touched a file (FILES_REQUIRED=1)
#
# ponytail: keyword detection is crude; upgrade to classifier only if FP rate annoys.

set -euo pipefail

SESSION_ID="${KIRO_SESSION_ID:-}"
PORT="${DECK_PORT:-19418}"
API="http://127.0.0.1:${PORT}"
TOKEN_FILE="${HOME}/.osa-kiro/local-token"

# ── Skip early if no session ──────────────────────────────────────────────────
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

# ── Find the session JSONL ────────────────────────────────────────────────────
JSONL="${HOME}/.kiro/sessions/cli/${SESSION_ID}.jsonl"
if [ ! -f "$JSONL" ]; then
    exit 0
fi

# ── Analyse the last turn via Python ─────────────────────────────────────────
RESULT=$(python3 - "$JSONL" <<'PYEOF'
import json, re, sys

CLAIM_PATTERNS = re.compile(
    r'\b('
    r'done|fixed|works now|verified|should work|complete|completed|'
    r'all tests pass|tests pass|build (succeeded|passed)|'
    r'bug is fixed|issue is (resolved|fixed)|problem is (fixed|resolved|solved)|'
    r'successfully (fixed|resolved|implemented|deployed|installed)|'
    r'the fix (is|works|applied)|'
    r'commit[a-z]* [a-f0-9]{6,}|'  # "committed abc1234"
    r'pushed to|'
    r'installed\b.*\bopen|'          # "installed. open -a"
    r'✅.*done|done.*✅'
    r')\b',
    re.IGNORECASE | re.DOTALL,
)

# Tools that count as "observation" — running them means the claim has evidence
OBSERVATION_TOOLS = {
    "shell", "bash", "run_command", "execute", "terminal",
    "computer", "str_replace_editor",
}

# Tools that are FILE edits — claim is more suspicious if only writes, no reads
WRITE_ONLY_TOOLS = {"write", "create_file", "str_replace", "patch"}

path = sys.argv[1]
with open(path) as f:
    lines = [l for l in f.read().splitlines() if l.strip()]

# Walk backwards to find the last turn (HumanMessage → end)
last_human_idx = None
for i in range(len(lines) - 1, -1, -1):
    try:
        d = json.loads(lines[i])
    except Exception:
        continue
    if d.get("kind") == "HumanMessage":
        last_human_idx = i
        break

if last_human_idx is None:
    print("NO_HUMAN")
    sys.exit(0)

# Everything after the last human message = this turn
turn_lines = lines[last_human_idx + 1:]

final_text = ""
tools_used = []

for line in turn_lines:
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get("kind") == "AssistantMessage":
        for item in d.get("data", {}).get("content", []):
            if item.get("kind") == "text":
                final_text = item.get("data", "")
            elif item.get("kind") == "toolUse":
                tools_used.append(item.get("data", {}).get("name", ""))

if not final_text:
    print("NO_TEXT")
    sys.exit(0)

# Check for claim keyword
m = CLAIM_PATTERNS.search(final_text)
if not m:
    print("NO_CLAIM")
    sys.exit(0)

claim_text = final_text[max(0, m.start()-60):m.end()+60].replace("\n", " ").strip()
claim_match = m.group(0)

# Check if any observation tool was used
observation_tools = [t for t in tools_used if t.lower() in OBSERVATION_TOOLS]
write_only = all(t.lower() in WRITE_ONLY_TOOLS for t in tools_used) if tools_used else True

if observation_tools:
    print("HAS_OBSERVATION")
    sys.exit(0)

# Claim without observation — fire
last_seq = len(lines) - 1
result = {
    "claim_text": claim_text,
    "claim_match": claim_match,
    "observed_tools": tools_used,
    "last_message_seq": last_seq,
}
print(json.dumps(result))
PYEOF
)

# ── Check result ──────────────────────────────────────────────────────────────
case "$RESULT" in
    NO_HUMAN|NO_TEXT|NO_CLAIM|HAS_OBSERVATION)
        exit 0
        ;;
esac

# Parse JSON result
CLAIM_TEXT=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['claim_text'])" 2>/dev/null || echo "")
CLAIM_MATCH=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['claim_match'])" 2>/dev/null || echo "")
LAST_SEQ=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['last_message_seq'])" 2>/dev/null || echo "0")
TOOLS=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['observed_tools']))" 2>/dev/null || echo "[]")

# ── POST to Quarterdeck API ───────────────────────────────────────────────────
if [ -f "$TOKEN_FILE" ]; then
    TOKEN=$(cat "$TOKEN_FILE")
    curl -sf --max-time 5 \
        -X POST "${API}/api/sessions/${SESSION_ID}/unverified-claim" \
        -H "Content-Type: application/json" \
        -H "X-Local-Token: ${TOKEN}" \
        -d "{\"claim_text\": $(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$CLAIM_TEXT"), \"observed_tools\": ${TOOLS}, \"last_message_seq\": ${LAST_SEQ}}" \
        > /dev/null 2>&1 || true
fi

# ── Notify ────────────────────────────────────────────────────────────────────
# cmux if available, fallback to osascript
if command -v cmux &>/dev/null && [ -n "${CMUX_WORKSPACE_ID:-}" ]; then
    cmux notify --title "⚠ Unverified claim" --body "\"${CLAIM_MATCH}\" — no observation in this turn" 2>/dev/null || true
else
    osascript -e "display notification \"Claimed '${CLAIM_MATCH}' — no verification tool ran in this turn\" with title \"⚠ Unverified claim (Quarterdeck)\"" 2>/dev/null || true
fi

exit 0
