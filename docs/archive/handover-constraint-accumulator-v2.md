# Handover v2: Constraint Accumulator

**Supersedes** `handover-verified-agent-loop.md` (same session, earlier draft).
Date: 2026-08-01
Status: design settled, most components already built, nothing bound together yet.

Provenance markers: **[VERIFIED]** read from vendor docs, source, or terminal output. **[CLAIM]** asserted, unverified. **[OPEN]** must be tested before build.

---

## 0. Thesis in one paragraph

An agent loop that retries with the failure in context is Reflexion and already exists. The durable object is not the search, it is the constraint that survives the episode. Every failure gets compiled into a predicate over artifacts that mechanically blocks its own recurrence, stored in the repo, provenance-stamped, expiring. The loop is the generator. The gate corpus is the product. This is Verification-Bounded Autonomy applied to the agent's own development process rather than to the agent's runtime.

---

## 1. Inventory: what already exists

The single most important finding of the session. Almost nothing here is new work.

| Capability | Where it already lives | State |
|---|---|---|
| Budget gates | Shape: DEGRADE 50%, FORCE_DECIDE 75%, STOP 100% | Built |
| Transactions with compensation | Shape | Built |
| Phase orchestration | Shape: EXPLORE / DECIDE / COMMIT | Built |
| Proof traces | Shape | Built |
| Human-readable rule DSL | Shape | Built |
| Manufactured-oracle factory | GreenRoom, bilateral KMS-signed attestation | Built |
| Four-class oracle taxonomy | VBA doctrine: native, proxy, manufactured, internalized | Written |
| Enforcement surface for coding agents | Kiro-CLI v3 hooks, exit code 2 | Vendor, verified |
| Programmatic control surface | Kiro-CLI ACP over stdio | Vendor, verified |
| Trace persistence | `~/.kiro/sessions/cli/<id>.jsonl` | Vendor, verified |
| Repeatable-task packaging | Skills format, `skill-creator` | Vendor, in use |
| Recurring real workload with a manufactured oracle | STOMDE monthly close skills | In use |

Shape: `github.com/vidanov/shape`, one file, zero dependencies, roughly 600 lines plus `bedrock.py`.

**Consequence.** The earlier draft of this handover specified a step contract with compensation, a budget ceiling, a gate registry, and a trace schema as new infrastructure. All four are Shape primitives. Rewrite as a binding, not a build.

## 2. What is genuinely new

Three things. Each is small.

1. **`shape_learn.py`.** The compiler that turns a failed trace into a proposed rule. Scoped in the May Shape conversation as trace-sink plus offline clusterer plus rule proposer, with `shape.py` frozen. Never shipped.
2. **Kiro binding.** Shape rules emitted as Kiro `PreToolUse` / `PreTaskExec` hooks so enforcement reaches the coding agent's tool calls, and Kiro trace events flowing back into Shape's trace sink. This is the missing wire.
3. **Skill format extension.** `manifest.json` plus `anchor.sh` plus `gates/` added to the existing skills convention, so a repeatable task carries its own admission predicate, verification, and accumulated constraints.

Everything else in this document is either already built or is measurement.

---

## 3. Conclusions that survived

Ordered by load-bearing weight.

1. **The value is constraint accumulation, not tree search.** Learning in context dies with the episode. Learning in a repo file outlives the session, the branch, and the operator. Retries are input to the accumulator, not the product.
2. **Rewind only earns its cost when attribution distance is greater than zero.** If failure surfaces where it was caused, plain oracle feedback wins on latency and simplicity. Rewind exists to fix at origin rather than at detection. Without distance, the doer patches defensively at the detection point, the anchor goes green, and the root defect ships.
3. **The observer needs an anchor, not an opinion.** A same-family model reading the doer's own narrative reproduces the doer's blind spot with added authority to rewind. Decorrelate by starving the observer of the doer's reasoning, not by switching vendor.
4. **Most observation is code.** Test runner, schema check, diff, reconciliation, cost counter. Model involvement is narrow and asynchronous.
5. **No model in the synchronous enforcement path.** From the May Shape analysis: enforcement plane stays deterministic and symbolic, the learned component sits in the control plane and only proposes. Governing one LLM with another in a real-time loop is self-defeating on auditability, latency, and cost.
6. **Constraints must be predicates over artifacts.** Prose reminders are instruction-based compliance and get violated under context pressure. A non-zero exit that blocks the call cannot be forgotten.
7. **Side effects are not rewindable by any vendor primitive.** Context forks. Files fork with git worktree. External state needs declared compensations, which Shape already implements.
8. **Loud failures need no gates.** A crash is already a perfect oracle. Gates exist for failures that produce plausible wrong output. Sort every assumption into loud or silent before spending effort on it.

## 4. Decisions reversed during the session

Recorded because the reversals matter more than the conclusions.

- Tree search / MCTS branching demoted from core mechanism to optional optimization. Most engineering tasks have one correct answer and a wrong assumption to forbid, not several viable branches to compare.
- LangGraph evaluated and rejected as primary substrate. It is an alternative to the driver, not to Kiro. Adopting it means writing the doer and losing spec agent, hooks, permissions model, and code intelligence, to gain fork-with-state.
- Model observer moved out of stage 1, per the May sequencing. Deterministic coverage first, model proposals later.
- Framework ambition dropped in favour of a format extension to skills, which already has distribution.

---

## 5. Platform findings: Kiro-CLI v3

### 5.1 Enforcement layer, the reason to stay on Kiro

**[VERIFIED]** Blocking triggers: `PreToolUse`, `PreTaskExec`, `UserPromptSubmit`. Everything else is observe-only.

**[VERIFIED]** `PreToolUse` blocks with exit code 2 and returns STDERR to the model. This is the gate primitive.

**[VERIFIED]** `PreTaskExec` fires before a spec task begins, has no matcher so it always fires, and can block. This is the step-boundary gate, and where Shape's budget ceiling and contract precondition attach.

**[VERIFIED]** `PostToolUse` and `PostTaskExec` emit trace events carrying tool_input and tool_response. This is the Shape trace-sink feed.

**[VERIFIED] Trap.** The v3 `agent` action type appends a prompt string to model context and spawns no subprocess. It cannot block. Vendor docs demonstrate it with an example that reads like a constraint but is only a suggestion. Constraint injection must always be `command` plus exit 2. Reserve `agent` actions for steering whose violation is acceptable.

**[VERIFIED]** Hook timeout defaults to 60s, 0 disables. Any observer invoked as a hook command must finish inside that.

**[VERIFIED]** v3 `permissions.yaml` gives capability policies where one rule allows or denies a category across all tools. Coarse layer above per-tool hooks.

### 5.2 State restore, the gap

**[VERIFIED]** `/rewind` forks the session at an earlier turn. Fork, not restore, so siblings survive. Conversation state only.

**[VERIFIED]** `/checkpoint` appears neither in the v3 feature comparison table nor in v3 `/help` output.

**[OPEN]** Removed, or hidden behind the 2.x flag `kiro-cli settings chat.enableCheckpoint true`. Both fit the evidence.

**[VERIFIED]** Even where checkpoints exist they never track file changes made by MCP tools or by bash commands the agent runs.

**[VERIFIED]** v3 removed `aws_tool` in favour of MCP servers. All AWS mutation now flows through the exact channel checkpoints do not snapshot. v3 therefore has strictly less usable state restore than 2.x for cloud work.

**[VERIFIED]** v3 session format is not backward compatible. v3 sessions cannot be resumed in V2.

**[CLAIM, rejected]** Kiro's in-product help agent asserted `/checkpoint list` works in v3. It is grounded on 2.x documentation, not on the running binary's registry. The session's cleanest specimen of a fluent, sourced, possibly wrong claim.

### 5.3 Control surface: ACP

**[VERIFIED]** `kiro-cli acp` speaks JSON-RPC 2.0 over stdio. `--agent <name>` selects an agent config.

**[VERIFIED]** Core methods: `session/new`, `session/load`, `session/prompt`, `session/cancel`, `session/set_mode`, `session/set_model`.

**[VERIFIED]** Interactive chat is itself an ACP client, so the TUI has no privileged channel and slash commands are reachable programmatically via `_kiro.dev/commands/execute`.

**[VERIFIED]** `_kiro.dev/commands/available` is a notification emitted after session creation carrying the command list. **Authoritative capability probe.** The binary enumerating itself, superseding `/help`, docs, and any model's claim.

**[VERIFIED]** Sessions persist to `~/.kiro/sessions/cli/<session-id>.json` plus `<session-id>.jsonl` append-only event log.

**[VERIFIED]** `ToolCall` and `ToolCallUpdate` notifications give live streaming.

**[VERIFIED, empirical probe at v1.28.0]** `sessionCapabilities: {}`, so no fork, list, or resume advertised at protocol level. `loadSession: true`, so **warm start is first-class while forking is not.** `mcpCapabilities` http false and sse false, so MCP over stdio only, which binds tightly to the aws_tool removal: HTTP MCP servers must be configured in `.kiro/settings/mcp.json`, not passed in `session/new`. Client capabilities unused, Kiro does its own file I/O, so the driver cannot intercept writes at protocol level and the gate must stay in hooks.

**[VERIFIED]** Extension methods are marked experimental and subject to change.

**[OPEN]** Whether `kiro-cli acp` runs the v2 or v3 engine, or accepts `--v3`. ACP docs updated 27 May 2026, v3 docs 17 June 2026. Determines whether the driver sees `PreTaskExec` at all.

**Doc quality note.** Vendor ACP examples show `agentInfo` 1.5.0 while empirical probing reports 1.28.0. Trust the wire, not the page.

---

## 6. Architecture as a binding

```
Kiro-CLI v3 (doer)
  |  PostToolUse / PostTaskExec  ->  trace events
  v
Shape trace sink                       [exists]
  |
  v
shape_learn.py (control plane)         [NEW, ~the only real code]
  |  proposes rule, human approves
  v
Shape rule DSL  ->  emit Kiro hook     [NEW binding]
  |
  v
.kiro/hooks/*.json  +  gates/          [enforcement plane, deterministic]
  |  PreToolUse / PreTaskExec, exit 2
  back to Kiro
```

Anchors run as plain commands, outside both planes. Shape budget gates attach at `PreTaskExec`. Compensations are Shape transactions, run in reverse before a fork completes. Files fork with git worktree, independent of `/rewind`, which is why the two compose.

`shape.py` stays frozen. Everything new is a companion module.

## 7. Oracle taxonomy (VBA four-class)

Do not attempt task-type agnosticism. Classify by verification type. The class determines what the loop is permitted to do.

| Class | Anchor source | Loop authority |
|---|---|---|
| **Native** | Already exists: tests, type check, cdk diff, schema validator | Auto-rewind permitted |
| **Proxy** | Correlated measure: linter, latency budget, cost delta | Advisory only, never rewind on proxy alone, must declare threshold and known bias |
| **Manufactured** | Built for purpose: row counts, checksums, referential integrity, sum reconciliation, partition-without-overlap. **GreenRoom is the factory for this class.** | Auto-rewind permitted once the anchor itself is validated |
| **Internalized** | Model judgment only: prose quality, argument strength | Auto-rewind disabled, route to human queue |

Intake step: classify before starting. If the honest answer is internalized-only, the loop does not apply and no framework repairs that.

## 8. Gate templates

The compiler does not write arbitrary scripts. It fills parameters in four templates. Free-form generation would need domain knowledge and produce unauditable gates.

1. **Forbidden pattern in scope.** Regex plus path glob.
2. **Required structure.** JSON Schema or required-field assertion over the artifact.
3. **Invariant.** Property assertion from a parameterized harness.
4. **Budget.** Numeric ceiling on tokens, time, or calls. Delegates to Shape.

Every domain's learning compresses into one of these four. That is why the compiler generalizes without knowing what a Lambda or a workbook is.

## 9. Scene bundles as a skill format extension

A skill is a human-authored prior: how to do the thing, written before the thing was done. A scene bundle is a machine-earned posterior: what went wrong doing it, compiled so it cannot recur. Same folder shape, opposite provenance.

Five deltas against the current skills format:

| | Skill | Scene bundle |
|---|---|---|
| Provenance | Written from intent | Distilled from a trajectory, cites the trace event |
| Enforcement | Read into context, skippable | Hook returns exit 2, not skippable |
| Validity | Eternal, rots silently | Hash manifest plus expiry |
| Verification | Produces output, does not check it | Ships its own anchor |
| Growth | Human edits | One gate per failure, automatic |

Warm start is the supported path here: `loadSession: true` at protocol level, plus `/chat save` and `/chat load` in v3. Forking is not. Build on warm start.

**Admission is a predicate, not a judgment.** Do not let a model decide whether a scene is relevant, because a stale scene injects confident wrong beliefs and, unlike a cold start, gives the agent no signal that its map is dead. Admit only if every load-bearing structure hash matches, gate registry version matches, agent config and model id match, and Kiro major version matches. Rank by intent only among admissible candidates.

The manifest derives itself: `PostToolUse` already records every fs_read and fs_write, so a scene carries `{path: content_hash}` for everything it actually touched. No hand-declared dependencies.

**Bundle contents:** session state, git ref, gate subset, oracle adapter, step contract template. This collapses the task-agnostic question. The bundle *is* the domain plugin.

---

## 10. First target: STOMDE `customer-deviation`

Chosen because side effects are files only. No AWS, no MCP mutation, no compensation layer needed. Real recurring work with a manufactured oracle, arriving monthly whether or not this gets built.

**Caveat: the skill is inherited, not authored.** That is a worse epistemic position, not a better one. The author holds the untyped knowledge of which bets are safe and which are load-bearing, and none of it is in the file. Do not assume the assumptions are ranked.

### 10.1 The bets it makes

From `SKILL.md`: tab `REV_CUST`; column N is YEAR; column AE is the variation-vs-last-FC block; rows labeled `Intercompany`, `Other Customers, Group`, `Other customers`, `TOTAL`, `ADJUSTMENTS` all exist; external customers sit above `Intercompany`; Reply companies sit strictly between `Intercompany` and `Other Customers, Group`; threshold default 100 k€; units EUR/000.

### 10.2 Sort into loud and silent

**Loud, ignore.** Label renamed, row lookup finds nothing, script crashes. Ten minutes lost. No gate needed.

**Silent, the whole point.** Three real ones:

1. **COGE inserts a forecast column.** Variation block shifts from AE to AG. The script reads AE, now something else. Every Delta in the board table is wrong. Nothing crashes, nothing looks odd, and no human re-derives 381,8 by hand.
2. **A new internal Reply company is added below `Other Customers, Group`.** Region logic classifies it as external, it clears 100k, and intercompany revenue appears as a named customer on a board slide.
3. **`ADJUSTMENTS` moves inside the external block.** It sums into MINOR CUSTOMER, and the reconciliation residual shrinks, which looks like an improvement.

### 10.3 The three files

**`anchor.sh`.** Line 62 of the skill already computes the reconciliation check. It **prints** it. Printing is advisory: an agent reads it, decides it looks fine, continues. Exit 1 stops the pipeline. Same computation, different authority. Add:

- Sum of table rows equals TOTAL, tolerance being the actual `ADJUSTMENTS` value, not a guessed epsilon. Catches 3.
- **Sum of all Deltas equals TOTAL current FC minus TOTAL last FC.** The important one. If the variation column shifted this breaks immediately. Catches 1, which is otherwise undetectable.
- %TOT sums to 100.0 within 0.1.
- Partition: every external row in exactly one of {named list, MINOR}, every internal row in REPLY COMPANIES, nothing double-counted, nothing dropped.
- No printed WIN/LOSS mover is an internal Reply company. Catches 2.

**`manifest.json`.** Hash structure, not values. Values change monthly, that is the job. Record a hash of the ordered label sequence in column A, a hash of sheet names, and the resolved column indices. Recompute next month, halt on mismatch, show the diff. Derive it mechanically: read `scripts/extract_deviation.py` and pull every literal. Each literal is one bet. This converts inherited implicit knowledge into a checkable list, which an inheritor cannot obtain any other way.

**`gates/`.** Line 48 already says rows are resolved by label, **not fixed indices**. Then columns are hardcoded N and AE. Same bug class, same workbook, fix applied to rows only, because the row version bit someone and the column version has not yet. A written lesson protects the case the author was thinking about. A gate blocking any hardcoded column letter protects the case nobody was thinking about. That is instruction versus enforcement, demonstrated inside the artifact itself.

### 10.4 Transfer test

Extract the literal sets from all three STOMDE skills (`customer-deviation`, `oc-coverage-fill`, `other-customer-burndown`) and compare. Literals present in all three are the real COGE contract. Literals appearing once are probably incidental. That ranking is what the original author held in their head.

Then test gates from one against another and count genuine blocks. That number is the transfer rate, and section 15 turns on it.

## 11. Other candidates, ranked by oracle strength times repetition

1. STOMDE monthly assets. Manufactured oracle, monthly, files only.
2. IaC conformance remediation across clients. Native oracle via cdk diff and policy validation. Doubles as the cross-repo transfer test.
3. PII redaction batches. Manufactured: re-scan output for zero findings, assert page count and non-PII text unchanged.
4. Runtime and dependency deprecation sweeps. Native, high repetition, low creativity.
5. Incident to gate. Every postmortem emits a constraint. The enterprise-legible framing of the whole loop.
6. Article pipeline. Mostly internalized, so no auto-rewind. Mechanical part is real: banned-phrase list, no em dashes, every claim carries a citation, link liveness. Voice rules are literally gate template 1.

---

## 12. Loop invariants

- No rewind without at least one new gate that would have blocked the failed branch. Otherwise the loop re-rolls dice with a clean context. Also makes search finite in constraint space rather than trajectory space.
- Rewind target must cite a trace event. No citation, fork from parent.
- Same constraint proposed twice means oscillation. Stop and escalate.
- Every gate carries path scope and domain tag in its matcher. Without this, gates accumulate globally and by month three the repo refuses everything for reasons nobody can reconstruct.
- Every gate records the trace event and branch that produced it, plus a review-by date. A gate that has not fired in ninety days is dead weight or fully internalized. Both mean retire.
- Proxy-class anchors never trigger auto-rewind.
- Scenes are versioned and frozen during any measurement window.

## 13. Metrics

### 13.1 Borrowed

- **pass^k, not pass@1.** τ-bench's pass^k is the probability of succeeding on all k attempts, and reported GPT-4o at 61% pass@1 against 25% pass@8 on retail tasks. The pass@k minus majority@k distance is the reliability gap.
- **Per-step hazard.** METR's 50% time horizon was roughly fifty minutes in early 2025, doubling roughly every seven months. Follow-up work found a per-step hazard rate with success dropping exponentially in length. Implication: failure is per-step, so the intervention is per-step gating rather than better planning.
- **Dense reward over binary.** LHTB grades subtasks because binary pass/fail hides substantial partial progress.
- **Unresolved taxonomy.** Copy directly: timeouts (still working at budget expiry), early exits (self-terminated, no harness error), harness errors. Three different bugs. Pass rate conflates all three.
- **Cost axis.** LHTB averages roughly 228 episodes, 85 minutes and about $10 per task, top models near $21, and performance does not scale linearly with API cost. Any architecture claim without cost is unfalsifiable, because pass rate can be bought with attempts.

### 13.2 Confound

Harness-induced belief divergence: harness choice shifts agent beliefs and the divergence grows with rollout length rather than stabilizing. The scene bundle is part of the harness. Fix model and task set, vary exactly one harness element.

### 13.3 Protocol

Three arms, same model, same tasks, same anchors: bare loop; loop plus oracle feedback in context (Reflexion baseline, which is what Kiro already does natively with a test command); loop plus compiled gates. Report pass^4, cost per task, and the three-way unresolved decomposition.

### 13.4 Metrics that are yours

No public benchmark measures these. They are the falsifiable core.

- **Gate yield.** Fraction of gates that later block a genuine regression. Near zero means the compiler generates superstition.
- **Gate false-block rate.** Blocks on correct actions. The cost side of enforcement, unreported anywhere.
- **Attribution distance distribution.** Detection step minus cause step. Measure before building any branch table. Mass at zero means do not build rewind.
- **Recurrence.** Same failure class across sessions, before and after a gate exists. The persistence claim. No current benchmark tests an agent across a multi-day session with persistent memory.
- **Transfer rate.** Gates from repo A that block genuine regressions in repo B. Section 15 turns on this.
- **Poisoned hit rate.** Not cache hit rate. The fraction of admitted warm starts that produced a worse outcome than cold would have. Tells you whether the admission predicate is tight enough, and it is the only way this fails expensively.

---

## 14. Open questions with exact tests

Run before writing code. Each has a cheap empirical answer.

1. **Does `kiro-cli acp` run the v3 engine?** `kiro-cli acp --v3`, `initialize`, `session/new`, read `agentInfo.version` off the wire.
2. **What commands actually exist?** Same session, read `_kiro.dev/commands/available`. Authoritative. Settles `/checkpoint` and gives the driver a startup capability gate.
3. **Is `/checkpoint` removed or hidden?** `kiro-cli settings chat.enableCheckpoint true`, restart, `/help`, grep. If absent, type `/checkpoint list` and treat unknown-command as removal proof. Architecture does not move either way.
4. **Does `/rewind` mint a new session id?** `/session-id`, `/rewind`, `/session-id`. Decides whether the branch table keys on session_id or needs branch_id with a parent pointer.
5. **Does `PreTaskExec` fire when the spec runs over ACP rather than the TUI?** Install a logging hook, run a spec task via `_kiro.dev/commands/execute`, check the log. If it does not fire, the step-boundary gate is TUI-only and the driver must enforce budgets itself.

## 15. Build order

Corrected per the May Shape sequencing: extend deterministic coverage before reaching for a model.

**Stage 0.** Run section 14. Record answers with dates.

**Stage 1. Trace sink, no model, no branching.** Kiro `PostToolUse` / `PostTaskExec` into Shape's trace sink. Retrofit `customer-deviation` with `manifest.json`, `anchor.sh`, `gates/`. Write gate proposals **by hand** for the first month. Fresh session per attempt.
*Kill criterion:* if hand-written gates never fire on real work, enforcement is not the bottleneck. Stop, keep the anchors.

**Stage 2. Measure.** Gate yield, false-block rate, attribution distance, recurrence across the three STOMDE skills.
*Kill criterion:* attribution distance mass at zero means no rewind, no branch table, no LangGraph. Ship stage 1.

**Stage 3. `shape_learn.py`.** Automate the compiler only once a month of hand proposals shows what they actually look like. Offline clustering over accumulated traces, proposals into human approval, `shape.py` frozen.

**Stage 4. Transfer test.** Gates from STOMDE against IaC conformance, or across two client repos. This is the result that decides project size.

**Stage 5. Side effects.** Bind Shape transactions to a deploying step. Only now does AWS enter.

**Stage 6. Branching.** Only if stage 2 justified it. `/rewind` plus worktree with own branch_id, or LangGraph as orchestrator with Kiro over ACP as the doer node and gates staying in the repo.

## 16. Strategic sizing

**The engine is small.** Four templates and an exit code, a few hundred lines. Hooks plus persistent learned constraints is an obvious roadmap item for Anthropic, AWS, and Cursor. Coupling to Kiro-CLI is coupling to the smaller product. Do not position on the engine.

**The measurement is worth more.** Nobody publishes gate yield, false-block rate, or cross-session recurrence, and the benchmark field explicitly does not test multi-day sessions with persistent memory. Metric definitions are cheaper to ship than a framework and travel further, because a definition outlives an implementation.

**One result decides the size.** Transfer rate. If gates are idiosyncratic per repo, this is an internal productivity asset and two talks. If a corpus for automotive ERP idempotency and event integration transfers across OEMs, the corpus is the asset, it compounds, it is domain knowledge rather than code, and no platform vendor can ship it because they lack the domain. That lands on infrastructure clients already run: IATF, ASPICE, 8D, DOORS.

**Where this converges with the existing positioning.** The July conclusion was that AgentCore Policy commoditized the governance primitives and Shape's durable surface is signed portable attestation rather than enforcement. A gate registry with provenance, the trace event that earned each gate, and an expiry date is exactly a KMS-signable artifact. Not "our agent was governed," but "these specific constraints were enforced on this run, here is the failure that produced each one, verifiable without entering our account." The accumulation thesis and the attestation thesis are the same object. That is also the answer to Continuum's supervised-learn-mode framing: a boundary you cannot independently verify is unfalsifiable.

**Bear case to hold onto.** Agent infrastructure is the most crowded space in the industry, there is no data moat and no network effect, and the honest outcome distribution is weighted toward small. Build stage 1, measure transfer, publish the metric definitions regardless of result. A clean negative transfer result is still worth more than the tool.

---

## 17. Prior art

**Algorithm, exists.** LATS: nodes encode task input, action history, observations; MCTS selects, expands, evaluates, backpropagates; on unsuccessful trajectories the model self-critiques and updates strategy for the next episode. Implementations in LangGraph and LlamaIndex. LangChain positions it as unifying Reflexion, Tree of Thoughts, and plan-and-execute.

**Substrate, does not exist.** SWE-agent uses git stash for file-level versioning and discards process state. OpenHands, Aider and other leaderboard systems execute linearly with no rollback beyond restarting. DeltaBox names this a capability gap rather than a design choice and reports state management dropping from 47 to 77 percent of trajectory time down to 3 to 6 percent on SWE-bench MCTS workloads. Research stage.

**Within-episode escalation, contested.** ReflexGrad (May 2026) argues Reflexion defers recovery to the next trial, TextGrad sharpens a wrong strategy locally, and LATS/ToT widen the per-step action space without updating policy. Code released.

**Oracle assumption, universal.** Both Reflexion and LATS terminate on a binary correctness signal: exact match, unit test pass, or task heuristic. They assume a native oracle. Section 7 exists because most real work does not have one.

**LangGraph specifics.** Time travel offers replay and fork-with-modified-state. Nodes after the checkpoint re-execute including LLM calls, API requests and interrupts, so replay is not cache read. Docs are explicit that side effects must be wrapped, mutating operations still need idempotency, and node boundaries must be engineered as replay boundaries. Persistent checkpointers include DynamoDBSaver. Open-source library is single-process with no coordination preventing two processes resuming the same thread_id.

**Design reading, not scores.** SentinelBench for monitoring tasks where progress requires waiting until the environment permits it, and ARE where the world evolves independently of the agent. That is the regime an ERP integration occupies, and it breaks the assumption that state changes only when the agent acts. WebAnchor reports first-plan-step errors reducing Pass@1 by up to 30 points, which argues for gating the plan before gating the steps.

**Competitive note.** Microsoft's Agent Governance Toolkit occupies the heavyweight policy-engine space, which is why Shape's single-file, zero-dependency, human-readable-trace positioning is the differentiator and should not be traded away.

## 18. Errata from this session

Logged because the thesis is that fluent claims need anchors.

1. **"Checkpoint is gone. Confirmed."** Overstated from `/help` absence alone. `/checkpoint` was flag-gated in 2.x and flag-gated commands can be hidden until enabled. Two hypotheses collapsed into one. Resolution is section 14 item 3.
2. **"Your driver cannot exec slash commands, it has to drive the TUI."** Wrong. Interactive chat is an ACP client and `_kiro.dev/commands/execute` dispatches slash commands over the protocol.
3. **Specified compensation, budget gates, gate registry and trace schema as new work.** All four are existing Shape primitives. Same error pattern as June, when Shape's cost control was misremembered as absent and corrected only after reading the repo. Read the repo first.
4. **Attributed `customer-deviation` to the user.** It is inherited, which changes the manifest task from recall to literal extraction.
5. **Kiro's in-product help agent** asserted `/checkpoint list` works in v3, grounded on 2.x docs rather than the running registry. Not my error, but the clearest specimen of the failure mode this architecture exists to survive.
