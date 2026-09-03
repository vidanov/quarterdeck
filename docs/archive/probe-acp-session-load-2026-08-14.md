# ACP session/load Probe — 2026-08-14

**Probe: ACP session/load against tmux-owned V3 session — 2026-08-14**

Verdict: ⚠ conflicted

| Question | Result |
|---|---|
| session/load succeeds | no |
| ACP writes to messages.jsonl | yes ⚠ |
| tmux session survives load | yes |
| Events arrive for tmux-driven turns | no / not tested (load failed) |
| Transcript parseable after probe | yes |

**Details:**
- Session id: `sess_f0d68aef-0636-4153-9a83-2525d410615b`
- messages.jsonl lines: before=2, after load=13, after turn=0
- ACP events seen: (none)

**Implication for Task 2:**
session/load failed or tmux session was killed.
ACP cannot attach to a session it does not own.
12g falls back to tmux send-keys for all V3 sessions.
