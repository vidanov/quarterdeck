# The Folder Shell

A design, reformulated. Working name only.

Every claim is tagged: **[M]** measured in this project, **[V]** verified in vendor docs or another product, **[D]** derived from something measured, **[S]** speculative and unsupported.

---

## 1. The reframe

The unit of identity is the working folder. Not the session.

Sessions become disposable. A folder has a plan, a rule corpus, a findings ledger, a queue of parked exchanges, and a history of what has been tried. Sessions are spawned against it, do work, report, and die. Nothing durable lives inside a session.

Everything else in this design falls out of that. Token efficiency, because no session accumulates. Learning scope, because the corpus is the folder's. Parallelism, because workers are cheap. Away-mode, because the folder holds state while you are gone.

The single most important consequence: **you never need to protect a session.** Today a long session is an asset you are reluctant to abandon, which is why context grows and why compaction exists. When the folder holds everything durable, ending a session costs nothing.

## 2. Topology

```
              you
               |
        ┌──────────────┐
        │ the shell     │  long-lived, small context, holds the plan
        │ conversation  │  never holds a worker trajectory
        └───────┬───────┘
                │ dispatch (frozen contract)
     ┌──────────┼──────────┬──────────┐
     ▼          ▼          ▼          ▼
  worker     worker     worker     worker    each: own worktree,
   + wt       + wt       + wt       + wt     own session, disposable
     │          │          │          │
     └──────────┴────┬─────┴──────────┘
                     ▼
              report queue          verdict + diff ref + findings
                     │                    never trajectories
                     ▼
        ┌────────────────────────────┐
        │  folder state              │
        │  plan / rules / findings   │
        │  ledger / parked / traces  │
        └────────────────────────────┘
```

**[V]** Constructible today: Kiro exposes `spawn`, `switch`, and `session/list` over ACP, and git worktrees provide isolation.

**[D]** The shell's context stays flat because it never receives tool output. This is compaction achieved by topology rather than by summarisation, and it is strictly better: nothing is lost because nothing entered.

**One rule that protects it.** A worker returns a verdict, a diff reference, and findings. Never its reasoning. Pulling one trajectory into the shell to inspect it makes that context permanent and undoes the design. Inspection happens by opening the worker's log, outside the conversation.

## 3. Five planes

Separated because they have different owners, different failure modes, and different costs.

### Plane 1: Delivery

Getting the right rule into the right context at the right moment.

**[M]** This is where the measured failure is. 23 labelled sessions, 39 eligible opportunities, 17 violations, and in 15 of 17 the rule had never entered context at all. Not ignored. Absent.

**[V]** Kiro already has four inclusion modes: `always`, `fileMatch` on a glob, `auto` routed by description, and `manual` via `#name`. **[V]** But custom agents do not load steering unless it is in the agent's `resources`, `fileMatch` is reported broken in Spec mode, and `fileMatch` never fires for global steering.

**[D]** So the first work is a configuration audit, not a feature. Fix delivery, re-measure, and most of the 15 may disappear. Anything built before that audit is built on an unknown baseline.

Beyond configuration: **[V]** path-triggered rules are deterministic and need no model, so prefer them. **[M]** Zero of six violations were predictable from the initial prompt, so session-start injection cannot be the whole answer. **[V]** OpenHands has the same gap, with open issues requesting triggers on agent output rather than only user messages. Triggering on what the agent does is the differentiator.

### Plane 2: Enforcement

Rules that cannot be skipped.

**[V]** Kiro's `PreToolUse` blocks on exit code 2 and returns STDERR to the model. `PreTaskExec` fires before each spec task, always, and can block. These are the only real enforcement primitives in the space.

**[V]** ZORO, published April 2026, ships the same overall architecture and enforces by asking the agent to prove each rule was followed. That is self-report. An exit code from a check the agent did not write is a different class of evidence, and it is the one thing this design has that theirs does not.

**[M]** Only one rule earned a gate on the evidence: 3 of 5 eligible across three distinct sessions, deterministic trigger. Two others had 2 eligible opportunities each and are unmeasurable. Build the one.

**[D]** Every gate needs a fixture where it must not fire, drawn from a real case. All checks in this project were specified with positive fixtures only, and that gap is what let a broken oracle through.

### Plane 3: Memory

Three lifetimes, one schema.

| Lifetime | Content | Where |
|---|---|---|
| Task | eliminations, dead ends | worker, promoted on exit |
| Folder | findings, plan, gates, parked | folder state |
| Global | cross-project gates | user scope, only if transfer is demonstrated |

Schema is identical at every lifetime: statement, evidence, validity condition.

**[D]** The validity condition is what makes a finding reusable without re-deriving it. A finding with no cheap confirmation is not durable and must not be carried.

**[D]** The durable output of a search is the narrowing, not the answer. Forty eliminations compress to forty lines; the trajectory that produced them is fifty thousand tokens. **[V]** MemRepair is one of the few systems anywhere that treats a failed validation as a first-class write rather than a discarded outcome.

**[S]** Whether folder-level gates transfer to other folders is unmeasured, and it is the single result that decides whether this is a personal tool or a product.

### Plane 4: Orchestration

**[D]** A state machine, not an agent. Everything at runtime is computable: worktree lifecycle, path-overlap rejection from declared paths, pairwise conflict pre-check, oracle dispatch, verdict from exit code, budget accounting, resource-owner queueing, merge ordering.

**[V]** `git merge-tree $(git merge-base A B) A B` detects conflicts between worktrees without merging. Almost nobody wires this into a loop. Running it pairwise on a timer converts merge-time surprise into dispatch-time information.

**[V]** Isolation is commodity and solved. The field's own conclusion is that the hard part is recombination, not isolation, and that worktrees let agents create conflicts between themselves without anyone knowing.

Two model calls only:

1. **Planning, before anything runs.** Slice the work, freeze interfaces, declare paths and side effects, write oracle commands. Small output, predates execution, and it is the artifact everything downstream verifies against.
2. **Exceptions.** A genuine semantic conflict from two clean branches. A discovered issue outside the plan.

**[D]** Diagnostic: every runtime model call is a plan defect. Count them. If the orchestrator pages a model six times per run, planning needs to absorb those cases.

**[D]** The ceiling is oracle throughput, not agent count. Ten agents that cannot verify concurrently are ten agents queued behind one anchor. Raising the ceiling means an oracle instantiable per branch, which is what GreenRoom is for. Everyone else is optimising isolation, which is free.

### Plane 5: Attention

Your attention is the scarcest resource and the only one that does not parallelise.

**Uninterrupted.** Worker completions append to a queue. They never inject into your current turn. **[D]** If completions interrupt, the interruption problem has been rebuilt in a new place.

**Transparent without friction.** Friction is being asked. Visibility is being able to see. Zero of the first, full of the second. One dim line per decision, inline, nothing to dismiss:

```
› what does idempotent mean here
  ...answer...
  ⌇ parked · 2 turns · 1.4k
```

**[D]** No confirmations, no dialogs. Nothing is ever deleted; excision hides by timestamp, restore is one command. **[V]** OpenCode does non-destructive timestamp hiding rather than deletion.

**[D]** One thing is never silent: what carried forward into a fresh session. It determines whether the new session can continue, and a silent failure there presents as the assistant developing amnesia, which is harder to diagnose than any long context.

**Away-mode.** Long runs need out-of-band signals: sound on completion, badge count for the queue, Telegram for anything over a threshold. **[D]** Escalate only on state that needs you: a blocked gate, an exhausted budget, a semantic conflict, an oracle that cannot run. Never on progress. **[D]** A notification that fires when nothing is needed trains you to ignore all of them, including the ones that matter, and that failure is worse than the notification it came from.

## 4. Token economics

Ranked by evidence, which is the honest ordering.

| Mechanism | Evidence | Effect |
|---|---|---|
| Shell never holds trajectories | **[D]** | Structural. The shell cannot grow. |
| Delegation to disposable workers | **[D]** | Each worker starts minimal, dies before it bloats. |
| Findings instead of trajectories | **[D]** | Order-of-magnitude compression on the durable part. |
| Targeted rule delivery | **[M]** | Correction cost averaged ~20k tokens against ~175 to inject. Break-even 1 to 2 percent. |
| Per-exchange excision | **[M]**, weak | 26% flagged, ~40% precision on n=20, interval 19 to 64. Recall unmeasured. Real effect 3 to 11 percent of characters. |

**[D]** Cost is size times turns-persisted, not size. An exchange near the start is re-sent on every later turn; one at the end is barely re-sent. **[D]** Excise from the tail, because removing from the middle invalidates the prompt cache from that point and you re-pay for everything after.

**[V]** Codex caps a million-token model at 272K deliberately, because a larger window delays compaction until the summary itself is unreliable. Smaller for reliability, from a vendor.

## 5. Learning from usage

**[D]** The corrections are the labels. In a spec session, when the agent gets something wrong you say so. That turn is a human judgment of failure recorded at the time by the only person qualified to make it. Nothing else in the log is ground truth.

The loop: correction observed, classified, and if it recurs across sessions, compiled into a gate or a delivery trigger.

**[M]** Classification must be frozen before reading logs: documented before the session, unambiguous read cold, still current. Corrections failing those are style drift, changed requirements, or things never written down. All four look identical to a filter.

**[M]** And the automation must not run ahead of the measurement. Two proxies in this project produced confident numbers and both were invalid: repeated tool calls turned out to be correct re-verification at 421 of 430, and a broken oracle check produced a 4-of-10 false signal that would have seeded a corpus. **[D]** Both were chosen because they were easy to compute rather than because they measured the claim.

**[V]** The compiler is commodity. Cline writes its own rule files, Gemini CLI appends to memory, Codex runs a background consolidation pipeline. Do not position on it.

## 6. Control and tuning

Everything folder-scoped, everything a file, everything diffable.

Tunables worth exposing: tail floor for excision, injection recency window, budget ceilings per phase, gate scope globs, notification thresholds, worker parallelism cap, oracle concurrency cap, which planes are active at all.

**[D]** Defaults must be safe rather than aggressive. Wrong-fresh costs you the thread. Wrong-excise costs one parked exchange. Wrong-block costs trust in every future block.

## 7. Build order

Each stage can end the project honestly.

1. **Delivery audit.** Configuration only. Re-measure. *If this removes most violations, the rest is much smaller and that is the correct result.*
2. **Hand test the topology.** Spawn one worker, keep talking in the shell, confirm the shell's context stays flat. *If it grows, the architecture is wrong and everything above is void.*
3. **One gate.** The rule with 3-of-5 recurrence. With a negative fixture.
4. **Findings ledger.** Statement, evidence, validity condition. Worker exit promotes.
5. **Orchestrator as state machine.** Worktrees, path-overlap rejection, `merge-tree` pre-check. No model.
6. **Shadow the excision decision.** Validate 40 blind, both directions. *If precision stays low, drop the plane.*
7. **Transfer test.** Gates from one folder against another. *This decides whether it is a tool or a product.*
8. **Away-mode.** Last, because it only matters once runs are long and reliable.

## 8. What this is not

Not an agent framework. Not a governance runtime; **[V]** AgentCore Policy commoditised those primitives. Not a rule compiler; three products ship one. Not a supervisor watching every step, because **[M]** none of the four recurring failures would have been caught by better reasoning, and **[D]** an observer reading a worker's self-report inherits the claim it was meant to check.

It is a folder-scoped shell that keeps context small by topology, delivers rules at the moment of use, enforces the few that earned it, remembers narrowings rather than trajectories, and never interrupts.

## 9. Open questions

**[S]** Does the shell's context actually stay flat across a real multi-worker run.
**[S]** Do gates transfer across folders.
**[S]** What excision recall actually is, unmeasured in either direction.
**[S]** Whether `PreTaskExec` fires when a spec runs over ACP rather than in the TUI.
**[M]** Whether the delivery audit alone closes most of the 15 of 17.

The last one is cheapest and comes first.
