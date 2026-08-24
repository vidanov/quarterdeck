"""Denied command patterns for preToolUse auto-deny.

Patterns are stored in ~/.osa-kiro/deny-patterns.json as a list of objects:
  {"id": "uuid", "tool": "execute_bash", "pattern": "rm -rf /", "note": "...", "enabled": true}

`tool` defaults to "execute_bash" if omitted. The pattern is a Python regex
matched against a compact JSON representation of tool_input for that tool.
`enabled` defaults to true if omitted (backwards compatible).
"""
import json
import re
import uuid
from pathlib import Path

from .config import STATE_DIR

DENY_FILE = STATE_DIR / "deny-patterns.json"

DEFAULT_PATTERNS = [
    {"id": "default-1", "tool": "execute_bash", "enabled": True,
     "pattern": r"rm\s+-rf\s+/(?!\S)", "note": "rm -rf /"},
    {"id": "default-2", "tool": "execute_bash", "enabled": True,
     "pattern": r"git\s+push\s+.*--force", "note": "force push"},
    {"id": "default-3", "tool": "execute_bash", "enabled": True,
     "pattern": r"DROP\s+TABLE|TRUNCATE\s+TABLE", "note": "destructive SQL"},
    {"id": "default-4", "tool": "execute_bash", "enabled": True,
     "pattern": r":(){ :\|:& };:", "note": "fork bomb"},
]

# Named packs — curated pattern sets users can install in one click.
# Each pack entry has a stable id so install is idempotent.
PACKS: dict[str, dict] = {
    "aws-safety": {
        "name": "AWS Safety Pack",
        "description": "Blocks the most dangerous AWS CLI operations: account/org deletion, "
                       "public S3 exposure, security group wide-open rules, IAM privilege "
                       "escalation, CloudTrail disabling, and credential exposure.",
        "patterns": [
            # ── Account / organisation nukes ──────────────────────────────────
            {"id": "aws-s1", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+organizations\s+delete-organization\b",
             "note": "AWS: delete organization"},
            {"id": "aws-s2", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+organizations\s+remove-account-from-organization\b",
             "note": "AWS: remove account from org"},
            {"id": "aws-s3", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+account\s+close-account\b",
             "note": "AWS: close account"},

            # ── S3 public exposure ────────────────────────────────────────────
            {"id": "aws-s4", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+s3api\s+put-bucket-acl\b.*\b(public-read|public-read-write|authenticated-read)\b",
             "note": "AWS: S3 bucket made public via ACL"},
            {"id": "aws-s5", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+s3api\s+delete-public-access-block\b",
             "note": "AWS: S3 remove public access block"},
            {"id": "aws-s6", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+s3api\s+put-bucket-policy\b.*\*.*Effect.*Allow",
             "note": "AWS: S3 bucket policy allows *"},

            # ── Security groups — wide open ───────────────────────────────────
            {"id": "aws-s7", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+ec2\s+authorize-security-group-(ingress|egress)\b.*0\.0\.0\.0/0",
             "note": "AWS: security group rule open to 0.0.0.0/0"},

            # ── IAM privilege escalation ──────────────────────────────────────
            {"id": "aws-s8", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+iam\s+attach-(user|role|group)-policy\b.*AdministratorAccess",
             "note": "AWS: attach AdministratorAccess policy"},
            {"id": "aws-s9", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+iam\s+create-policy-version\b.*--set-as-default",
             "note": "AWS: IAM policy version set as default (escalation vector)"},
            {"id": "aws-s10", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+iam\s+delete-account-password-policy\b",
             "note": "AWS: delete account password policy"},

            # ── CloudTrail / Config — audit disabling ─────────────────────────
            {"id": "aws-s11", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+cloudtrail\s+(stop-logging|delete-trail)\b",
             "note": "AWS: disable or delete CloudTrail"},
            {"id": "aws-s12", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+configservice\s+delete-configuration-recorder\b",
             "note": "AWS: delete Config recorder"},

            # ── Credential / secret exposure ──────────────────────────────────
            {"id": "aws-s13", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+iam\s+create-access-key\b",
             "note": "AWS: create IAM access key (credential creation)"},

            # ── KMS key deletion ──────────────────────────────────────────────
            {"id": "aws-s14", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+kms\s+schedule-key-deletion\b",
             "note": "AWS: schedule KMS key deletion"},
            {"id": "aws-s15", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+kms\s+disable-key\b",
             "note": "AWS: disable KMS key"},

            # ── RDS / database nukes ──────────────────────────────────────────
            {"id": "aws-s16", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+rds\s+delete-db-(instance|cluster)\b.*--skip-final-snapshot",
             "note": "AWS: delete RDS without final snapshot"},

            # ── EC2 termination protection bypass ─────────────────────────────
            {"id": "aws-s17", "tool": "execute_bash", "enabled": True,
             "pattern": r"aws\s+ec2\s+modify-instance-attribute\b.*DisableApiTermination.*false",
             "note": "AWS: disable EC2 termination protection"},
        ],
    },
    "crew-safety": {
        "name": "Crew Safety Pack",
        "description": "Protects against the most dangerous operations an autonomous kirocrew "
                       "agent might execute: self-modification of steering/hooks, mass file "
                       "deletion, git history rewrites, credential exfiltration, and runaway "
                       "process spawning.",
        "patterns": [
            # ── Steering / hook self-modification ─────────────────────────────
            {"id": "crew-s1", "tool": "execute_bash", "enabled": True,
             "pattern": r"(rm|unlink|truncate)\b.+\.kiro/(steering|hooks|settings\.json)",
             "note": "Crew: delete steering files or hooks"},
            {"id": "crew-s2", "tool": "execute_bash", "enabled": True,
             "pattern": r"(rm|unlink)\b.+\.kiro/sessions",
             "note": "Crew: delete kiro session data"},

            # ── Mass deletion ─────────────────────────────────────────────────
            {"id": "crew-s3", "tool": "execute_bash", "enabled": True,
             "pattern": r"rm\s+-rf\s+~(?!\S)",
             "note": "Crew: rm -rf home directory"},
            {"id": "crew-s4", "tool": "execute_bash", "enabled": True,
             "pattern": r"rm\s+-rf\s+~/Documents",
             "note": "Crew: rm -rf ~/Documents"},
            {"id": "crew-s5", "tool": "execute_bash", "enabled": True,
             "pattern": r"rm\s+-rf\s+~/Desktop",
             "note": "Crew: rm -rf ~/Desktop"},

            # ── Git history rewrite ───────────────────────────────────────────
            {"id": "crew-s6", "tool": "execute_bash", "enabled": True,
             "pattern": r"git\s+(rebase\s+--onto|filter-branch|filter-repo)\b",
             "note": "Crew: git history rewrite"},
            {"id": "crew-s7", "tool": "execute_bash", "enabled": True,
             "pattern": r"git\s+push\b.*--force(?!-with-lease)",
             "note": "Crew: git force push (without lease)"},

            # ── Credential / token exfiltration ──────────────────────────────
            {"id": "crew-s8", "tool": "execute_bash", "enabled": True,
             "pattern": r"curl\b.+(Authorization:|Bearer\s+\$|--header.*token)",
             "note": "Crew: curl with auth header (possible exfil)"},
            {"id": "crew-s9", "tool": "execute_bash", "enabled": True,
             "pattern": r"cat\b.*(\.env|\.netrc|id_rsa|id_ed25519|credentials)\b",
             "note": "Crew: read credentials/private keys"},
            {"id": "crew-s10", "tool": "execute_bash", "enabled": True,
             "pattern": r"(printenv|env)\s*\|?\s*(curl|nc|ncat|wget|python|node)\b",
             "note": "Crew: pipe env vars to network tool"},

            # ── Runaway process / background spawn ────────────────────────────
            {"id": "crew-s11", "tool": "execute_bash", "enabled": True,
             "pattern": r"nohup\b.+&\s*$",
             "note": "Crew: background process with nohup"},
            {"id": "crew-s12", "tool": "execute_bash", "enabled": True,
             "pattern": r"(crontab\s+-[lr]|crontab\s+[^-])",
             "note": "Crew: crontab read/write"},

            # ── SSH / tunnel ──────────────────────────────────────────────────
            {"id": "crew-s13", "tool": "execute_bash", "enabled": True,
             "pattern": r"ssh\b.+(-R\s+\d+|-L\s+\d+|-D\s+\d+)",
             "note": "Crew: SSH tunnel/port-forward"},

            # ── Obsidian vault wipe ───────────────────────────────────────────
            {"id": "crew-s14", "tool": "execute_bash", "enabled": True,
             "pattern": r"rm\b.+Obsidian\s+Vault",
             "note": "Crew: delete Obsidian vault"},
        ],
    },
}


def _load() -> list[dict]:
    if not DENY_FILE.exists():
        return []
    try:
        data = json.loads(DENY_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(patterns: list[dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DENY_FILE.write_text(json.dumps(patterns, indent=2))


def list_patterns() -> list[dict]:
    return _load()


def add_pattern(tool: str, pattern: str, note: str = "") -> dict:
    patterns = _load()
    entry = {"id": str(uuid.uuid4())[:8], "tool": tool or "execute_bash",
             "pattern": pattern, "note": note, "enabled": True}
    patterns.append(entry)
    _save(patterns)
    return entry


def set_enabled(pattern_id: str, enabled: bool) -> bool:
    """Toggle a pattern on or off. Returns True if pattern was found."""
    patterns = _load()
    for p in patterns:
        if p.get("id") == pattern_id:
            p["enabled"] = enabled
            _save(patterns)
            return True
    return False


def remove_pattern(pattern_id: str) -> bool:
    patterns = _load()
    before = len(patterns)
    patterns = [p for p in patterns if p.get("id") != pattern_id]
    if len(patterns) == before:
        return False
    _save(patterns)
    return True


def install_pack(pack_id: str) -> tuple[int, int]:
    """Install a named pack. Returns (added, skipped) counts.

    Idempotent — patterns with matching stable ids are not duplicated.
    """
    pack = PACKS.get(pack_id)
    if not pack:
        raise ValueError(f"Unknown pack: {pack_id}")
    existing_ids = {p.get("id") for p in _load()}
    added = 0
    patterns = _load()
    for p in pack["patterns"]:
        if p["id"] in existing_ids:
            continue
        patterns.append({**p})
        added += 1
    if added:
        _save(patterns)
    skipped = len(pack["patterns"]) - added
    return added, skipped


def remove_pack(pack_id: str) -> int:
    """Remove all patterns that belong to a named pack. Returns count removed."""
    pack = PACKS.get(pack_id)
    if not pack:
        raise ValueError(f"Unknown pack: {pack_id}")
    pack_ids = {p["id"] for p in pack["patterns"]}
    patterns = _load()
    before = len(patterns)
    patterns = [p for p in patterns if p.get("id") not in pack_ids]
    removed = before - len(patterns)
    if removed:
        _save(patterns)
    return removed


def list_packs() -> list[dict]:
    """Return pack metadata with installed status for each pattern."""
    existing_ids = {p.get("id") for p in _load()}
    result = []
    for pack_id, pack in PACKS.items():
        total = len(pack["patterns"])
        installed = sum(1 for p in pack["patterns"] if p["id"] in existing_ids)
        result.append({
            "id": pack_id,
            "name": pack["name"],
            "description": pack["description"],
            "total": total,
            "installed": installed,
        })
    return result


def matches(tool_name: str, tool_input: dict) -> tuple[bool, str]:
    """Return (matched, note) if any enabled pattern matches this tool call."""
    patterns = _load()
    # Compact representation to match against
    input_str = json.dumps(tool_input, separators=(",", ":"))
    for p in patterns:
        # Skip disabled patterns
        if not p.get("enabled", True):
            continue
        if p.get("tool", "execute_bash") != tool_name:
            continue
        try:
            if re.search(p["pattern"], input_str, re.IGNORECASE):
                return True, p.get("note", p["pattern"])
        except re.error:
            continue
    return False, ""
