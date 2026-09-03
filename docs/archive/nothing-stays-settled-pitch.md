# Nothing Stays Settled

Positioning document. Companion to `folder-shell-build-plan.md`, which is the mechanism. This is the argument.

---

## 1. The pitch

**In one line.**
Coding agents re-derive what was already settled, and there is no mechanism that makes a settled question stay settled.

**In thirty seconds.**
Every time an agent starts work, it re-decides things your team decided months ago. The industry's answer is bigger context windows and better models, which means re-reading everything and re-deriving faster. Neither makes anything stay decided. We measured what actually happens: in 15 of 17 rule violations across 23 sessions, the rule was not ignored. It was never in the model's context at all. The fix is not a smarter agent. It is delivering the settled constraint at the moment of the decision, enforcing it with something the agent cannot talk its way past, and keeping a record of which constraints held.

**In two minutes.**
Three things accumulate in real engineering work: what has been decided, what has been ruled out, and what must never happen again. Human teams hold these in review culture, coding standards, and institutional memory. Agents hold none of them. Each session starts from zero on questions that were answered last week, and produces plausible work that violates decisions nobody restated.

The current tooling response is rules files. Every vendor has one. They are loaded at session start and then compete for attention with everything else in a long context. We tested whether they even arrive. Often they do not: in this substrate, custom agent configurations do not load rules at all unless explicitly wired, and one trigger mode is broken in the exact mode we work in.

So the gap is not in reasoning. It is in delivery, enforcement, and proof. Deliver the constraint when the action happens, not at session start. Enforce it with an exit code, not by asking the agent to confirm it complied. Keep the record so someone can verify afterwards which constraints were in force. That last part is what a regulated industrial buyer is actually paying for.

---

## 2. What breaks today

Three failures, each with a different cause and a different fix. Conflating them is why current tools do not work.

**The constraint was never present.** Written down, in the repository, and not in the model's context when it mattered. This is a delivery failure and it is by far the most common. **[M]** 15 of 17.

**The constraint was present and lost the competition for attention.** A rule in a ten-thousand-token block at session start, against a decision three hours later. This is an attention failure. Less common than the first, and it is the one everyone assumes is the whole problem.

**The work was verified against nothing.** No oracle, so nothing detected the violation. **[M]** The single most common recurring failure pattern was the agent claiming a task was done without running the command that would have shown otherwise.

Note what none of these are. None is a reasoning failure. **[M]** Of the four failure classes that recurred across sessions, not one would have been prevented by a more capable model.

---

## 3. Why the obvious answers do not work

**Bigger context windows.** Re-reading everything is not remembering. A vendor already concluded this against its own interest: Codex caps a million-token model at 272K deliberately, because a larger window delays compaction until the summary itself becomes unreliable. Capacity is not the constraint.

**Better models.** Improves the third failure a little and the first two not at all. A model cannot follow a rule it never received.

**Rules files.** Necessary, insufficient, and unverified. Nobody checks whether their rules arrive. We did, and they often do not.

**A supervisor agent.** The intuitive fix, and structurally broken. A supervisor reads the worker's report. If the worker's premise was wrong, the supervisor evaluating against the same premise passes it. Same family, same assumptions, one extra layer of confidence. This is the 1986 correlated-failure result: independently built programs written to a shared specification fail together, because the correlation lives in the specification rather than the implementations.

We nearly published a false positive from exactly this. A verification check we wrote was subtly wrong. Had it gone unexamined, the next step would have compiled a constraint that satisfied the broken check. Compliance would have risen. The measured result would have looked strong. The constraint and the checker would have been wrong in the same direction, and their agreement would have been reported as proof.

**Self-report enforcement.** ZORO, published April 2026, ships the closest thing to this architecture and enforces by requiring the agent to prove it followed each rule. That is the agent grading itself against its own understanding. It fails the same independence test.

---

## 4. The gap, precisely

Four claims. The first two are measured. The third is the wedge. The fourth is the strongest and the least obvious.

1. **Nobody delivers constraints at the point of decision.** Everything fires at session start, on a user keyword, or on context pressure. The leading open-source implementation has two open issues requesting exactly this, noting that its triggers respond only to what the user says and not to what the agent does. **[M]** Zero of six violations in our sample were predictable from the initial request.

2. **Nobody enforces without self-report.** One vendor substrate has a hook that returns a non-zero exit and blocks the action. That is the only mechanism in the space that does not route through the model's own claim about its behaviour.

3. **Nobody measures whether their rules arrive.** There is no benchmark and no standard practice. Every team running production agents has written a rules file. Almost none can tell you its delivery rate.

4. **Everybody coordinates parallel agents by messaging.** The field treats coordination and communication as the same thing. Practitioner sources define the absence of coordination as agents that cannot communicate, share a task list, or resolve dependencies. Anthropic's Agent Teams is a team lead, a shared task list with dependency tracking and file locking, and teammates messaging each other peer-to-peer. Augment's Intent uses a living spec as a shared ledger that propagates requirement changes to all active agents. Every one of these scales as a communication problem.

Shared enforced constraints are an alternative. Workers do not need to talk if they all satisfy the same locally-checked invariants. Semantic conflicts get prevented at authoring time rather than negotiated at merge time, and the coordinator disappears rather than getting smarter.

The third claim is the commercial wedge: it is a diagnostic anybody can run on their own logs, it produces an uncomfortable number, and the number motivates the rest. The fourth is the architectural one, and it is where the defensible position sits.

### 4.1 Filling gap one: delivery

**Mechanism.** Two triggers, in order of preference. Path-triggered rules fire when the agent reads a file matching a glob, are fully deterministic, and need no model. Description-routed rules fire when the request matches a rule's description, and are the fallback where no glob can express the trigger. Both already exist in the substrate.

**What is missing.** A trigger on what the agent *does*, not on what the user said. **[M]** Zero of six violations were predictable from the initial request, so a session-start trigger cannot reach them. In this substrate `postToolUse` does not fire in the session type we use, so the mechanism is a process that tails the live session log and writes the relevant rule into the active context when a matching action appears.

**Cost.** **[M]** About 175 tokens per injection against a correction that averaged about 20,000. Break-even is roughly one injection in fifty landing usefully.

**How it is falsified.** A probe token in each rule file, echoed back on request. If the token appears, the rule arrived. If violations continue at the same rate once delivery is confirmed, the gap was misidentified and this plane is worthless. Not measured yet.

### 4.2 Filling gap two: enforcement

**Mechanism.** A rule that has recurred across three or more distinct sessions is rewritten as a program. It runs before the action, exits non-zero, prints why, and the agent cannot proceed. Not a paragraph, not a request to confirm compliance.

**What is missing.** Almost nothing technically. **[M]** What is missing is evidence: of four recurring rules, only one has enough observations to justify a gate. The others have two each.

**Cost.** Near zero per check. The real cost is a false block, which is the only failure mode that damages trust in every subsequent block.

**How it is falsified.** Every gate ships with a fixture where it must *not* fire, drawn from a real case. If false-block rate is material, enforcement costs more than it saves. Also unmeasured, and it is the number nobody in this field reports.

### 4.3 Filling gap three: measurement

**Mechanism.** Four things, none of which are hard and all of which are usually skipped. Corrections captured with attribution: which worker, which task, which rule, and the commit hash of the constraint set at dispatch. A denominator of eligible opportunities, so a violation count means something. Labels applied blind, before the outcome is known. Validation sampled in both directions, so precision and recall are both reported.

**What is missing.** Recall. **[M]** Precision on one automated judgment was about 40 percent on 20 samples, interval 19 to 64. Recall was never measured, so every number derived from it is a floor of unknown tightness.

**Cost.** Hours of manual labelling per batch. That is the entire cost, and it is why nobody does it.

**How it is falsified.** It cannot be. It is a method, not a claim. What it can do is produce a result that kills the other three gaps, which has already happened twice on this project.

### 4.4 Filling gap four: coordination

**Mechanism.** This is where the transferred theory pays off concretely. Not every invariant can be enforced without coordination, and the distributed-systems literature says exactly which ones can.

**The test:** if two workers each independently satisfy the constraint, and their results are merged, does the constraint still hold? If yes, it is coordination-free: gate it locally in every worker and never talk about it. If no, it needs a single owner.

| Constraint | Merge-safe? | Handling |
|---|---|---|
| No hardcoded column indices in this module | Yes | Local gate, no coordination |
| Dedup keys derive from payload, not transport metadata | Yes | Local gate |
| Two workers never touch the same path | Yes | Rejected at plan validation |
| Format, naming, style rules | Yes | Local gate |
| Exactly one migration in this release | **No** | Single owner, queued |
| Migration N applies before N+1 | **No** | Ordered, single owner |
| Total spend across all workers under a ceiling | **No** | Shared decrementing budget |
| Frozen interface stays consistent | **No** | Frozen before dispatch, no worker may write it |
| Nothing deletes what another worker references | **No** | Owner, or serialize |
| Two workers never bind the same port or container name | **No** | Allocated from a pool by the orchestrator |

Everything in the "yes" column costs nothing and needs no messages. Everything in the "no" column is where the owner mechanism, the queue, and the frozen-interface rule come from. They are not arbitrary design choices; they are the exact set of cases the theory says cannot be handled locally.

**What is missing.** All of it. Nothing here is built, and no constraint corpus has been classified this way.

**How it is falsified.** Two ways, and both are cheap. First, count how many of your recent task pairs were genuinely path-disjoint. If most work touches shared code, the coordination-free set is small and this is the wrong investment. Second, classify an existing constraint corpus by the merge-safety test. If most constraints land in the "no" column, you have rebuilt a coordinator with extra steps.

---

## 5. The idea, stated properly

**A settling layer for agent work.**

Three things accumulate, at three lifetimes, with one schema: statement, evidence, and a validity condition that says when it stops being true.

| Lifetime | What accumulates | Example |
|---|---|---|
| Task | eliminations | not the connection pool, pool metrics show 3 of 20 in use |
| Project | decisions and constraints | dedup keys derive from payload, never transport metadata |
| Organisation | invariants | no client account credentials in any agent session |

Each is delivered when relevant, not when the session begins. Each is enforced by a program rather than a paragraph, where the stakes justify it. Each carries provenance: what established it, when, and what would invalidate it.

**The reframe that matters.** The durable output of agent work is not the answer. It is the narrowing. Forty things ruled out compress to forty lines; the trajectory that ruled them out is fifty thousand tokens. Today the forty lines are thrown away and the fifty thousand tokens are what we try to preserve, compress, and pay to re-send. That is backwards.

A permanently enforced constraint and a session-scoped elimination are the same object at different durations. That is one mechanism, not two products.

---

## 6. What changes in practice

Five properties. Each has a mechanism, a contrast with how it works today, and an honest limit.

### 6.1 You are never blocked

**Today.** You give an agent a task and wait. Or you open a second session and lose the thread. Or you interrupt it and destroy work in progress. The conversation and the execution are the same object, so one blocks the other.

**Mechanism.** They are separated. The conversation holds the plan and does almost no work. Every task goes to a spawned session in its own directory, reports back, and dies. Dispatch takes a turn. Then you keep talking.

**What changes.** You describe a task, it leaves, and the next thing you say is a new topic. Discuss an unrelated idea, ask about something in the codebase, plan the next piece of work. Nothing is queued behind execution. Three workers running does not slow the conversation, because the conversation is not doing the work.

**Precondition, and it is not optional.** Interfaces must be frozen before dispatch. A worker sent against a contract that can still change returns results you cannot compare. Freeze granularity should match what you are willing to throw away: freeze the whole plan and one change invalidates everything in flight; freeze per interface and a change costs only the work behind it.

**Limit.** Completions arrive when they arrive. They append to a queue and never interrupt your current turn. If a completion injected itself into your conversation, the blocking problem would simply have moved.

**But silence is ambiguous, and that has to be solved rather than tolerated.** A hung worker looks exactly like a busy one. So every worker always displays a state, derived mechanically: working, stalled, looping, blocked on input, over budget. Progress is measured by *distinct* actions, because a worker repeating the same call is emitting output without advancing. Stalls escalate on a ladder: change the state, then notify locally, then snapshot the findings and cancel with compensation. Findings are extracted before any kill, because a worker that ruled out five hypotheses before hanging still has value.

One state breaks the no-interrupt rule deliberately. A worker blocked on a permission request is not making progress and never will without you, so it notifies immediately on every channel. A worker silently waiting on an approval nobody can see is the worst failure available here: it holds a slot, looks busy, and never finishes.

### 6.2 Context stays flat, and nothing is lost

**Today.** Context grows monotonically. At some threshold it is compacted, meaning a model summarises the conversation and the original is replaced. You lose reasoning chains, specific tool output, and the intermediate steps of a debugging session. Most tools give no indication of how many times this has happened.

**Mechanism.** The conversation never receives tool output. Not filtered, not summarised. It never arrives. Workers return a verdict, a diff reference, and findings. The trajectory stays in the worker's own log, on disk, complete and inspectable.

**What changes.** This is compaction by topology rather than by summarisation, and it is strictly better on both axes: the conversation cannot grow, and nothing is destroyed. You can read any worker's full trajectory later, because it was never compressed to fit.

**Cost model worth internalising.** Content near the start of a session is re-sent on every subsequent turn. Content at the end is barely re-sent. So cost is size multiplied by turns-persisted, not size. A short exchange early can cost more than a long one late. This is why removing content from the middle is expensive and why the conversation must simply never accumulate rather than being periodically cleaned.

**Workers still compact, and that is where the design earns the rest of its keep.** The conversation never needs compaction because it never grows. Workers do grow, so they are compacted early rather than under pressure, kept inside a target band. A summary written at 30 percent of the window is better than one written at 90, because there is less to compress. One vendor already caps a million-token model at 272K for exactly this reason.

And compaction is the harvest point. It is the moment a trajectory is about to be destroyed, so it is the moment findings are pulled out into durable storage with their validity conditions. Do that and the summary can be aggressive, because what mattered is already saved elsewhere. Skip it and every compaction quietly discards the narrowings that cost the most to produce.

**Limit.** One violation undoes it. Pulling a worker's trajectory into the conversation to inspect it makes that context permanent. Inspection happens by opening the log, outside the conversation, and the report boundary is enforced mechanically rather than by convention. Separately, compaction invalidates the prompt cache, so band tuning trades cache warmth against summary quality. Both knobs are currently guesses.

### 6.3 Parallelism bounded by verification, not by agents

**Today.** Isolation is a solved problem and every orchestrator ships it. The hard part is recombination, and the field's own conclusion is that worktrees let agents create conflicts with each other that nobody notices until merge. Coordination is attempted by communication: a shared task file, a coordinator agent, sequenced merges.

**Mechanism.** Three deterministic checks, no model involved.

- Each task declares the paths it may touch. Two tasks whose globs can overlap are never dispatched concurrently. Rejected at plan validation, before anything runs.
- `git merge-tree` finds conflicts between two branches without merging them. Run pairwise on a timer, it converts merge-time surprise into dispatch-time information. Almost nobody wires this into a loop.
- Shared resources get a single owner. One migration owner, one holder of the test database, one holder of the cloud account.

**Coordination without communication.** This is the structural difference from every other orchestrator. Parallel workers do not need to talk if they all satisfy the same enforced constraints. A constraint is a shared invariant, checked locally, with no message passing. Semantic conflicts get prevented at authoring time rather than detected at merge time, because the write that would have caused one is blocked.

**The real ceiling, stated honestly.** Parallelism is bounded by independent verification throughput, not by worker count. If verification needs one database, one port, or one cloud account, effective parallelism is one regardless of how many workers run. Ten workers that cannot verify concurrently are ten workers queued behind a single check. Raising the ceiling means an oracle instantiable per branch. Everyone else in this space is optimising isolation, which is free, and ignoring verification capacity, which is the actual constraint.

**Limit, and it is a serious one.** Parallelism frequently loses. Every fresh worker starts with a cold prompt cache, so the topology creates cold starts by construction. Per-worker setup (dependency install, cold build cache, container start, test database) can exceed the work itself. Sequential narrowing is lost, because one process applies what it learned at step *k* at step *k+1* while parallel workers all search the original space. And the merged result was never tested by any worker, so integration verification is a cost that exists only in the parallel case.

The test is arithmetic, not judgment: if median per-worker setup exceeds median task duration, parallelism loses. Freezing interfaces is serial, so Amdahl caps the speedup regardless of worker count. Measure how many recent task pairs were genuinely path-disjoint before building any of it.

**This is a separate claim from the rest.** If parallelism does not pay on a given codebase, the conversation still stays small and you can still leave. Those properties do not depend on it.

### 6.4 Failures are paid for once

**Today.** You correct the agent. It fixes the thing. The correction lives in a context window that will be compacted, cleared, or forked. Next week the same mistake appears in a different session and you correct it again. The correction was real work and it evaporated.

**Mechanism, four steps.**

1. **Capture with attribution.** A correction is the only ground truth in the system: a human judgment of failure, recorded at the time, by the person qualified to make it. But the correction is spoken to the conversation while the failure happened in a worker that has already exited. So each record carries the worker session, the task, the rule, the git commit of the constraint set at dispatch, and whether the constraint was delivered to that worker at all.

2. **Count recurrence across sessions.** Two corrections in one session is one failure corrected twice. Recurrence requires distinct sessions, and it needs a denominator: how many opportunities existed where the constraint applied. Without the denominator, a violation count is unfalsifiable in either direction.

3. **Route by cause, not by symptom.** The `delivered` field splits corrections into two populations with different fixes. Not delivered means fix delivery. Delivered and violated anyway means consider enforcement. **[M]** That split was 15 to 2 in our sample, which is why most of the value is in delivery and not in enforcement.

4. **Compile only what earned it.** A constraint that recurred across three or more distinct sessions becomes either a delivery trigger or a program that blocks the action. Everything else stays documentation. **[M]** Of four recurring rules, only one had enough observations to justify a gate.

**Why this is not another memory feature.** Three products already write their own rule files. The difference is what a rule is. A paragraph in a file is a request, and it competes for attention. A program that returns a non-zero exit and blocks the action is not a request. It also carries provenance: the failure that produced it, when, and what would make it obsolete.

**The withdrawal path matters.** Sometimes the correction was wrong, or the requirement changed afterwards. Records carry a status and only confirmed ones count. Without that, every count is inflated by your own reversals, and the inflation runs in the direction that makes the project look necessary.

**Limit.** Two prior attempts to automate this classification produced confident numbers that were invalid, both because the metric was chosen for being easy to compute. Classification criteria must be frozen before reading logs, and every automated judgment validated against a hand-checked sample in both directions.

### 6.5 You can leave

**Today.** Long-running agent work requires supervision, because there is no channel that tells you when something needs you and no state that survives your absence.

**Mechanism.** Durable state lives in the project, not in a session, so nothing is lost while you are gone. Out-of-band signals for the states that need a human: sound on completion, a badge for queue depth, a message for anything past a duration threshold.

**Escalation rule.** Notify only on blocked constraint, exhausted budget, semantic conflict between two clean branches, or an oracle that cannot run. **Never notify on progress.** A notification that fires when nothing is needed trains you to ignore all of them, including the ones that matter, and that failure is worse than the notification that caused it.

**What you return to.** A queue of reports, a set of merged branches, a list of what was ruled out, and any new constraint proposals awaiting your approval. Not a transcript to read.

---

## 7. Where the money is

Not developer productivity. That market is crowded, the buyers are individuals, and the value is hard to attribute.

**The buyer is an organisation that must answer for what an agent did.** Automotive, industrial, anything under IATF or ASPICE. Their question is not whether the agent was fast. It is whether the constraint was in force, and whether that can be shown to a third party without granting access to their systems.

A constraint registry with provenance, the failure that produced each entry, and an expiry date is a signable artifact. The claim it supports is narrow and checkable: **these specific constraints were enforced on this run, here is the failure that produced each one, and it is verifiable without entering our environment.**

That is a different sentence from "our agents are governed," and it is the only version a regulated buyer can act on.

---

## 8. What we are not claiming

Stated plainly, because the credibility of everything above depends on it.

- **Not that this makes agents smarter.** It makes settled things stay settled.
- **Not that the numbers generalise.** They are properties of one substrate at one version. Fix the vendor bug and they change. The measurement method generalises; the results do not.
- **Not that the recurrence rate is large.** **[M]** 8.6 percent of sessions hit the loop we measured. Small population. It ran 6.5 times longer than clean sessions, which is why it is worth addressing, but this is not a claim about most work.
- **Not proven.** One finding is measured. The architecture is derived. Two prior measurement attempts in this project produced confident numbers that turned out to be invalid, both because the metric was chosen for being easy to compute rather than for measuring the claim.

### 8.1 Novelty audit, per claim

Nothing here is claimed wholesale. This table exists so that no reviewer has to construct it, and so nothing gets asserted as new that is not.

| Claim | Prior art | Verdict |
|---|---|---|
| Coordination of parallel coding agents by shared enforced constraints instead of messaging | Field frames coordination as communication. Claude Code Agent Teams: shared task list, file locking, peer-to-peer messaging. Augment Intent: living spec as shared ledger. Blackboard architecture (Han & Zhang 2025) is shared context, not enforced invariants | **New in this domain.** Not new in computer science, see 8.2 |
| Correction records with steering-commit provenance, separating a violated rule from a rule that did not yet exist | None found | **New** |
| Validity conditions as executable commands on findings | Time-based decay exists: agentmemory decays low-confidence entries below 0.3 after seven days | **New.** A command that re-checks is not a timer |
| Per-harness delivery measurement as a diagnostic | MemoryCode tests cross-session instruction retrieval with distractors. SWE-Bench-CL measures forgetting and forward/backward transfer. Neither measures whether a given tool delivers its own rules | **New as method** |
| Eligible-opportunity denominator for recurrence | Standard in empirical methodology, absent in this domain | **Applied novelty** |
| Learned constraints plus enforcement that is not self-report | ZORO (arXiv 2604.15625) Enrich-Enforce-Evolve enforces by asking the agent to prove compliance. Kiro provides exit-code blocking | **New as combination.** Both halves exist separately |
| Injection triggered by agent output rather than user message | OpenHands has two open issues requesting it | **Recognised gap, unbuilt** |
| Eliminations carried forward with reusability conditions | MemRepair treats failed validation as a first-class write rather than a discarded outcome | **Extension** |
| Parallelism ceiling as oracle throughput | Not found as a framing | **New framing, not a mechanism** |
| Overall Enrich-Enforce-Evolve architecture | ZORO, April 2026 | **Not new** |
| Rule compiler writing its own constraint files | Cline `new_rule`, Gemini CLI `save_memory`, Codex CLI background consolidation | **Not new** |
| Worktree isolation for parallel agents | Commodity. Conductor, Vibe Kanban, Claude Squad, Cursor background agents, and many others | **Not new** |
| Subagent context isolation | Claude Code subagents, OpenHands | **Not new** |
| Three-way restore: code, conversation, or both | Claude Code `/rewind`, plus Summarize from here | **Not new** |
| Non-destructive hiding rather than deletion | OpenCode timestamp-based hiding. OpenHands condenser with a persistent EventLog allowing replay after compression | **Not new** |
| Path-triggered rule loading | Claude Code `.claude/rules` with `paths:` front matter. OpenHands path-triggered rules. Kiro `fileMatch` | **Not new** |
| Stuck-loop detection | OpenHands: five semantic patterns checked every step | **Not new** |
| Two-pass summarisation with self-critique, tail preserved verbatim | Gemini CLI | **Not new** |
| Smaller context window for reliability | Codex caps a million-token model at 272K deliberately | **Not new, and it is a vendor arguing our case** |
| Conflict detection between branches without merging | `git merge-tree`. Available for years, rarely wired into a loop | **Not new, underused** |
| Correlated failure of independently built checkers | Knight and Leveson, 1986 | **Not new, and it is the foundation** |

**Read before publishing any priority claim:** "Compact Constraint Encoding for LLM Code Generation: An Empirical Study of Token Economics and Constraint Compliance" (arXiv 2604.07192). Token economics and constraint compliance together is close to the break-even analysis here, and it surveys the shared-context architectures relevant to claim four.

**Useful theoretical support:** Scofield, January 2026 (arXiv 2601.15077) models agents as enforcing distinct families of validity constraints on a shared state, converging to invariant sets defined by joint constraint satisfaction rather than optimisation of a single objective. Different problem, but it is grounding for why constraint composition works.

### 8.2 Why nobody in the frontier does it this way

The coordination claim should worry anyone reading it. If shared invariants replace messaging, why has no well-funded team shipped it? Five reasons, and none of them is that the idea is wrong.

**It is not new in computer science, only in this domain.** Coordination avoidance via shared invariants is established distributed-systems work. Bailis and colleagues formalised invariant confluence around 2014: if every operation preserves an invariant, concurrent execution and merge preserve it too, and no coordination is required. That is precisely the claim, proved a decade ago for databases. The people building agent orchestrators come from machine learning, not from distributed systems, so the transfer has not happened. **This is a stronger position than invention.** It arrives with theory, terminology, and a body of results.

**The enforcement primitive is very new.** Without a mechanism that blocks an action, a shared constraint is just a shared prompt, and a shared prompt is communication. Exit-code blocking hooks shipped recently and only in some tools. The mechanism did not exist to build on.

**It has a cold-start problem, and vendors cannot ship those.** Constraint coordination works once a corpus has accumulated. Messaging works on day one, in an empty repository, in a demo. A vendor selling to new users cannot ship a feature whose value appears in month three of one codebase. Whoever feels the accumulation problem is a long-lived team, not a first-time user, and that is not who tooling is designed for.

**It is subtractive.** It removes the coordinator instead of making it smarter. Roadmaps add components.

**Messaging demos better.** Agents conferring is legible and impressive. A constraint quietly preventing a conflict that would have happened is invisible, and its value only shows in the counterfactual you cannot display.

None of that makes the claim true. It explains why it would still be available if it were.

---

## 9. The one result that decides the scale

**Do constraints transfer between projects?**

If they are idiosyncratic per repository, this is a good internal tool with a low ceiling. Real, useful, not a product.

If a constraint set for a domain (industrial event integration, ERP idempotency, plant data contracts) transfers across organisations, then the constraint corpus is the asset. It compounds, it is domain knowledge rather than code, and no tooling vendor can ship it because they do not have the domain.

Everything else in the build plan is instrumentation for answering that question honestly.

---

## 10. How to talk about it

**Do not lead with the architecture.** Lead with the diagnostic.

> How many of your agent's rules actually reach the model? Nobody knows, because nobody measures it. Here is the method, and here is what we found when we ran it on ourselves: fifteen out of seventeen.

That is a smaller claim than any competing pitch, it is falsifiable, and it is much harder to argue with.
