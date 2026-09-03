# Quarterdeck: measurement and memory

A build plan. Supersedes `folder-shell-build-plan.md`, which specified building a shell from scratch. **That is cancelled.** Quarterdeck already is the shell, and KiroCrew covers the parts Quarterdeck deliberately does not.

**v2.** Merges `session-record-2026-08-08.md` in, per that document's own instruction: "Fold it in and discard this file." Everything from the session record's Part I (sections 1–10) was already reflected here and is cross-referenced rather than duplicated. Part II of the session record (sections 11–18) was genuinely new and is added below as Tasks 7–11 and Part 6B. Section 1.5 and the Appendix explain exactly what moved where, and what still needs the fuller `ROADMAP.md` this document doesn't contain.

---

# PART 1: INTRO

## 1.1 What changed

The earlier plan specified a driver, an orchestrator, a board, a liveness monitor, a secrets store, and a notification layer. All of it exists:

- **Quarterdeck** already provides the authorization plane, the audit trail, the grid, the remote surface, hook installation, and session adoption.
- **KiroCrew** (Apache-2.0, `kirodotdev/KiroCrew`) provides the Gateway, multiplexed ACP sessions, schedules, subagents, and lessons.

So the remaining work is small and specific. Quarterdeck is an **authorization and observation plane with no memory.** Nothing accumulates across sessions. That is the gap, and it is the only gap worth building.

Three additions, plus one structural fix that has to come first.

## 1.2 Evidence tags

| Tag | Meaning | Treatment |
|---|---|---|
| **[M]** | Measured | Reliable, sample size stated |
| **[Q]** | Read from Quarterdeck's own docs | Verify against the running build |
| **[V]** | Vendor documentation | Re-check the version |
| **[D]** | Derived | Plausible, unproven |
| **[S]** | Speculative | Test before building on it |

If an observation contradicts a **[Q]** or **[V]** claim, the observation wins. Record it with the date.

## 1.3 The measured problem

**[M]** 23 sessions labelled by hand. 39 eligible opportunities, 17 rule violations. **In 15 of 17, the rule had never entered the model's context.** Not ignored. Absent.

**[M]** Two rules had zero violations across eight opportunities, so documentation sometimes works unaided.

**[M]** Across 418 sessions in thirty days, ~8.6 percent hit a repair loop where the agent kept patching instead of diagnosing. Those ran 6.5x longer than clean ones.

**[M]** One such loop cost ~20,000 tokens from first wrong action to human correction. Delivering the rule costs ~175. Break-even is roughly one injection in fifty.

## 1.4 Two prior measurement failures

**[M]** Both produced confident numbers that were wrong, and both were chosen because they were easy to compute rather than because they measured the claim.

*Repeated tool calls as evidence of forgetting.* The gradient looked strong. Then a check showed 421 of 430 repeated pairs had a file modification in between, meaning correct re-verification after a change.

*A correctness checker for timetable software.* Four of eight runs failed one check. The check was wrong. After fixing it, zero of ten failed.

**Consequence, applied throughout: no automated judgment is believed until validated against a hand-checked sample, in both directions.**

## 1.5 What this merge changed [NEW]

Reading the session record against this plan, three things fell out:

1. **Most of it was already here.** The 15-of-17 finding, the two measurement failures, the ownership/takeover model, correction capture, the denominator requirement, and the two finding classes (target-relative vs. target-independent) were already fully specified in Parts 2–5 below, in some cases with more implementation detail than the session record itself. No new work from those sections.
2. **Five ideas were genuinely new** and had no home: duration estimation, secrets handling, liveness states with a spawned-process registry, board layering, and compaction management. These become **Tasks 7–11**, added after the existing six.
3. **One real conflict surfaced.** Part 6 of this plan cancels "double-buffered session handoff" on the grounds that KiroCrew's multiplexed runtime already addresses it. The session record's compaction design (§15.3 there, Task 11 here) proposes double-buffering again, scoped specifically to compaction overlap rather than general session handling, and grounds it in Quarterdeck's own `--resume-id`. These may not actually be the same claim, but they weren't reconciled before this merge. See Task 11 and the flagged row in Part 6.

Everything else new — the moving-target doctrine, the "captain" role, and two smaller backlog notes — didn't fit as buildable tasks. They're doctrine and backlog, filed under **Part 6B**, and several of them reference sections of a larger `ROADMAP.md` (10b–10g, 7d, sections 4/12/13) that this document does not contain. Where that's true, it's called out explicitly rather than guessed at.

---

# PART 2: WHAT ALREADY EXISTS

Do not rebuild any of this.

## 2.1 Quarterdeck

**[Q]** From the changelog and release checklist:

| Capability | Detail |
|---|---|
| **Enforcement** | `preToolUse` gating, per-session opt-in via `~/.osa-kiro/gates/<session-id>`, nonce-keyed pre-correlation gates, timeout, default-deny, UI toggle warning when the hook is absent |
| **Trace** | `postToolUse` audit to `~/.osa-kiro/audit/<date>.jsonl`, 90-day retention, sensitive keys redacted at write time, requests logged in middleware including refused ones |
| **Turn boundary** | `stop` hook gives real end-of-turn, not a freshness heuristic. Not nonce-guarded, so foreign sessions benefit |
| **Correlation** | `agentSpawn` injects `DECK_NONCE` via tmux env; hook writes `KIRO_SESSION_ID` to `~/.osa-kiro/spawns/$DECK_NONCE`; `correlated_via` records the route |
| **Hook installer** | Marker-scoped merge into agent configs, preserves existing hooks, keeps a backup, reports stale vs missing vs current, live coverage count |
| **Transcript** | `GET /api/sessions/{id}/messages?after=<seq>`, stable JSONL `seq`, role, message id, tool metadata, oversized results become addressable placeholders |
| **Grid** | Attention-first: "Needs you" as cards, "Working" as collapsed lines, ordered by how stuck a session is. Card reply without opening the detail panel |
| **Task stack** | `~/.osa-kiro/stacks/`, add/reorder/edit/delete/send-next, auto-advance driven by `stop`, refuses to send into thinking or awaiting-approval |
| **Adoption** | Take over a foreign session; hand a managed session to Terminal.app |
| **Remote** | Tailscale-only source addresses, single-use QR exchange code with 2-minute expiry, token rotation, LaunchAgent, 10 dispatch and 60 input per minute rate limits |

**[Q] Important correction to earlier analysis.** Prior work concluded `postToolUse` does not fire in chat-mode sessions. Quarterdeck's audit trail is built on it. Quarterdeck's evidence wins.

## 2.2 Quarterdeck doctrine, which constrains every solution below

**[Q]** From `SPEC.md`:

1. **Read-only on `~/.kiro/sessions/cli/`.** Never modify. Watch and read only.
2. **Local-first.** No network required.
3. **Real-time.** Status within 2 seconds of file changes.
4. **No-config start.** Works by watching the standard sessions directory.

Doctrine 1 is the binding one. **Anything this plan adds writes under `~/.osa-kiro/`, never into session files.**

## 2.3 KiroCrew, for reference

**[V]** Gateway routing to managed agent sessions, each driven over ACP against `kiro-cli`, with `agent.provider` fixed to `acp`. Sessions may run as a dedicated ACP process or as a handle on a shared multiplexed runtime. Workspace-scoped lessons from corrections, skills synthesized from repeated patterns, cron and heartbeats, subagents, credential redaction, 137 deny patterns, a tightest-wins governance ceiling, and `kirocrew security events / audit / verify`.

**Consequence for Quarterdeck: KiroCrew sessions land in the same directory Quarterdeck watches.** That is Part 3.

## 2.4 What is missing from both

Nothing accumulates in a way that is *measured*. KiroCrew claims lessons apply in future sessions and that skills load only when relevant. **Neither claim has been verified by anyone.** Quarterdeck makes no such claim and has no memory at all.

## 2.5 Prior art, so nothing gets claimed twice [NEW]

**[V] ZORO** (arXiv 2604.15625, April 2026). Same overall shape: rules files are passive, so anchor them to every step, enrich the plan with relevant rules, enforce during implementation, evolve from user feedback. **Enforces by asking the agent to prove it complied — self-report.** Quarterdeck's exit-code gate (4.2 below) is a different class of evidence, and that is the one durable architectural advantage this plan has over it.

**[V] Hermes** (Nous Research, MIT). Persistent memory of successes and failures generating reusable skills, isolated subagents with their own terminals, filesystem checkpoints and rollback, a pre-execution scanner for terminal commands, cron delivery to seven messaging surfaces, and the `agentskills.io` portability standard.

**[V] OpenClaw** (Apache/MIT, formerly Clawdbot). Heartbeat daemon, AgentSkill system with a registry, long-term version-controlled memory in local Markdown inside a workspace directory, and a Gateway multiplexing sessions on one port.

**[V] Also relevant.** MemRepair treats failed validation as a first-class write rather than a discarded outcome. MemoryCode benchmarks cross-session instruction retrieval with distractors present. SWE-Bench-CL restructures a standard benchmark into chronological per-repository streams with forgetting and transfer metrics. `agentmemory` decays low-confidence entries after seven days — by time, not by relevance to a target.

**[V] Read before publishing any priority claim:** arXiv 2604.07192, "Compact Constraint Encoding for LLM Code Generation: Token Economics and Constraint Compliance." Closest existing work to the break-even analysis in 1.3.

None of this changes the plan. It changes what gets claimed as novel when any of this is written up: the measurement method (2.4, Part 4.3, Task 2) is scarce in this literature; self-report-free enforcement (4.2) is not unique but is uncommon; a verified-not-claimed memory layer is the gap everyone else leaves open.

## 2.6 Why the ownership and gate mechanisms are shaped this way [NEW]

**[V]** Coordination avoidance via shared invariants is established distributed-systems theory: Bailis and colleagues formalised invariant confluence around 2014. If every operation preserves an invariant, concurrent execution and merging preserve it too, so no coordination is required.

**The test, in one question: if two workers each satisfy a constraint independently and you merge their work, does the constraint still hold?**

| Answer | Consequence |
|---|---|
| Yes | Merge-safe. Check it locally, per worker. No owner needed |
| No | Needs a single owner |

Merge-safe, by this test: forbidden patterns, key-derivation rules, path disjointness, style. **Not** merge-safe, therefore needing an owner: uniqueness (one migration), ordering, global budgets, port and container allocation, frozen interfaces, referential integrity across workers.

**[D]** This is why Part 3's ownership model exists (one owner per session, one path to `handoverable`) and why Part 4.2's gates are scoped per-rule rather than global: scope is the mechanism for deciding, per rule, which side of this table it's on. A rule with `scope_globs` that overlap between two workers is a candidate for "needs an owner," not a gate.

---

# PART 3: SESSION OWNERSHIP (build this first)

## 3.1 The conflict

Quarterdeck's model assumes one session equals one unit of human attention. Three things break it, and one of them is already happening.

**Already happening: KiroCrew subagents.** Crew spawns isolated subagents that return results to a parent. Each one is a `kiro-cli` session in the watched directory, so each becomes a Quarterdeck card. A fan-out of five research subagents produces five cards, none individually meaningful.

**Coming: SuperChat or any multi-session feature.** Same shape, worse ratio.

**Structural: successor sessions.** Any handoff pattern that replaces a session with a fresh one seeded from a summary leaves the predecessor on disk. It shows as an idle card forever.

And the second half of the problem is worse than clutter: **adopting a machine-owned session means two writers.** Quarterdeck sends input; the orchestrator sends input; the session state is corrupted and neither party knows why. This is the failure mode the session record calls "the takeover gap" — see 3.8.

## 3.2 The model

Every session has an owner, a role, and a group.

| Field | Values | Meaning |
|---|---|---|
| `owner` | `human`, or an orchestrator name such as `kirocrew`, `superchat` | Who sends input |
| `role` | `primary`, `worker`, `successor`, `retired` | What it is within its group |
| `group_id` | opaque id, or null | Which unit of work it belongs to |
| `handoverable` | bool | Whether adoption is legal |
| `visible` | bool | Whether it appears as its own card |

**Defaults, and they must be safe.** A session with no ownership record is `owner: human`, `role: primary`, `handoverable: true`, `visible: true`. An unknown session must never be hidden, because a silently hidden session is worse than a cluttered grid.

## 3.3 Where it lives

**Doctrine 1 forbids writing into session files.** So ownership records are sidecars under Quarterdeck's own directory:

```
~/.osa-kiro/owners/<session-id>.json
{ "owner": "superchat", "role": "worker",
  "group_id": "grp_7f2a", "handoverable": false,
  "visible": false, "declared_at": "...", "declared_by": "..." }
```

Whoever spawns a machine-owned session writes the sidecar **before dispatching the first prompt.** Not after. A worker that runs before its sidecar exists appears as a human-owned card, and someone will take it over.

## 3.4 Foreign orchestrators

KiroCrew will not write Quarterdeck's sidecars. So Quarterdeck needs a read-only adapter.

**[V]** Crew keeps its state under `~/.kiro/crew`, including session records. Read it, derive ownership, cache it. Read-only, so doctrine holds.

**[S]** Whether Crew's state exposes the parent-child relationship, and under what schema, is unverified. Task 1 checks it.

**If the adapter cannot determine ownership, do not guess.** Mark those sessions `owner: unknown`, keep them visible, and mark them `handoverable: false` with a reason shown in the UI. Visible and refused is honest. Hidden is not.

## 3.5 Grid behaviour

- A group shows **one card** representing the group, with a worker count and the group's aggregate state.
- Drilling into the group card lists its workers. Individual workers are never top-level cards.
- `role: retired` sessions are excluded from the grid entirely but remain readable through the transcript API. That is where predecessors go.
- A setting shows hidden sessions, off by default. **[Q]** Filters already persist to `localStorage` per device, so this fits the existing pattern.

## 3.6 Adoption rules

- `handoverable: false` disables the take-over control, with the owner named in the tooltip. Not a silent no-op.
- The API refuses adoption of a non-handoverable session with a specific error, because the UI is not the only caller.
- **Release protocol.** An owner may write `"released": true` into the sidecar, which restores `handoverable`. This is the only path from machine-owned back to human-owned. Without it, adoption is permanently refused and that is the correct default.
- Handing a managed session **to** Terminal.app stays legal for human-owned sessions only.

## 3.7 If you build a SuperChat

Three rules, and they follow from the above rather than being separate.

1. **Every child session gets a sidecar before its first prompt**, with `handoverable: false` and `visible: false`.
2. **The parent is the only handoverable session in the group.** SuperChat itself is not handoverable, because its state lives partly outside the session file and a human takeover would see half of it.
3. **On completion or abandonment, children are marked `retired`, not deleted.** Their transcripts stay readable. **[Q]** Quarterdeck already refuses to delete running sessions, so extend that guard to cover machine-owned ones.

## 3.8 Cross-check against the session record's "takeover gap" [NEW]

The session record raises this independently, calling it "the real gap": that detecting a subagent (3.4) is not the same as refusing to take it over, and that takeover kills the pid, so taking over a live worker is silent destruction of work in progress, not just a second writer.

**No new work here.** 3.6 and 3.7 already specify exactly this: a fifth-value-shaped `owner`/`handoverable` pair (rather than a bare `control` enum), sidecar-before-first-prompt, parent-only handoverability, retire-don't-delete. The session record's own framing — *"you already fixed this exact class of bug once,"* citing a prior pty-api handover where marking a mid-adoption session as `foreign` was the worse failure — is the same argument used to justify 3.4's "visible and refused, never hidden" rule. **Recorded here so it is not rediscovered as a gap a second time.**

---

# PART 4: THE THREE ADDITIONS

## 4.1 Correction capture

**The measured problem is that rules do not arrive. The measured signal for that is a human correction.** It is the only ground truth in the system: a judgment of failure, made at the time, by the person qualified to make it.

**[M]** Prior attempts to detect corrections by keyword were 93 percent false positives, because automated pipelines used words like "wrong" and "again" as batch markers. **So capture is manual, one keystroke, not inferred.**

A button on the card. One press writes:

```
~/.osa-kiro/corrections/<date>.jsonl
{ "session_id": "...", "group_id": "...", "owner": "...",
  "ts": "...", "last_message_seq": 4127,
  "steering_commit": "a3f91c2",
  "rules_in_context": ["frontend-checks.md", "aws-accounts.md"],
  "rule_id": null,
  "status": "open",
  "note": "" }
```

Four fields carry the weight:

**`steering_commit`.** The git hash of the steering tree at that moment. A correction is evidence of a violation only relative to what the rules said when the work happened. Without it, "the requirement changed" gets counted as a violation, and the count inflates in the direction that makes the project look necessary.

**`rules_in_context`.** Which steering files actually reached the model. This is the 15-of-17 measurement, captured continuously instead of by hand. Requires 4.3.

**`status`.** `open`, `confirmed`, `withdrawn`. Only `confirmed` counts. You will sometimes correct the agent and be wrong, or change your mind afterwards. Withdrawing must be one click. **[D]** Reviewing only the corrections that confirm what you expected, and skipping the ones that don't, moves every downstream rate toward the answer you started with. Apply the same both-directions discipline used in Task 5's validation to this log too.

**`last_message_seq`.** **[Q]** The messages API already provides stable `seq`, so the correction points at exactly what was being corrected.

**Attribution across the group.** A correction pressed on a group card is about a worker. **[Q]** `agentSpawn` correlation and `correlated_via` already exist, so record both `session_id` and `group_id` and let the analysis join them later. **Never infer the target from the correction text.**

## 4.2 Per-rule gates

**[Q]** Today a gate is per-session and all-or-nothing: `~/.osa-kiro/gates/<session-id>` exists, so every tool call is held for approval.

That is an authorization plane. It is not memory. The upgrade:

```
~/.osa-kiro/rules/<rule-id>.json
{ "rule_id": "no-client-aws-accounts",
  "check_cmd": ".osa-kiro/checks/aws-account.sh",
  "scope_globs": ["**/*.tf", "**/cdk/**"],
  "tools": ["execute_bash", "use_aws"],
  "provenance": { "correction_ids": ["..."], "created_at": "..." },
  "review_by": "2026-11-01",
  "mode": "warn" }
```

The `preToolUse` hook evaluates applicable rules. A non-zero exit blocks the call and returns the reason. Everything else passes without prompting.

**This is the whole thesis in one change.** "Approve every call from my phone" becomes "silently block the one thing that bit me last week." Same hook, same directory, same plumbing.

**Three requirements, none optional.**

**`mode` starts at `warn`.** A new rule logs what it would have blocked and blocks nothing. Promotion to `block` is manual, after reading the log. **[M]** Two automated judgments in this project were invalid; a rule promoted on first sight would be the third, and this one would block real work.

**Every rule ships a negative fixture.** A real past case where it must **not** fire. Prior work had only positive fixtures, and that is exactly what let a broken checker through undetected.

**Every rule has `provenance` and `review_by`.** Which corrections produced it, and when to reconsider it. A rule that has not fired in ninety days is either dead weight or fully internalized by the codebase, and both mean retire.

**Scope is mandatory.** `scope_globs` plus `tools`. Without scoping, rules accumulate globally and by month three the agent is refused everywhere for reasons nobody can reconstruct. See 2.6 for why scope is also the merge-safety test.

## 4.3 Delivery recording

**[Q]** The hook installer already reports live coverage. Extend the concept from *installed* to *delivered*.

Per turn, record which steering files were actually in the assembled context. Two mechanical definitions, and no others:

1. The file's content appeared in the assembled prompt.
2. The trace shows the file being read or searched.

**Never infer delivery from behaviour.** An agent following a rule is not evidence the rule arrived. An agent breaking one is not evidence it was absent. **[M]** That distinction is the entire finding.

**[V]** Kiro has four inclusion modes: `always`, `fileMatch` on a glob, `auto` routed by description, and `manual` via `#name`. **[V]** Known problems: custom agents do not load steering unless it is in the agent config's `resources`; `fileMatch` is reported broken in Spec mode for workspace steering; `fileMatch` never fires for global steering, with `auto` as the documented workaround.

**[Q] Trap.** `~/.kiro/sessions/cli/*.jsonl` contains `Prompt` events with no `meta.timestamp`. In one 615-session sample, 918 of 9,396 lacked it. Those are injected context, not user input. Treating them as user prompts corrupts every turn boundary. But note: **those injected prompts are where delivered steering appears**, so they are the signal, not noise.

---

# PART 5: TASKS

In order. Each has a DONE WHEN. Some have a STOP. Tasks 1–6 are unchanged from v1. **Tasks 7–11 are new**, drawn from the session record's Part II, and are lower-confidence: several depend on sections of the broader `ROADMAP.md` (noted per task) that this document does not contain, so treat their scoping as a first pass, not a spec.

---

### TASK 1: Ownership adapter and grid fix

Build first. Without it, every later task measures a grid full of sessions nobody owns.

1. Implement the sidecar format in 3.3. Read-only on session files.
2. Implement safe defaults per 3.2. Unknown means visible and human-owned.
3. Read `~/.kiro/crew` and derive ownership for Crew-managed sessions. **Read-only.** Record the schema you found and its version in `crew-adapter.md`.
4. Where ownership cannot be determined, mark `owner: unknown`, keep visible, set `handoverable: false` with a reason shown in the UI.
5. Group cards per 3.5. One card per group, workers on drill-in, `retired` excluded from the grid but readable via the transcript API.
6. Adoption refusal per 3.6, enforced in the API and not only the UI. Implement the `released` flag.
7. Add the show-hidden setting, default off.

**DONE WHEN** all five hold:

- A Crew fan-out of three or more subagents shows as **one** group card, not three.
- A session with no sidecar appears normally and is adoptable.
- A `handoverable: false` session refuses adoption from the API with a specific error naming the owner.
- Setting `released: true` restores adoptability.
- A retired predecessor is absent from the grid and still readable through `/api/sessions/{id}/messages`.

**STOP if `~/.kiro/crew` does not expose the parent-child relationship.** Report what it does expose. Grouping then needs a different signal, and guessing produces a grid that hides real work.

---

### TASK 2: Delivery recording

1. Per turn, record which steering files reached the context, using only the two definitions in 4.3.
2. Handle the injected-`Prompt` case explicitly. Those events carry the signal.
3. Write to `~/.osa-kiro/delivery/<date>.jsonl`, keyed by session and turn.
4. Show it: per-session, which rules are live right now. **[Q]** The settings panel already shows hook coverage, so this is the same shape one level down.

**Then test the vendor's mechanism, not your config.** For each of the four inclusion modes, place a unique probe token in a steering file, trigger the mode, and ask the agent to echo any probe tokens it can see. Record a matrix of mode against context:

- default agent versus each custom agent config
- workspace versus global steering
- chat session versus spec session
- a Quarterdeck-adopted session versus a Crew-managed one

**DONE WHEN** `delivery-audit.md` contains the full matrix with CLI version and date, and the per-session live-rules view works.

**This is the deliverable that stands alone.** Even if nothing else in this plan gets built, a measured rule-delivery matrix for a shipping tool is a result nobody has.

---

### TASK 3: Correction button

1. Add the control to the card and to the detail panel.
2. Write the record in 4.1 on one press. No dialog, no required fields.
3. Populate `steering_commit` from the git hash of the steering tree, `rules_in_context` from Task 2, `last_message_seq` from the messages API.
4. Record both `session_id` and `group_id`. **Never infer the target from text.**
5. Add confirm and withdraw, one click each. Only `confirmed` counts.
6. Reject and log any record missing a required field. An incomplete record still gets counted, which is worse than none.
7. One report command: per rule, the number of distinct sessions with a confirmed correction, and the delivered versus not-delivered split.

**DONE WHEN** a correction pressed on a group card resolves to the specific worker session, its task, and the steering commit in force at its dispatch.

**Do not compute a recurrence rate yet.** That needs a denominator of eligible opportunities, which is separate manual labelling. Raw counts only.

---

### TASK 4: Per-rule gates

1. Implement the rule format in 4.2. Extend the existing `preToolUse` hook; do not write a second one.
2. `mode: warn` is the only mode a new rule may have. Promotion to `block` is a manual edit.
3. Enforce `scope_globs` and `tools`. An unscoped rule is rejected at load.
4. Build **one** rule first, for the strongest measured pattern: the agent continuing to patch after a repeated failure instead of stopping to diagnose. **[M]** 3 of 5 eligible opportunities across three distinct sessions.
5. Write both fixtures. A positive one from the session that motivated it. **A negative one from a real session where a command failed repeatedly as legitimate progress**, where the rule must not fire.
6. Keep the existing per-session all-or-nothing gate. It is the right tool for a session you do not trust yet.

**DONE WHEN** both fixtures pass, the rule runs in `warn` mode for a week, and the warn log is readable.

**Do not build gates for the other rules.** **[M]** They have two observations each, which is not enough.

**STOP before promoting anything to `block`** until the warn log shows it would have fired on real violations and not on correct work. Report the counts. A rule that never fires is dead weight; a rule that false-blocks costs trust in every future block.

---

### TASK 5: Recurrence, with a denominator

Only after Tasks 2 through 4 have run for a few weeks.

1. Freeze the classification criteria **before reading any logs**: documented before the session, unambiguous read cold, still current. Corrections failing those are style drift, changed requirements, or things never written down.
2. Label eligibility **blind to the outcome**. Truncate each session at the first assistant message, label from the prompts and the steering state at that date, freeze, then reveal the rest. Blinding must be a property of the artifact, not of restraint.
3. Count recurrence as distinct sessions per rule, using confirmed corrections only.
4. Report the delivered versus not-delivered split. **[M]** It was 15 to 2 by hand, which is why delivery comes before enforcement.

**DONE WHEN** the report states sample sizes, raw counts, and a confidence interval on every rate.

**Report a negative result plainly.** If recurrence is low once the denominator exists, there is nothing to accumulate and Task 4 should be reverted rather than extended.

---

### TASK 6: Findings, optional

Only if Task 5 shows recurrence is real.

1. `~/.osa-kiro/findings.json`. Each entry: statement, evidence, and a **validity condition expressed as a command**. A finding with no cheap way to re-check it is not durable and must not be written.
2. Two classes. `target_relative`, meaning decisions and progress, which die when the goal changes. `target_independent`, meaning eliminations, environment facts, and discovered constraints, which outlive every goal.
3. Save `target_independent` liberally and unfiltered. **Put the relevance filter on the load side**, where the question is known. At save time it is a guess about the future.
4. Harvest on turn end via the existing `stop` hook, and again before any compaction, because compaction destroys the trajectory.
5. Show findings in the detail panel. Editable and deletable. **[V]** KiroCrew keeps its memory inspectable and editable for the same reason.

**DONE WHEN** a `target_independent` finding established under one goal is loaded by a session working toward a different goal. A single-class implementation fails that test.

---

### TASK 7: Duration measurement and calibrated estimates [NEW]

Every threshold elsewhere in this plan (stall detection in Task 9, budget ceilings) is currently a hand-written guess. This task makes the guesses replaceable by data the system already generates for free.

1. Record per completed task: wall-clock duration, token spend, tool-call count, **distinct** tool-call count, oracle attempts before passing, final verdict, a type tag. **[Q]** The stats view and audit trail already exist, so this is a new writer over existing plumbing.
2. Record, before dispatch, the features that make prediction possible: model, effort, whether a task string was supplied, cwd, project, whether gating was on, whether the stack was auto-advancing.
3. Estimate with the simplest calibratable thing: **median and p90 of completed tasks sharing a type tag.** No model, no regression.
4. **Report a range, never a point estimate.** **[M]** Sessions that hit a repair loop ran 6.5x longer than clean ones; a mean is meaningless on that distribution. Example: `task-07 migration est 8-25 min (n=6, p90 25m)`.
5. **Say "no estimate" when n is small.** An estimate from two observations is worse than none, because it will be planned against.
6. **Calibrate continuously.** Log predicted range against actual for every task. Report the fraction of actuals falling inside the range. **If a p90 range is exceeded far more than 10 percent of the time, report the estimator as uncalibrated and show no number.** Two automated judgments elsewhere in this project were confidently wrong; a displayed uncalibrated estimate would be the third and the most visible one.

**DONE WHEN** at least one task type has n ≥ 6, shows a range instead of a point, and the calibration report exists and is checked at least once against actuals.

**Unlocks, in order of value once this exists:** data-derived budget ceilings (replace hand-written numbers with p95 of comparable tasks); per-type stall thresholds for Task 9 (a single global silence threshold is wrong in both directions — a build is legitimately silent for minutes, an edit is not); honest "should I wait or leave" information for the user.

**[S] Out of scope for now:** sharing duration data across projects. Depends on the same transfer question as Q6/Q7 in Part 7 — unmeasured, keep estimates project-scoped until transfer is demonstrated.

**Depends on:** the stats view and audit trail (2.1), which this extends rather than replaces.

---

### TASK 8: Secrets the agent must never see [NEW]

Current practice — pasting a key into the conversation, or hand-editing files with shell tricks — writes the secret permanently into the session log. **The agent should declare which secrets it needs by name and never receive a value.**

**Five tiers, prefer the highest that applies:**

| Tier | Mechanism | Use for |
|---|---|---|
| 0 — Discovery | Scan the project for `process.env.X`, `os.environ[...]`, `.env.example`, CI config. Show which names are missing, pre-populated | Finding out what's needed |
| 1 — The field | Masked input in a Settings panel, project-scoped, rendered outside the transcript, never a chat turn. Value goes to the OS keychain. Panel shows name, set-date, sessions that used it — **never the value, no reveal toggle** | Entry point for everything below |
| 2 — Injection | **[Q]** Quarterdeck spawns via `tmux new-session`, so inject the secret into that environment. The value never reaches a context window | Anything reading `process.env` |
| 3 — Ephemeral materialization | Write `.env` at spawn (for tools like Vite/Next that read from disk, not environment), **verify `.gitignore` covers it before writing**, delete at teardown. Refuse and report if the check fails, never warn-and-continue | Frameworks that require a file |
| 4 — Proxy instead of pass | Quarterdeck holds the real key, exposes a local endpoint with a per-session token. **The real key never leaves Quarterdeck.** Gives a spend cap per session, per-session attribution, and instant revocation by killing one token | Third-party HTTP API keys — prefer this tier whenever it applies |

**Redaction is the load-bearing half.** **[Q]** The audit trail already redacts sensitive keys at write time; extend the same filter to every output path — audit records, pane capture, the messages API, card excerpts, remote views — because processes leak constantly (`env` dumps, `curl -v`, stack traces echoing a connection string). **Match the literal plus its URL-encoded, base64, and JSON-escaped forms**; replace with `***REDACTED:NAME***` so the name survives for debugging.

**Two policies from day one.** `agent_allowed: false` refuses a session declaring a production credential **before dispatch**, not at runtime. A cheap authenticated validation check per secret, run by Quarterdeck, surfaces pass/fail only, so a wrong key is caught immediately rather than forty minutes into a session.

**The case that cannot be fixed.** If a secret is pasted into the conversation, it is already in the session JSONL on disk. Redacting the display does not unwrite the file. **Say so plainly and recommend rotation. Never claim to have removed it.** Backstop: entropy detection on user input, warning on a hit, offering to move it to the store.

**DONE WHEN** a project with a missing `.env` key shows the gap in the discovery panel, a key entered in the field never appears in any transcript or audit record even when the agent runs `env`, and pasting a key into chat triggers the entropy warning.

**References sections outside this document:** this was raised in the session record as extending a Settings section that isn't specified here. Check against the full `ROADMAP.md` before building the panel UI.

---

### TASK 9: Liveness states and a spawned-process registry [NEW]

A stalled session looks identical to a busy one from the grid. This task makes the difference visible and handles the processes an agent spawns, which are a different problem from the session itself.

**Two different questions.** *Alive* means the process exists and output is arriving. *Progressing* means the output is advancing. A session can be alive and not progressing: retrying the same call, waiting on a lock or network timeout, waiting on an unseen permission prompt, or looping.

**States, always displayed:** `WORKING` (emitting new distinct events), `WAITING_INPUT` (blocked on approval), `STALLED` (alive, no new output past threshold), `LOOPING` (emitting but not advancing), `OVER_BUDGET` (ceiling reached).

**Signals, cheapest first, all mechanical:** process exists; time since last JSONL growth; time since pane changed; **count of distinct tool calls, not total** — repeated identical calls are the signature of a loop, not progress, **[M]** the one signal already measured here, discriminated by whether a mutation occurred between two identical calls; token spend rising while distinct-event count stays flat; a held `preToolUse` request (**[Q]** `/api/approvals` already exposes this). Thresholds should be per-task-type from Task 7, not global constants, once Task 7 exists.

**`WAITING_INPUT` breaks every no-interrupt rule elsewhere in this plan.** Nothing changes without the user. Notify immediately, on every channel, regardless of threshold — a session silently blocked on an unseen approval holds attention, looks busy, and never finishes. **[Q]** The attention strip already interrupts a focused view for this; extend it to the away case.

**Escalation ladder:** at threshold, change the displayed state, no notification (most stalls resolve); at twice threshold, notify locally; at five times or budget exhaustion, **snapshot findings first, then act.** Never act before snapshotting — a session two hours into an investigation holds eliminations that outlive it, and killing it without extracting them means the next attempt re-explores the same dead ends.

**Spawned processes are a separate problem.** Dev servers, builds, watch modes, containers an agent starts are not sessions: no JSONL, they outlive the session that started them, and silence from them is normal, so Task 9's stall logic must not point at them.

- **They outlive their session.** Register every long-running process at spawn with an owning session, a declared lifetime (`task` — killed on session end; `session` — kept for later work; `user` — **the process is the deliverable, never auto-killed**), and a kill command. No kill command means a leak by construction.
- **Ports are not merge-safe** (2.6). Two sessions each picking 3000 is exactly the class of conflict that can't be checked locally. Quarterdeck allocates from a pool and injects the assignment; sessions never choose their own.
- **Output volume.** Spawned-process output goes to a file, never into a context; the agent greps or tails it. Cheapest large token saving available.
- **Inverted health signals.** For a spawned process, silence is normal, a listening port is the real readiness check, log growth means something only for builds, and exit is expected for a build and a failure for a server. Readiness needs a declared check per process, not a timeout.
- **Reaping on exit:** kill every `task` and `session` process, report every `user` process left alive with PID and port, report anything that could not be killed. A silent failure to reap is worse than a crash, because the next run inherits an unexplained port conflict.

**User affordances:** `status` (all states, one screen), `peek` (read-only log in a pager), `nudge` (ask the session to state its current step before cancelling), `cancel` (snapshot, then act), `extend` (raise a ceiling set too low).

**DONE WHEN** a session in each of the five states is visibly distinguishable in the grid without opening it, a `WAITING_INPUT` session notifies immediately regardless of how recently it changed state, and a spawned dev server survives its owning session ending with `lifetime: session` while a `lifetime: task` process does not.

**References sections outside this document:** this extends a stall-detection proposal (referred to in the session record as "10c") that is not specified here. Confirm the existing proposal's shape against `ROADMAP.md` before implementing thresholds.

---

### TASK 10: Board layering [NEW]

A unified view of everything running is useful; merging raw output into one stream is not — it rebuilds the context problem in the interface instead of solving it. **The board shows state; output is reachable, never ambient.**

**Three layers, in order of how often they're read:**

1. **The board.** Dense, one line per thing, refreshed from disk. **[Q]** This is the existing grid (2.1), extended with per-session **context percentage**, a spawned-processes section (Task 9), and queue depth. Context percentage matters because with several sessions running there is no other way to know which is about to compact — **[V]** most tools expose nothing here; Claude Code's `/context` is the exception, and **[Q]** `/context` is already a quick-command chip here.
2. **The activity feed.** A merged, **filtered** event stream — state transitions, held approvals, gate blocks, oracle verdicts, corrections, handoffs. Not raw tool output. This is a `git log` of the run: what you read after being away.
3. **Drill-down.** **[Q]** The existing three panes (Live, Activity, Last Output).

**[D] The board should read disk rather than attach to a running process.** It survives a backend restart, cannot perturb anything because it only reads, and works remotely with nothing but a file tail.

**DONE WHEN** the grid shows context percentage per session, the activity feed reads as a coherent timeline across every currently-tracked session without raw tool output leaking into it, and the whole board still renders correctly against cold disk state after a backend restart mid-run.

**References sections outside this document:** filed against "7d grid refinements" and the stats view in the session record; those aren't specified here, so confirm the existing grid spec before extending it.

---

### TASK 11: Compaction management [NEW — see the flagged conflict below before building]

Four related pieces. Build 11.1 and 11.2 first; 11.3 is where the flagged conflict with Part 6 lives; 11.4 is a one-off measurement, not a shipped feature.

**11.1 — Keep sessions in a healthy band.** Compact early, not under pressure. **[V]** Codex caps a million-token model at 272K deliberately, because a larger window delays compaction until the summary itself becomes unreliable — a summary written at 30 percent of the window is better than one written at 90 percent. **[V]** Kiro's `Compaction` event already carries a `strategy` with `context_window_percent_to_exclude`, `message_pairs_to_exclude`, `truncate_large_messages`, `max_message_length`. **[S]** 20–30 percent is a starting guess, to be checked against 11.4's measurement, not a finding yet.

**11.2 — Harvest before compacting, then compact without hesitation.** Compaction is precisely the moment a trajectory is about to be destroyed, so it's the moment findings (Task 6) must already be captured. Do that and the summary can drop whatever it likes, because what mattered is durable elsewhere. **Save liberally, load selectively** (Task 6.3) — the harvester must receive the goal, not only the transcript, or it keeps what the trajectory emphasised rather than what the goal needs. **[Q]** `~/.kiro/sessions/cli/*.jsonl` is append-only, so pre-compaction state is still readable after the `Compaction` event fires — harvest can be triggered by the event itself. **[D] Cheap ongoing check, no experiment needed:** after a compaction, did the session re-derive something it already knew? That's an observable harvest miss with a specific fix.

**11.3 — Overlap compaction so it costs no dead time. [FLAGGED — see conflict]** A tight healthy band means more compactions, so more dead time, which is what makes overlap worth doing rather than merely nice. The proposed mechanism: session A keeps working; at the threshold, a **separate call** reads A's JSONL from disk and summarizes without pausing A or touching its context; when the summary is ready, session B starts in the same working directory, seeded with the summary, applicable findings, and A's verbatim tail (**[V]** Gemini CLI preserves roughly 30 percent of the tail this way); handoff at a clean boundary; A ends. **[Q]** Feasible today because `--resume-id` exists and tmux owns the process independently of any session id. **[M] Parsing trap:** with overlap, a `Compaction` event lands in the log after events it doesn't cover — anything computing turn boundaries must be tested against this explicitly.

> **This is functionally double-buffered session handoff, which Part 6 lists as cancelled** on the grounds that KiroCrew's multiplexed ACP runtime already addresses the underlying cost. It's possible these are different claims — Part 6's cancellation was about general session handoff, this is scoped narrowly to compaction overlap and grounded in Quarterdeck's own resume mechanism rather than a new runtime — but that distinction was asserted, not verified, when this was written. **Do not build 11.3 until this is resolved one way or the other** (see Q8 in Part 7). If it turns out to be the same mechanism Crew already provides, defer to Crew and drop 11.3.

**11.4 — A one-off experiment on what compaction actually loses, not a shipped feature.** Before compacting, extract 8–12 specific facts to a probe file the session cannot see, deliberately spanning both finding classes from Task 6 (half target-relative, half target-independent). Fork at the pre-compaction point; let the original compact; give both branches the identical next prompt. Measure, all mechanical, no model judgment: fact retention (ask each branch a question requiring each probe fact, count what it supplies), oracle verdict (pass/fail), rediscovery (tool calls duplicating a pre-fork call with no intervening mutation), tokens to pass, and constraint violations. **Report retention separately by class** — the predicted result is that target-relative facts survive and eliminations get dropped, in which case the fix is 11.2's harvest rule, not a tighter band; check for that before concluding anything about the band width itself. **[V]** Confirm `replayMarking` doesn't differ between the forked and compacted branch first, or the comparison is confounded on two axes instead of one. Sample size 5–10 pairs; one pair proves nothing given trajectory non-determinism. **Do not build the runtime fork-and-choose mechanism** — it decays as soon as either branch makes progress, and since the JSONL is append-only, knowing what was dropped costs nothing and needs no fork; re-injecting one specific fact costs a few hundred tokens.

**DONE WHEN** 11.1 and 11.2 ship, findings survive at least one real compaction with a verified pre/post check, and 11.3 either has an explicit answer to the flagged conflict or is not built. 11.4's DONE WHEN is a written report, not code: retention by class, with sample size and the four mechanical measures above.

**References sections outside this document:** filed against "V3 format" and "the ACP driver" sections in the session record, not specified here.

---

# PART 6: WHAT IS NO LONGER IN SCOPE

Explicitly cancelled from the earlier plan. Do not build these.

| Dropped | Why |
|---|---|
| Driver, orchestrator, worker topology | **[Q]** Quarterdeck dispatches and adopts. **[V]** Crew orchestrates |
| The board (as a from-scratch build) | **[Q]** Quarterdeck's grid is the board — see Task 10 for layering it, not replacing it |
| Liveness monitor (as a from-scratch build) | **[Q]** `stop` hook, pane status, and attention ordering already exist — see Task 9 for extending it, not replacing it |
| Notification channels | **[Q]** Remote plus phone approvals. **[V]** Crew has seven messaging surfaces |
| Secrets store and redactor (as a from-scratch build) | **[Q]** Audit redacts sensitive keys at write time — see Task 8 for extending coverage, not rebuilding the redactor |
| Worktrees, `merge-tree`, compensation, parallelism | **[D]** Arithmetic, not opinion: if median per-worker setup (cold prompt cache, dependency install, container start) exceeds median task duration, parallelism loses before it starts. Interface-freezing is inherently serial, so Amdahl caps the speedup regardless of worker count. Not Quarterdeck's job; revisit only if parallel work becomes real and the arithmetic changes |
| **Double-buffered session handoff** | **[V]** Crew's multiplexed ACP runtime addresses the same cost. **⚠ Flagged**: Task 11.3 proposes a narrower version of this scoped to compaction overlap. Not reconciled — see Q8 |
| Excision and context surgery | **[M]** ~40 percent precision on 20 hand-checked samples (95% interval ~19–64%), recall never measured. **[Q]** A side-chat-against-frozen-context feature (referred to in the session record as "10g") solves the underlying problem by prevention instead — content that never enters the main log needs no removal — which is a better fix than improving this number |
| Rewind (auto-revert to a checkpoint) | **[D]** Unjustified, not refuted. Needs attribution distance greater than zero — evidence that fixing at the origin beats fixing at the point of detection — and nobody has measured that. Where a failure surfaces at its cause, plain oracle feedback already wins on latency and simplicity |
| An observer model watching every step | **[M]** None of the four recurring failures measured in this project would have been prevented by better reasoning at watch-time. Three needed a rule present at the right moment (Task 2); one needed a command run (Task 4). **[D]** An observer reading a worker's self-report inherits exactly the claim it was meant to check — the same self-report weakness noted against ZORO in 2.5 |

---

# PART 6B: DOCTRINE AND BACKLOG, NOT YET TASKS [NEW]

Ideas from the session record that don't reduce to a buildable task yet, either because they're policy rather than a feature, or because they extend parts of the broader `ROADMAP.md` this document doesn't contain. Recorded so they aren't rediscovered or silently dropped.

## 6B.1 Moving targets

The target — spec, goal, plan — changes during real work; this is normal, and it breaks the assumption used everywhere above that a target predates the work it's judging.

**Two kinds are in scope, one isn't.** *Refinement* (a constraint narrows the target) and *expansion* (scope grows, existing work stays valid) are both fine and need no new machinery — a refinement is just a constraint delivered like any other (Part 4), reaching a running session at its next decision point; an expansion is an append. *Redirection* — a premise changes, prior work may be void — is explicitly **not** something to automate: detect it mechanically (it replaces rather than narrows a goal, changes an oracle, changes an interface a running session depends on, or contradicts an already-delivered constraint), then **stop new dispatch, harvest findings from affected sessions first** (target-independent findings, Task 6, survive redirection completely), **report without acting.** The user decides.

**Anti-drift rule:** only the user amends a target. A target change authored by anything else, in response to work already done, isn't a target change — it's the work justifying itself, which turns the target from a judge into a certificate. Every target version is timestamped and authored, never edited in place; every finding and correction records which version it was made against, for the same reason `steering_commit` exists on corrections (4.1).

**Volatility bounds task size.** Compare how often the target actually changes against how long a dispatched task runs (both measurable via Task 7 once it exists). If targets shift every twenty minutes and work runs two hours, freezing the target is fiction; if work runs ten minutes, a change costs ten minutes. Under high volatility, the load-bearing property of a task isn't that it's well-specified — it's that it's cheap to discard, and target-independent findings become almost the only durable value.

**Where this would go:** a doctrine section near Part 2.2, since it's a constraint on every other part's assumptions, not a task in itself.

## 6B.2 The "captain" role

**The idea, refined:** instead of a strong model supervising weaker ones by reading their output, the strong model authors the measuring instruments — oracles, checkpoints, criteria — **before** any worker starts, then stops. It runs once.

The reasoning: a captain reading a worker's self-report has *less* information than the worker, since it never saw the trajectory — a stronger model reasoning over a "fixed it" summary is more fluent about accepting the summary, not more likely to catch what's wrong with it. Handing it the full trace instead just grows its context to the size of everything below it, putting the most expensive model on the largest context. Authoring checkpoints before work starts gets independence a different way: written by something not executing, so it can't grade its own trajectory; small enough to hold in a short context; and it only runs once, so no continuous cost and no self-report to swallow.

**Two hard constraints, or it doesn't work.** Every criterion must be mechanically checkable — the moment a checkpoint reads "verify the design is sound," judgment is back and the independence is gone; if a criterion can't be a command with an expected exit code, that checkpoint belongs to a human, not the loop. And the captain's own premises are still unverified — it authors the oracle from its own understanding, and if that's wrong, every downstream check is wrong in the same direction while still passing. Mitigation is the same negative-fixture rule as everywhere else in this plan (4.2): every criterion ships a case where it must **not** fire, drawn from something real.

**Decision ownership, stated generally** (this is the rule Part 3, Part 4, and Part 6's rewind/observer rows are all specific cases of): every decision goes to whoever holds evidence that *predates* the thing being decided.

| Decision | Owner | Why |
|---|---|---|
| Tracing | Nobody — unconditional | A side effect of execution. Any condition is a place a failure can avoid being recorded |
| Rewind | The checkpoint | The return point was fixed when the checkpoint was authored, not at the moment of failure, which is when the doer is most invested in continuing |
| Fresh start | The user | A question about intent. Nothing in the log contains it |
| Excision / parking | The goal, applied cheaply | The goal predates the exchange, so the judgment has an anchor |

**Why this is filed as doctrine, not a task:** it's adjacent to the driver/orchestrator work cancelled in Part 6, and whether it's actually in tension with that cancellation — a planning-only role that runs once and stops, versus a runtime orchestrator, which is what got cancelled — wasn't checked before this merge. See Q9 in Part 7.

## 6B.3 Two smaller backlog notes

**The defer queue.** A side-chat feature (referred to in the session record as "10g") already handles a lookup or clarification that shouldn't enter the main log. It doesn't handle the opposite case: a discovered issue that's real work but off the current goal, which must not be lost. Proposed disposition: three categories, not two — lookup/clarification → park (side chat, never enters the log); discovered issue → defer (queued with session id and turn, triaged later); on-goal work → continue. The test isn't "did this serve the goal" but "does this need to survive," since a discovered issue is by definition not in the goal and the goal can't tell you which bucket it's in. **[Q]** A task-stack mechanism already exists (`~/.osa-kiro/stacks/`, Part 2.1) that could carry this. **[D] Worth checking against the labelled 23-session data (1.3):** how many of the 17 violations were the agent pursuing something it discovered rather than something it was asked for — if discovered-issue drift is common, this queue matters more than it looks.

**Turn search for "what did we say about X."** Two different questions hide in that phrasing and guessing wrong costs differently: "what did I *decide* about X" wants a note search with a small fresh context, while "*continue* the reasoning about X" wants to fork to a specific turn, where history is the payload. Guessing the notes direction wrong costs a wasted search; guessing the history direction wrong hands over a stale context with no signal it's stale, which is worse. Proposed smallest build: score prior turns by term overlap with the question, show the top three with a one-line preview, let the user pick — an automatic jump to the wrong turn produces a confidently stale context, so keeping the user in the loop is the point, not a shortfall. **References a "branch from a specific turn" mechanism** not specified in this document.

---

# PART 7: OPEN QUESTIONS

| # | Question | Tag | Answered by |
|---|---|---|---|
| Q1 | Does `~/.kiro/crew` expose parent-child session relationships? | **[S]** | Task 1 |
| Q2 | Does a steering rule reach the model at decision time, or only at session start? | **[M]** partly | Task 2 |
| Q3 | Does KiroCrew's lesson mechanism actually deliver? Their claim is unverified by anyone | **[S]** | Task 2 |
| Q4 | Is recurrence real once a denominator exists? | **[S]** | Task 5 |
| Q5 | Does `kirocrew security verify` produce a signed artifact or only a local check? | **[S]** | side probe |
| Q6 | Do rules transfer between projects? | **[S]** | later, and it decides whether any of this is more than a personal tool |
| Q7 | Do duration estimates (Task 7) transfer between projects, and is that the same transfer question as Q6, or a separate one? | **[S]** | Task 7, once it has run long enough to compare |
| Q8 | Is Task 11.3's compaction overlap actually the same mechanism as the cancelled double-buffered handoff in Part 6, or a genuinely narrower claim? | **[S]** | Must be resolved before building Task 11.3 |
| Q9 | Does the "captain" role (6B.2) conflict with the cancelled driver/orchestrator (Part 6), or is a run-once planning role a different thing entirely? | **[S]** | Check against `ROADMAP.md` before treating 6B.2 as buildable |

**Q1 comes first because it can change the grid design. Q2 and Q3 are the deliverable. Q4 can end the project honestly. Q8 and Q9 gate whether Task 11.3 and 6B.2 should ever leave the backlog.**

---

# PART 8: REPORTING RULES

1. Sample size beside every number. Never a bare percentage.
2. Confidence intervals on any rate from fewer than 30 observations.
3. If something could not be measured, say so and state what would be needed.
4. A negative result is a successful outcome.
5. Any observation contradicting a **[Q]** or **[V]** claim supersedes it. Record it with the date.
6. **Review disagreements in both directions or neither.** Checking only the cases where an automated judgment and a human disagree in one direction — only false positives, or only false negatives — moves every estimate toward whatever was already expected. This applies to correction withdrawals (4.1), gate warn-log review (Task 4), and validation sampling (Task 5) alike.

**The one thing to protect:** Quarterdeck works and gets used daily. Every addition here is additive, writes only under `~/.osa-kiro/`, and defaults to visible, warn-only, and safe. **Nothing in this plan may make the grid less trustworthy than it is today**, because a tool that is not trusted stops being used, and then nothing gets measured at all.

---

# PART 9: POSITIONING [NEW]

Not framed as an agent-tooling builder. **The measurement is the position: whether agent tooling delivers what it promises.** KiroCrew claims skills load only when relevant and that corrections become lessons applied in future sessions. Neither claim has been verified by anyone. The method to check both is Part 4 and Part 5 of this document, and the tool to run it — Quarterdeck — is already installed and used daily, which is what makes this cheap rather than speculative.

Both outcomes are useful. If the claims hold, the measurement method is the contribution. If they don't, the gap is the product, and there are receipts (Task 2's delivery-audit, Task 5's recurrence report).

**[S] The one result that decides scale:** do rules transfer between projects (Q6)? Idiosyncratic per repository is a good internal tool with a low ceiling. Transferable across organisations in a domain means the corpus itself is the asset, it compounds, and it's domain knowledge rather than code — which a tooling vendor can't ship because they don't have the domain.

---

# APPENDIX: WHAT MOVED WHERE [NEW]

For checking this merge against the two source documents later.

| Session record section | Disposition |
|---|---|
| §1 The one measured finding | Already in 1.3 — no change |
| §2 Two measurement failures, the rule they produced | Already in 1.4, 4.2 — no change |
| §3 Quarterdeck-specific corrections | Already in 2.1 — no change |
| §4 The takeover gap | Already covered by 3.6–3.7 — cross-referenced at 3.8, no new work |
| §5.1 Delivery measurement | Already in 4.3, Task 2 — no change |
| §5.2 Correction capture with provenance | Already in 4.1 — added the one-direction-review caution |
| §5.3 The denominator | Already in Task 5 — no change |
| §5.4 Finding classes | Already in Task 6 — no change |
| §6 Always-on | **Not folded in.** Depends on a notification/scheduling design ("10d") not present in this document. Not addressed here |
| §7 Decisions and reversals | Folded into Part 6's table (richer rationale) and new rows for rewind and the observer |
| §8 Prior art | New — added as 2.5 |
| §9 The position | New — added as Part 9 |
| §10 Reporting rules | Already in Part 8 — added the both-directions review rule |
| §11 Duration: measure, learn, calibrate | New — Task 7 |
| §12 Secrets the agent must never see | New — Task 8 |
| §13 Liveness: working, or hung? | New — Task 9 (liveness half); spawned-process registry folded into the same task |
| §14 The board: one view of everything | New — Task 10 |
| §15 Compaction (4 subsections) | New — Task 11, with 15.3's double-buffering flagged as a conflict against Part 6 rather than silently accepted |
| §16 Moving targets | New — 6B.1, doctrine rather than a task |
| §17 The captain | New — 6B.2, doctrine rather than a task, flagged against Q9 |
| §18 Defer queue; turn search | New — 6B.3, backlog notes |

**Not resolved by this merge, and worth doing before the next build cycle:** §6 (always-on) was left out entirely because it depends on scheduling and notification sections this document doesn't contain. If those sections exist in the fuller `ROADMAP.md`, always-on should be folded in as a proper task rather than left as a gap in the appendix.
