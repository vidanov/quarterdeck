# Handover: Constraint-Accumulating Agent Loop on Kiro-CLI v3

Date: 2026-07-31
Status: design settled, platform partially verified, no code written
Provenance markers used throughout: **[VERIFIED]** read from vendor docs or user terminal output, **[CLAIM]** asserted by a model or unverified source, **[OPEN]** must be tested before build.

---

## 1. Objective

Build a long-running agent loop where a doer executes steps, an observer detects failure against anchors, and the loop returns to an earlier point with the learned error injected as an enforced constraint rather than as prose. Target substrate: Kiro-CLI v3, driven programmatically.

---

## 2. Conclusions that survived the session

Ordered by load-bearing weight. Items 1 and 2 are the design. Everything else is support.

1. **The value is constraint accumulation, not tree search.** Learning that lives in context dies with the episode. Learning compiled into a repo file outlives the session, the branch, and the operator. Retries are the input to the accumulator, not the product.
2. **Rewind only earns its cost when attribution distance is greater than zero.** If failure surfaces at the step that caused it, a simple oracle-feedback loop is better on latency and complexity. Rewind exists to fix at origin rather than at detection. Without distance, the doer patches defensively at the detection point, the anchor goes green, and the root defect ships.
3. **The observer needs an anchor, not an opinion.** Same-family model reading the doer's own narrative reproduces the doer's blind spot with added authority to rewind. Decorrelation is achieved by starving the observer of the doer's reasoning, not by switching model vendor.
4. **Most observation is code, not a model.** Tier 0 is deterministic: test runner, schema check, diff, cost counter. Tier 1 model involvement is narrow: convert a FAIL plus artifact diff into a constraint proposal, structured JSON only.
5. **Constraints must be predicates over artifacts.** Prose reminders are instruction-based compliance and get violated under context pressure. A gate that returns a non-zero exit and blocks the call cannot be forgotten.
6. **Side effects are not rewindable by any available primitive.** Context forks. Files fork with git worktree. Account and external state require declared compensations per step. No vendor solves this today.

---

## 3. Decisions reversed during the session

Recorded because the reversals matter more than the conclusions.

- Tree search / MCTS-style branching was demoted from core mechanism to optional optimization. Most engineering tasks have one correct answer and a wrong assumption to forbid, not several viable branches to compare.
- LangGraph was evaluated and rejected as the primary substrate. It is an alternative to the driver, not to Kiro. Adopting it means writing the doer, losing spec agent, hooks, permissions model and code intelligence, to gain fork-with-state.
- Recommended build order changed to no branching in v1.

---

## 4. Platform findings: Kiro-CLI v3

### 4.1 Enforcement layer (the reason to stay on Kiro)

**[VERIFIED]** Hook triggers that can block: `PreToolUse`, `PreTaskExec`, `UserPromptSubmit`. All other triggers are observe-only.

**[VERIFIED]** `PreToolUse` blocks execution with exit code 2 and returns STDERR to the model. This is the gate primitive.

**[VERIFIED]** `PreTaskExec` fires before a spec task begins, has no matcher so it always fires, and can block. This is the step-boundary gate. It is what makes budget ceilings and pre-step contract checks enforceable.

**[VERIFIED]** `PostToolUse` and `PostTaskExec` provide the paired trace events with tool_input and tool_response.

**[VERIFIED] Trap.** The v3 `agent` action type appends a prompt string to model context and spawns no subprocess. It cannot block. Vendor documentation demonstrates it with an example that reads like a constraint but is only a suggestion. Constraint injection must always use `command` plus exit 2. Reserve `agent` actions for steering whose violation is acceptable.

**[VERIFIED]** Hook timeout defaults to 60s, 0 disables. Any observer invoked as a hook command must finish inside that.

**[VERIFIED]** v3 `permissions.yaml` provides capability policies where a single rule allows or denies a category across all tools. Coarse layer above per-tool hooks.

### 4.2 State restore (the gap)

**[VERIFIED]** `/rewind` forks the session at an earlier turn. Fork, not restore, so siblings survive. This is a conversation-state operation only.

**[VERIFIED]** `/checkpoint` does not appear in the v3 feature comparison table, in any status, and does not appear in v3 `/help` output.

**[OPEN]** Whether `/checkpoint` is removed or merely hidden behind the 2.x gate `kiro-cli settings chat.enableCheckpoint true`. Both hypotheses fit the evidence. See section 9.

**[VERIFIED]** Even where checkpoints exist, they never track file changes made by MCP tools or by bash commands the agent runs.

**[VERIFIED]** v3 removed `aws_tool` in favour of MCP servers. Consequence: all AWS mutation now flows through the exact channel checkpoints do not snapshot. v3 has strictly less usable state restore than 2.x for cloud work.

**[VERIFIED]** v3 session format is not backward compatible. v3 sessions cannot be resumed in V2.

**[CLAIM, rejected]** Kiro's own in-product help agent asserted that `/checkpoint list` is a working v3 in-session command. It is grounded on 2.x documentation, not on the running binary's command registry. Logged as the session's cleanest example of a fluent, sourced, possibly wrong claim.

### 4.3 Control surface: ACP

**[VERIFIED]** `kiro-cli acp` speaks JSON-RPC 2.0 over stdio. `--agent <name>` selects an agent config.

**[VERIFIED]** Core methods: `session/new`, `session/load`, `session/prompt`, `session/cancel`, `session/set_mode`, `session/set_model`.

**[VERIFIED]** Interactive chat is itself an ACP client. The TUI therefore has no privileged channel, and slash commands are reachable programmatically.

**[VERIFIED]** `_kiro.dev/commands/execute` executes a slash command over the protocol.

**[VERIFIED]** `_kiro.dev/commands/available` is a notification emitted after session creation, carrying the list of available commands. **This is the authoritative capability probe.** It is the binary enumerating itself, superseding `/help`, documentation, and any model's claim.

**[VERIFIED]** Sessions persist to `~/.kiro/sessions/cli/<session-id>.json` for state plus `<session-id>.jsonl` as an append-only event log. Trace infrastructure already exists on disk.

**[VERIFIED]** `ToolCall` and `ToolCallUpdate` notifications provide live streaming.

**[VERIFIED, v1.28.0 empirical probe]** `sessionCapabilities: {}`. No fork, list, or resume advertised at protocol level. `loadSession: true`. Forking exists only as a slash command, if at all.

**[VERIFIED, same probe]** `mcpCapabilities`: http false, sse false. MCP over stdio only. HTTP MCP servers must be configured in `.kiro/settings/mcp.json`, not passed in the `mcpServers` array of `session/new`. This binds tightly to the aws_tool removal.

**[VERIFIED, same probe]** Client capabilities are not used. Kiro performs its own file I/O. The driver cannot intercept writes at protocol level, so the gate must remain in hooks.

**[VERIFIED]** Extension methods are marked experimental and subject to change.

**[OPEN]** Whether `kiro-cli acp` runs the v2 or v3 engine, and whether it accepts `--v3`. ACP docs updated 27 May 2026, v3 docs 17 June 2026. Determines whether the driver observes `PreTaskExec` at all.

**Note on doc quality:** vendor ACP examples show `agentInfo` version 1.5.0 while empirical probing reports 1.28.0. Trust the wire, not the page.

---

## 5. Architecture

### 5.1 Fixed core, domain-independent

Four schemas. No prompts.

**Step contract**
```
{ goal, artifacts_touched[], side_effects[], compensation_cmd,
  budget: {tokens, seconds, calls}, oracle_ref, oracle_class }
```

**Anchor runner interface**
```
in:  step_contract
out: { verdict, exit_code, evidence_path, cost }
```
Any language, any domain. The core knows contracts and exit codes and nothing else.

**Trace event schema.** One shape for all steps. Keyed on session_id plus branch_id.

**Gate registry plus compiler.** Gates live in `.kiro/hooks/`, each with scope, provenance and expiry.

### 5.2 Oracle classes (the real axis of generality)

Do not attempt task-type agnosticism. Classify by verification type. The class determines what the loop is permitted to do.

| Class | Anchor source | Loop authority |
|---|---|---|
| Native | Already exists: tests, type check, CDK diff, schema validator | Auto-rewind permitted |
| Proxy | Correlated measure: linter, latency budget, cost delta | Advisory only. Never rewind on proxy alone. Must declare threshold and known bias |
| Manufactured | Built for the purpose: row counts, checksums, referential integrity, sum reconciliation, partition-without-overlap | Auto-rewind permitted once the anchor itself is validated |
| Internalized | Model judgment only: prose quality, argument strength | Auto-rewind disabled. Route to human queue |

Intake step: classify before starting. If the honest answer is internalized-only, the loop does not apply and no framework repairs that.

### 5.3 Gate templates (why the compiler generalizes)

The observer does not write arbitrary scripts. It fills parameters in four templates. Free-form script generation would need domain knowledge and would produce unauditable gates.

1. **Forbidden pattern in scope.** regex plus path glob.
2. **Required structure.** JSON Schema or required-field assertion over the artifact.
3. **Invariant.** Property assertion generated from a parameterized harness.
4. **Budget.** Numeric ceiling on tokens, time, or calls.

Every domain's learning compresses into one of these four.

### 5.4 Roles

**Doer.** Strongest coding model, effort high, full tool access, one spec session. Authors code only. Does not author its verdict, its progress summary, or the rewind decision.

**Observer tier 0.** Deterministic. `PostTaskExec` runs the anchor, appends `{branch, task, verdict, exit_code, stderr_tail, cost}` to trace JSONL.

**Observer tier 1.** Small model, low effort, separate ACP session. Input is anchor output plus artifact diff. Never the doer's reasoning. Output is a constraint proposal in constrained JSON.

### 5.5 Substrate

- Context: `/rewind` via `_kiro.dev/commands/execute`.
- Files: git worktree per branch. Independent of the context layer, which is why they compose.
- External state: declared compensations, run in reverse order before a fork completes.

---

## 6. Worked example (use this as the first test case)

Spec: make an SQS-consuming Lambda idempotent so duplicate delivery cannot double-post to an ERP endpoint.

Tasks: (1) dedup table in CDK, (2) dedup middleware, (3) unit tests, (4) deploy to sandbox, (5) integration test for duplicate delivery, (6) docs.

Anchor for task 5: deploy, send the same logical event twice, assert exactly one ERP call. Native oracle, binary, no judgment.

**Failure trajectory, first pass.** Task 5 fails, two ERP calls. Trace localizes cause to task 2: dedup key was SQS `messageId`, which changes on redrive. Attribution distance = 3.

Observer proposal:
```json
{ "trigger": "PreToolUse", "matcher": "fs_write|str_replace",
  "action": { "type": "command",
              "command": ".kiro/checks/dedup-key-source.sh" } }
```
Script greps the dedup module for approved key sources, exits 2 with STDERR: dedup key must derive from business payload, not transport metadata. Rewind to fork before task 2 with the gate present at session start.

**Second pass.** Doer hashes the whole body including a producer timestamp. Anchor fails again. Second gate is a property test asserting two payloads differing only in transport fields hash equal. Manufactured oracle, now permanent.

**Note.** Task 4 deploys. That is the side effect no primitive rewinds. Start with tasks 1 to 3 only, where the oracle is a unit test and rollback is worktree alone.

---

## 7. Loop invariants

- No rewind without at least one new gate that would have blocked the failed branch. Otherwise the loop re-rolls dice with a clean context. This also makes the search finite in constraint space rather than trajectory space.
- Rewind target must cite a trace event. No citation, fork from parent.
- Same constraint proposed twice means oscillation. Stop and escalate.
- Every gate carries a path scope and domain tag in its matcher. Without this, gates accumulate globally and month three the repo refuses everything for reasons nobody can reconstruct.
- Every gate records the trace event and branch that produced it, plus a review-by date. A gate that has not fired in ninety days is dead weight or fully internalized. Both mean retire.
- Proxy-class anchors never trigger auto-rewind.

---

## 8. Metrics and evaluation

### 8.1 Borrowed from public work

- **pass^k, not pass@1.** τ-bench introduced pass^k, probability of succeeding on all k attempts. Reported gap: GPT-4o 61% pass@1 against 25% pass@8 on retail tasks. majority@k is the looser variant; the pass@k minus majority@k distance is the reliability gap.
- **Per-step hazard.** METR's 50% time horizon was roughly fifty minutes in early 2025, doubling roughly every seven months. Follow-up analysis found a per-step hazard rate, success dropping exponentially with length. Implication for this design: failure is per-step, so the intervention is per-step gating rather than better planning.
- **Dense reward over binary.** LHTB grades subtasks because binary pass/fail hides substantial partial progress.
- **Unresolved-run taxonomy.** Copy directly: timeouts (agent still working at budget expiry), early exits (agent terminates itself, no harness error), harness errors. Three different bugs. Pass rate conflates all three.
- **Cost as a first-class axis.** LHTB averages 228 episodes, 85 minutes and roughly $10 per task, with top models near $21. Performance does not scale linearly with API cost. Any architecture claim without a cost axis is unfalsifiable, because pass rate can always be bought with attempts.

### 8.2 Confound to control

Harness-induced belief divergence: harness choice shifts agent beliefs, and the divergence grows with rollout length rather than stabilizing. The harness is not a neutral wrapper. Comparison protocol must fix the model and task set and vary exactly one harness element.

### 8.3 Protocol

Fix model. Fix task set. Fix anchors. Three arms:

1. Bare loop.
2. Loop plus oracle feedback injected into context (Reflexion baseline, and what Kiro already does natively with a test command).
3. Loop plus compiled gates.

Report pass^4 with cost per task and the three-way unresolved decomposition.

### 8.4 Metrics specific to this design

No public benchmark measures these. They are the falsifiable core of the thesis.

- **Gate yield.** Fraction of compiled gates that later block a genuine regression. Near zero means the compiler is generating superstition.
- **Gate false-block rate.** Blocks on correct actions. The cost side of enforcement, unreported anywhere.
- **Attribution distance distribution.** Detection step minus cause step, measured before building any branch table. If the mass sits at zero, do not build rewind.
- **Recurrence.** Same failure class across sessions, before and after a gate exists. This is the persistence claim. No current benchmark tests an agent over a multi-day session with persistent memory.

---

## 9. Open questions with exact tests

Run these before writing code. Each has a cheap empirical answer.

1. **Does `kiro-cli acp` run the v3 engine?**
   `kiro-cli acp --v3`, then `initialize`, then `session/new`. Read `agentInfo.version` off the wire.

2. **What commands actually exist?**
   Same session, read the `_kiro.dev/commands/available` notification. Authoritative. Settles `/checkpoint` definitively and gives the driver a startup capability gate.

3. **Is `/checkpoint` removed or hidden?**
   `kiro-cli settings chat.enableCheckpoint true`, restart, `/help`, grep. If it appears, it was hidden. If not, type `/checkpoint list` and treat an unknown-command error as removal proof. Architecture does not move either way: checkpoints never snapshot MCP or bash effects, and v3 routes all AWS through MCP.

4. **Does `/rewind` mint a new session id or rewrite in place?**
   `/session-id`, `/rewind`, `/session-id`. Determines whether the branch table keys on session_id or needs its own branch_id with a parent pointer.

5. **Does `PreTaskExec` fire when the spec is driven over ACP rather than the TUI?**
   Install a logging hook, run a spec task via `_kiro.dev/commands/execute`, check the log. If it does not fire, the step-boundary gate is TUI-only and the whole design needs the driver to enforce budgets instead.

---

## 10. Build order

Each stage has a kill criterion. Do not proceed past a stage that fails it.

**Stage 0.** Run section 9. Record answers with dates.

**Stage 1. No branching at all.** Fresh Kiro session per attempt. Anchor plus constraint compiler plus gate registry. Gates accumulate in `.kiro/hooks/`. Tasks 1 to 3 of the worked example, unit-test oracle, worktree rollback.
*Kill criterion:* if gate yield is low, the loop was never the problem. Stop here and keep the gates.

**Stage 2.** Measure attribution distance on real trajectories.
*Kill criterion:* mass at zero means no rewind, no branch table, no LangGraph. Ship stage 1.

**Stage 3.** Compensations proven on a deploying step. Add task 4.

**Stage 4.** Branch table, only if stage 2 justified it. Options: `/rewind` plus worktree with own branch_id, or LangGraph as orchestrator with Kiro over ACP as the doer node and gates staying in the repo.

---

## 11. Prior art

**Algorithm, exists.** LATS: nodes encode task input, action history and observations; MCTS selects, expands, evaluates, backpropagates; on unsuccessful trajectories the model self-critiques and updates strategy for the next episode. Implementations in LangGraph and LlamaIndex. LangChain positions it as unifying Reflexion, Tree of Thoughts and plan-and-execute.

**Substrate, does not exist.** SWE-agent uses git stash for file-level versioning and discards process state. OpenHands, Aider and other leaderboard systems execute linearly with no rollback beyond restarting. DeltaBox names this a capability gap rather than a design choice, and reports state management dropping from 47 to 77 percent of trajectory time down to 3 to 6 percent on SWE-bench MCTS workloads. Research stage, not a product.

**Within-episode escalation, contested.** ReflexGrad (May 2026) argues Reflexion defers recovery to the next trial, TextGrad sharpens a wrong strategy locally, and LATS/ToT widen the per-step action space without updating policy. Code released.

**Oracle assumption, universal.** Both Reflexion and LATS terminate on a binary correctness signal: exact match, unit test pass, or task heuristic. They assume a native oracle. Section 5.2 exists because most real work does not have one.

**LangGraph specifics.** Time travel offers replay (retry from a prior checkpoint) and fork (branch with modified state). Nodes after the checkpoint re-execute including LLM calls, API requests and interrupts, so replay is not cache read. Docs are explicit that side effects must be wrapped, mutating operations still need idempotency, and node boundaries must be engineered as replay boundaries. Persistent checkpointers include DynamoDBSaver. Open-source library is single-process, with no coordination preventing two processes resuming the same thread_id.

**Design reading, not scores.** SentinelBench for monitoring tasks where progress requires waiting until the environment permits it, and ARE where the world evolves independently of the agent. That is the regime an ERP integration actually occupies, and it breaks the assumption that state changes only when the agent acts. WebAnchor reports first-plan-step errors reducing Pass@1 by up to 30 points, which argues for gating the plan before gating the steps.

---

## 12. Errata from this session

Logged because the design's own thesis is that fluent claims need anchors.

1. **"Checkpoint is gone. Confirmed."** Overstated from `/help` absence alone. `/checkpoint` was flag-gated in 2.x, and flag-gated commands can be hidden until enabled. Two hypotheses were collapsed into one. Correct resolution is section 9 item 3.
2. **"Your driver cannot exec slash commands, it has to hold a session open and write into the TUI."** Wrong. Interactive chat is an ACP client, and `_kiro.dev/commands/execute` dispatches slash commands over the protocol.
3. **Kiro's in-product help agent** asserted `/checkpoint list` works in v3, grounded on 2.x docs rather than the running registry. Not an error of mine, but the clearest specimen of the failure mode this architecture is built to survive.
