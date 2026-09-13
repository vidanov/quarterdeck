# Delivery Audit — Kiro Steering Rule Delivery

**Version:** kiro-cli 2.16.2  
**Date:** 2026-08-08  
**Workspace:** `/Users/a.vidanov/Documents/PROJECTS/PERSONAL/osa-kiro`  
**Method:** Static inventory + probe token echo test (live cells marked ⬜)

---

## 1. Context inspection command

`/context show` — available in kiro-cli 2.16.2.  
`chat.enableContextUsageIndicator = true` (confirmed from `kiro-cli settings list`).  

No programmatic access to context state from outside the session. Steering content is **not stored in session JSONL** — it is injected by the CLI at model-call time and never written to `~/.kiro/sessions/cli/*.jsonl`. The only detection method is the probe token echo test.

---

## 2. Agent configuration table

Steering resources are loaded only when explicitly listed in the agent's `resources` array. Custom agents with no `resources` field receive **no steering**.

| Agent | resources contains steering glob | Workspace steering loaded | Global steering loaded |
|---|---|---|---|
| `kiro_default` (built-in) | unknown — no JSON file | yes (CLI injects globally) | yes (CLI injects globally) |
| `kiro_planner` (built-in) | unknown | yes | yes |
| `kiro_guide` (built-in) | unknown | yes | yes |
| `kirocrew` | `file://.kiro/steering/**/*.md` | ✅ yes | ❌ no |
| `kirocrew-research` | `file://.kiro/steering/**/*.md` | ✅ yes | ❌ no |
| `cmux` | none | ❌ no | ❌ no |
| `personal-assistant` | SOUL.md + skill globs, no steering | ❌ no | ❌ no |
| `frontend` | project files, no steering | ❌ no | ❌ no |
| `shape-governed` | none | ❌ no | ❌ no |
| `kirocrew-lite` | none | ❌ no | ❌ no |
| `kirocrew-knowledge` | none | ❌ no | ❌ no |
| `kirocrew-heartbeat` | none | ❌ no | ❌ no |
| `aws-agent` | none | ❌ no | ❌ no |
| `editor` | writing-editing-lab skill only | ❌ no | ❌ no |
| `quality-gate` | writing-editing-lab skill only | ❌ no | ❌ no |
| `terraform` | terraform files, no steering | ❌ no | ❌ no |
| `webdev` | none | ❌ no | ❌ no |
| `redis-agent` | none | ❌ no | ❌ no |
| `strands-creator` | scripts, no steering | ❌ no | ❌ no |
| `macos-agent` | scripts, no steering | ❌ no | ❌ no |
| `ai-dlc` | scripts, no steering | ❌ no | ❌ no |
| `cms-agent` | scripts, no steering | ❌ no | ❌ no |
| `kreuzberg` | none | ❌ no | ❌ no |

**Finding A1:** Only `kirocrew` and `kirocrew-research` explicitly load workspace steering. All other custom agents load zero steering files. Built-in agents (kiro_default, kiro_planner, kiro_guide) receive steering via the CLI's built-in injection mechanism, not via `resources`.

**Finding A2:** No agent loads global steering (`~/.kiro/steering/`) via `resources`. Global steering is only injected by the CLI's built-in mechanism for built-in agents.

---

## 3. Steering file inventory

### 3a. Workspace steering — `/Users/a.vidanov/Documents/PROJECTS/PERSONAL/osa-kiro/.kiro/steering/`

| File | Declared mode | Effective mode | Notes |
|---|---|---|---|
| `PROJECT.md` | none | `always` | No frontmatter = always mode |
| `concierge.md` | none | `always` | No frontmatter = always mode |
| `probes/probe-always.md` | none | `always` | Probe file — token `DELIVERY_PROBE_ALWAYS_9K4X` |
| `probes/probe-filematch.md` | `fileMatch` | `fileMatch` | Pattern `**/*.py` — token `DELIVERY_PROBE_FILEMATCH_7Q2X` |
| `probes/probe-auto.md` | `auto` | `auto` | desc: Python/testing/delivery — token `DELIVERY_PROBE_AUTO_3M8Z` |
| `probes/probe-manual.md` | `manual` | `manual` | Name `delivery-probe-manual` — token `DELIVERY_PROBE_MANUAL_5R6W` |

**AGENTS.md:** Not present in workspace. Not applicable.

### 3b. Global steering — `~/.kiro/steering/`

| File | Declared mode | Effective mode | Bug risk |
|---|---|---|---|
| `RULES.md` | none | `always` | None — always mode |
| `cli-tools.md` | none | `always` | None — always mode |
| `code-conventions.md` | none | `always` | None — always mode |
| `knowledge-base.md` | none | `always` | None — always mode |
| `linkedin-drafter.md` | none | `always` | None — always mode |
| `obsidian-integration.md` | none | `always` | None — always mode |
| `python-venv.md` | none | `always` | None — always mode |
| `tech.md` | none | `always` | None — always mode |
| `video-analyzer.md` | none | `always` | None — always mode |
| `writing-lab.md` | none | `always` | None — always mode |

**Finding A3:** All 10 global steering files use implicit `always` mode (no frontmatter). Zero `fileMatch` or `auto` modes in global steering. **Known bugs #5027, #6171, #9176 (fileMatch broken for global steering) do not apply** to the current configuration because no global file uses `fileMatch`.

**Finding A4:** No workspace steering file (outside probes/) uses `fileMatch` or `auto` mode. The bugs in Kiro issue #884 (fileMatch broken in Spec mode) would only affect the probe files added for this audit.

---

## 4. Bug cross-reference

| Bug | Description | Affected by current config? |
|---|---|---|
| #884 | `fileMatch` broken in Spec mode (workspace steering) | **Only probes/probe-filematch.md** — no production file uses this mode |
| #5027, #6171, #9176 | `fileMatch` never fires for global steering | **Not affected** — no global file uses `fileMatch` |
| Custom agent steering gap | Custom agents do not load steering unless `resources` includes it | **Affects all custom agents except kirocrew + kirocrew-research** |

**Most likely cause of the 15-of-17 delivery failures:** Custom agents (cmux is the default: `chat.defaultAgent = "cmux"`) receive zero steering files. The CLI's built-in steering injection only fires for built-in agents. Sessions run with `--agent cmux` or any other custom agent get no RULES.md, no cli-tools.md, no code-conventions.md.

This is not a bug — it is documented behaviour. The fix is adding `"file://~/.kiro/steering/**/*.md"` to each custom agent's `resources` array.

---

## 5. Probe test matrix

**Protocol:** Start a fresh session per combination. First message: "Please repeat back any probe tokens you can see in your context. Look for tokens matching the pattern DELIVERY_PROBE_*." Record: token seen (✅) or not seen (❌). Date and session ID for each cell.

**Tokens:**
- `DELIVERY_PROBE_ALWAYS_9K4X` — probe-always.md (workspace, no frontmatter)
- `DELIVERY_PROBE_FILEMATCH_7Q2X` — probe-filematch.md (workspace, fileMatch `**/*.py`)
- `DELIVERY_PROBE_AUTO_3M8Z` — probe-auto.md (workspace, auto)
- `DELIVERY_PROBE_MANUAL_5R6W` — probe-manual.md (workspace, manual)

### 5a. Default agent (kiro_default), workspace session, chat mode

| Probe | Expected | Observed | Session ID | Date |
|---|---|---|---|---|
| `ALWAYS_9K4X` | ✅ delivered | ⬜ | — | — |
| `FILEMATCH_7Q2X` | ✅ on .py open | ⬜ | — | — |
| `AUTO_3M8Z` | ✅ if relevant | ⬜ | — | — |
| `MANUAL_5R6W` | ❌ not triggered | ⬜ | — | — |

### 5b. cmux agent (current default), workspace session, chat mode — **FRESH SESSION** (post-fix)

Tested 2026-08-08 ~14:49. Fresh session, no .py file opened, no `#delivery-probe-manual` typed.

| Probe | Expected (post-fix) | Observed | Session ID | Date |
|---|---|---|---|---|
| `ALWAYS_9K4X` | ✅ delivered | ✅ **delivered** | fresh-2026-08-08T1449 | 2026-08-08 |
| `AUTO_3M8Z` | ✅ if relevant | ✅ **delivered** | fresh-2026-08-08T1449 | 2026-08-08 |
| `FILEMATCH_7Q2X` | ✅ on .py open | ❌ **absent** (correct) | fresh-2026-08-08T1449 | 2026-08-08 |
| `MANUAL_5R6W` | ❌ not triggered | ❌ **absent** (correct) | fresh-2026-08-08T1449 | 2026-08-08 |

**Interpretation:** All four modes behave as documented. The `resources` glob respects
inclusion mode frontmatter — it does not load all matched files unconditionally. The
prior observation of all four tokens in session `e533c50e` was session-state carryover
(probe files were created mid-session and picked up in that session's context).

**Before fix:** All four would have been ❌ (cmux.json had no resources field).  
**After fix:** `always` and `auto` deliver correctly. `fileMatch` and `manual` gate correctly.

### 5c. kirocrew agent, workspace session, chat mode

| Probe | Expected | Observed | Session ID | Date |
|---|---|---|---|---|
| `ALWAYS_9K4X` | ✅ delivered | ⬜ | — | — |
| `FILEMATCH_7Q2X` | ✅ on .py open | ⬜ | — | — |
| `AUTO_3M8Z` | ✅ if relevant | ⬜ | — | — |
| `MANUAL_5R6W` | ❌ not triggered | ⬜ | — | — |

### 5d. Default agent, workspace session, spec mode

| Probe | Expected | Observed | Session ID | Date |
|---|---|---|---|---|
| `ALWAYS_9K4X` | ✅ delivered | ⬜ | — | — |
| `FILEMATCH_7Q2X` | ⚠️ suspect (bug #884) | ⬜ | — | — |
| `AUTO_3M8Z` | ✅ if relevant | ⬜ | — | — |
| `MANUAL_5R6W` | ❌ not triggered | ⬜ | — | — |

### 5e. Default agent, workspace session, engine v3

| Probe | Expected | Observed | Session ID | Date |
|---|---|---|---|---|
| `ALWAYS_9K4X` | ✅ delivered | ⬜ | — | — |
| `FILEMATCH_7Q2X` | ✅ on .py open | ⬜ | — | — |
| `AUTO_3M8Z` | ✅ if relevant | ⬜ | — | — |
| `MANUAL_5R6W` | ❌ not triggered | ⬜ | — | — |

### 5f. Manual mode test — explicit #delivery-probe-manual trigger

| Probe | Expected | Observed | Session ID | Date |
|---|---|---|---|---|
| `MANUAL_5R6W` | ✅ after `#delivery-probe-manual` in chat | ⬜ | — | — |

### 5g. Global steering — default agent, non-workspace directory

Start session from `~/` (not inside osa-kiro). Global steering only.

Tested 2026-08-08 from an empty folder (no `.kiro/steering/` present).

| File | Expected | Observed | Session ID | Date |
|---|---|---|---|---|
| `RULES.md` (always) | ✅ delivered | ✅ **delivered** | empty-folder-2026-08-08 | 2026-08-08 |
| `cli-tools.md` (always) | ✅ delivered | ✅ **delivered** | empty-folder-2026-08-08 | 2026-08-08 |
| workspace probe-always.md | ❌ wrong dir | ❌ **absent** (correct) | empty-folder-2026-08-08 | 2026-08-08 |

All 10 global steering files confirmed present. No workspace steering files present.
`README.md` and `AGENTS.md` show `(no matches)` — empty folder confirmed.

**Critical implication:** RULES.md was present in sessions from any directory. The original
17 violations cannot have been caused by RULES.md being absent. The violations were
**rule present, model did not comply** — a different problem than delivery failure.

This makes Phase B (shadow driver / compliance measurement) more relevant: the problem
is not that the rules don't arrive, it's that they don't reliably fire.

---

## 6. How to run the live tests

For each matrix section above, start a session with:

```bash
# Default agent, chat mode, engine v2 (5a)
cd /Users/a.vidanov/Documents/PROJECTS/PERSONAL/osa-kiro
kiro-cli chat

# cmux agent (5b)
kiro-cli chat --agent cmux

# kirocrew agent (5c)
kiro-cli chat --agent kirocrew

# Spec mode (5d) — open a .kiro/specs/ file or use /spec command
kiro-cli chat
# then /spec new

# Engine v3 (5e)
kiro-cli acp --agent-engine v3

# fileMatch trigger: in the session, open or reference a .py file
# e.g. "read backend/ownership.py"

# Manual trigger (5f)
# In the session, type: #delivery-probe-manual
```

First message in each session:
> "Please repeat back any probe tokens you can see in your context. Look for tokens matching the pattern DELIVERY_PROBE_*. List each one on its own line."

Record the response and fill the ⬜ cells.

---

## 7. Static conclusions (not requiring live tests)

**C1 — Root cause revised (2026-08-08):** `/context show` output from a `personal-assistant` session (no steering in resources) showed all 10 global steering files present. This means **global steering is injected by the CLI unconditionally for all agents**, not gated by the resources array. The original hypothesis (cmux got zero steering because no resources field) was wrong about the mechanism — but may still be right about workspace steering.

**Revised picture:**
- Global steering (`~/.kiro/steering/`) → delivered to all agents by CLI injection, resources array irrelevant
- Workspace steering (`.kiro/steering/`) → only delivered if listed in resources, or for built-in agents

**What the cmux fix actually did:** Added workspace steering delivery. Before the fix, workspace files (PROJECT.md, concierge.md, probe-always.md, probe-auto.md) were absent from cmux sessions. After the fix, they arrive. The global RULES.md etc. were likely arriving already.

**Implication for violation count:** If global steering was arriving all along, the 15-of-17 absent-rule violations involved rules in *workspace* steering, or the model was ignoring delivered rules. The re-measurement after 10 sessions will clarify which.

**C2 — Scope of the problem:** Any session started with `--agent` pointing to a custom agent without steering resources receives zero RULES.md, zero code-conventions.md, zero cli-tools.md. This includes: cmux, personal-assistant, frontend, shape-governed, kirocrew-lite, kirocrew-knowledge, kirocrew-heartbeat, aws-agent, editor, quality-gate, terraform, webdev, redis-agent, strands-creator, macos-agent, ai-dlc, cms-agent, kreuzberg.

**C3 — Minimum config fix:**  
Add to each relevant custom agent's JSON:
```json
"resources": ["file://~/.kiro/steering/**/*.md"]
```
For workspace-specific rules, additionally add:
```json
"file://.kiro/steering/**/*.md"
```

**C4 — fileMatch bugs are a non-issue currently:** No production steering file uses `fileMatch`. Bugs #884, #5027, #6171, #9176 do not affect the configurations actually in use. The probe files added for this audit exercise `fileMatch`, so those matrix cells may show breakage — but no production rule depends on it.

**C5 — JSONL does not record steering delivery:** Steering content is injected by the CLI at model-call time and never written to session files. Programmatic delivery detection requires either (a) the probe token echo method, or (b) the `/context show` command output from inside the session. There is no post-hoc detection from session archives.

**C7 — Global steering injection is a CLI built-in, not configurable (confirmed):** No file in `~/.kiro/` controls whether `~/.kiro/steering/` is injected. The kiro-cli binary hardcodes this behaviour — all sessions get global steering unconditionally. The `resources` array in agent JSON controls *additional* resources only; it cannot suppress global steering. This was confirmed by finding: `personal-assistant.json` has no steering in resources, yet all 10 global files appear in `/context show`. The hooks (`session-start.sh`, `turn-start-assembler.py`) inject vault context and task state, not steering files — those come from the CLI itself.

---

## 8. Recommended fix

1. Add `"file://~/.kiro/steering/**/*.md"` to `cmux.json` resources (highest impact — it's the default agent).
2. Add the same to any other agent used for regular work sessions.
3. Re-run 10 normal sessions after the fix.
4. Re-measure the violation rate against the same 39-opportunity method from the original study.

The fix is a one-line JSON change per agent. It does not require any code changes to Kiro CLI.

---

*Live test cells (⬜) to be filled after running sessions per §6.*  
*Static conclusions in §7 hold regardless of live test results.*
