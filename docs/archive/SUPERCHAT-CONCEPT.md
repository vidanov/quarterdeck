# SuperChat — Persistent Orchestration App

Status: concept draft, 2026-08-15.

Draws on ideas from nothing-stays-settled-pitch (section 6) and the
constraint-accumulating loop (ROADMAP §14), but is a **separate product**
from Quarterdeck. Quarterdeck observes and gates agent sessions.
SuperChat *is* the session — the one you work in.

---

## 1. The problem, from experience

The productive workflow today is:

1. Open the ROADMAP, pick a task.
2. Use a strong model (paid profile) to formulate a plan with actionable
   todos.
3. Hand the plan to a cheaper model (free profile) for execution.
4. Review results, steer, repeat.

This works but is entirely manual. The handover is a copy-paste between
sessions. The user is the orchestrator, the context bus, and the scheduler.

Quarterdeck shows what's running and lets you gate dangerous actions. But
Quarterdeck is not the thing you work *in* — it's the thing you glance at.
SuperChat is the thing you work in.

## 2. What SuperChat is

A persistent, always-responsive working environment where you:

- Read the ROADMAP and pick (or get proposed) the next task.
- Think through the plan with a strong model — actionable todos with
  acceptance criteria come out.
- Dispatch execution to underlying agent sessions, choosing parallel or
  sequential based on dependency analysis.
- Route each dispatch to the appropriate model tier (strong for planning
  and ambiguous work, cheap for mechanical execution).
- Never wait. The central chat is always available for you to talk, steer,
  ask questions, or change direction while workers run.
- See worker results and attention requests in a side panel, not inline.

Your role: make decisions, test results on your side, provide driving
guidance. The system handles scheduling, handover, model selection, and
progress tracking.

## 2.1 Relationship to Quarterdeck

| | SuperChat | Quarterdeck |
|---|---|---|
| What it is | The working session | The control surface |
| User posture | Active — thinking, steering, deciding | Glancing — monitoring, approving |
| Owns the plan | Yes | No |
| Dispatches work | Yes | Shows dispatched work |
| Model routing | Decides which model | Shows which model |
| Approval gates | Creates the sessions that hit gates | Holds the gates |
| Development of Quarterdeck | Is a task dispatched *through* SuperChat | Is the project being worked on |

SuperChat may *use* Quarterdeck's APIs (to see worker status, to read
approval queues). But it is not a tab inside Quarterdeck. It is the app
you have open all day. Quarterdeck is the dashboard on the second monitor.

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    SUPERCHAT (always responsive)         │
│                                                         │
│  Reads: ROADMAP, steering files, prior decisions        │
│  Does:  plans, proposes, dispatches, reports            │
│  Model: strong (planning requires judgment)             │
│                                                         │
│  ┌─────────────────────────┐  ┌──────────────────────┐ │
│  │   Central Chat Window   │  │   Process Sidebar    │ │
│  │                         │  │                      │ │
│  │  Always your turn.      │  │  Worker A: running   │ │
│  │  Talk, steer, decide.   │  │  Worker B: ⚠ needs   │ │
│  │  Never blocked by       │  │    you (approval)    │ │
│  │  workers.               │  │  Worker C: done ✓    │ │
│  │                         │  │                      │ │
│  └─────────────────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────┘
         │                              │
         │ dispatch                     │ results + attention
         ▼                              ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Worker 1   │  │  Worker 2   │  │  Worker 3   │
│  (cheap)    │  │  (cheap)    │  │  (strong)   │
│  mechanical │  │  mechanical │  │  ambiguous  │
│  task       │  │  task       │  │  task       │
└─────────────┘  └─────────────┘  └─────────────┘
```

## 4. UX layout

### 4.1 Central chat (left/main pane)

The conversation where you think with the system. It is:

- **Never blocked.** Workers do not hold this conversation. A dispatch
  takes one turn ("Dispatched task X to worker"), then you keep talking.
- **Context-flat.** Worker outputs do not enter here. Only verdicts,
  summaries, and diffs arrive. The full trajectory is inspectable in
  the sidebar.
- **Planning-oriented.** The natural language here is "what next," "change
  the approach to X," "why did worker B fail," "skip task 3."

### 4.2 Process sidebar (right pane)

A live panel showing all dispatched work:

- **State per worker:** working, stalled, blocked-on-input, done, failed.
  Derived mechanically (same as nothing-stays-settled 6.1).
- **Attention badge:** when a worker needs the user, it surfaces here with:
  - What was done (summary).
  - Why attention is needed (approval, decision, ambiguity, failure).
  - Suggested action (approve, deny, redirect, retry with guidance).
- **Expandable detail:** click a worker to see its full transcript, tool
  calls, findings. This is the "side chat against frozen context" (10g)
  applied to workers.
- **No interruption rule:** completions append to the queue. They never
  inject into the central chat mid-thought. The user pulls them when ready.

### 4.3 Steering panel (collapsible, below or tabbed)

The steering files, visible and editable in the UI:

- Shows which steering files are active for the current session.
- Editable inline — but locked when a worker is mid-execution against
  that file (you cannot change the contract while someone is building
  to it).
- Edit history visible (what changed, when, what was executing under
  the old version).
- This is the "interface freeze" from nothing-stays-settled made visible.

## 5. Dispatch logic

### 5.1 Parallelism decision

SuperChat decides parallel vs. sequential by:

1. **Path disjointness.** If two tasks touch non-overlapping file globs,
   they can run concurrently.
2. **Interface dependency.** If task B depends on the output of task A
   (a shared type, a new API, a migration), it waits.
3. **Resource contention.** If both need the test database, one port,
   or the same cloud account, they queue.

The user can override: "run these in parallel anyway" or "do these one
at a time."

### 5.2 Model routing

| Task type | Model | Rationale |
|---|---|---|
| Planning, architecture, ambiguous decisions | Strong (paid) | Requires judgment, trade-off reasoning |
| Mechanical implementation (clear spec, tests defined) | Cheap (free) | Follows a plan, does not need to decide |
| Review, verification, gate checking | Cheap or deterministic | Pattern matching, not creative |
| Stuck/looping/failed → escalation | Strong | Diagnosis needs the stronger model |

The app manages this automatically. The user sees which tier a worker is
using and can promote ("use the strong model for this one").

### 5.3 Handover protocol

When SuperChat dispatches to a worker:

```
{
  "task": "Implement the /api/sessions/{id}/compact endpoint",
  "plan": [
    "1. Add route to api.py",
    "2. Send /compact via ACP to the session",
    "3. Return success/failure",
    "4. Add test for the new endpoint"
  ],
  "acceptance_criteria": [
    "Endpoint returns 200 on success",
    "ACP compact command is sent to the correct session",
    "Test passes"
  ],
  "context": {
    "steering_files": ["deployment.md", "PROJECT.md"],
    "relevant_code": ["backend/api.py", "backend/acp_observer.py"],
    "constraints": ["No new dependencies", "Match existing route patterns"]
  },
  "model_tier": "cheap",
  "timeout_minutes": 30
}
```

Worker returns:

```
{
  "status": "done|failed|blocked",
  "summary": "Added /compact endpoint, tests pass",
  "findings": ["ACP requires session to be in 'managed' state"],
  "files_modified": ["backend/api.py", "tests/test_compact.py"],
  "needs_user": false
}
```

## 6. Interaction patterns

### 6.1 Happy path

1. User opens SuperChat. It reads the ROADMAP, proposes the next task.
2. User agrees (or redirects). SuperChat formulates the plan.
3. SuperChat dispatches workers (2 parallel, 1 sequential after them).
4. User continues chatting — asks about an unrelated design question,
   reviews a different file, plans tomorrow's work.
5. Workers complete. Results appear in the sidebar.
6. User clicks Worker A result → reviews diff → approves.
7. User clicks Worker B result → sees a problem → types guidance in the
   sidebar → worker retries with new context.
8. SuperChat proposes the next task.

### 6.2 Attention needed

A worker hits an approval gate or gets confused:

1. Sidebar shows: "⚠ Worker B needs you"
2. User clicks → sees:
   - "Attempted to delete test fixtures. Approval gate held."
   - "Context: was cleaning up after test run, fixture files matched glob."
   - Actions: [Allow] [Deny] [Redirect: "skip cleanup, just run tests"]
3. User chooses. Worker continues.
4. Central chat was never interrupted.

### 6.3 Course correction

User changes their mind mid-execution:

1. "Actually, skip task 3. We don't need that endpoint."
2. SuperChat: cancels Worker C (extracts findings first), updates the plan,
   dispatches the next item.
3. Sidebar shows Worker C: "cancelled (user direction), findings preserved."

## 7. What SuperChat builds vs. what it reads from Quarterdeck

### SuperChat's own capabilities (to build)

| Capability | Notes |
|---|---|
| Plan persistence | Plan lives on disk, survives compaction |
| Model routing (strong/cheap) | Agent config per dispatch |
| Worker result extraction | Parse JSONL tail for verdict |
| Steering panel (view/edit) | View and edit steering files inline |
| Non-blocking sidebar | Worker status + attention queue |
| Interface freeze (lock steering during execution) | Prevent contract changes mid-work |
| Dispatch with handover protocol | Structured task + context + constraints |
| ROADMAP reader | Proposes next task from project roadmap |

### Reads from Quarterdeck (already built)

| Capability | How SuperChat uses it |
|---|---|
| Session status | Worker state (running, blocked, done) |
| Approval gates | Knows when a worker is held |
| Sub-agent visibility | Sees worker-of-worker depth |
| ACP control | Could send commands to sessions |
| Audit trail | What tools ran, what was gated |

SuperChat is not dependent on Quarterdeck. It could dispatch tmux sessions
directly and read `.jsonl` files itself. Quarterdeck's APIs are a
convenience — the observation layer is already there, why rebuild it.
But if Quarterdeck isn't running, SuperChat still works.

## 8. What this is NOT

- **Not a Quarterdeck feature.** It is a separate app. Quarterdeck watches;
  SuperChat works. They are the dashboard and the cockpit.
- **Not a new agent framework.** It is a UI and dispatch layer on top of
  existing kiro-cli sessions.
- **Not a coordinator agent.** The coordination is by constraint (shared
  invariants, path disjointness), not by messaging between workers.
- **Not a replacement for the user.** Decisions, testing, and driving
  guidance stay with the human. The system handles scheduling and handover.
- **Not dependent on a specific vendor's model.** Model routing is a
  preference, not a hard dependency. Works with one model tier too (just
  slower/more expensive).

## 9. Relationship to existing design work

| Existing concept | How it feeds SuperChat |
|---|---|
| nothing-stays-settled 6.1 (never blocked) | Central chat architecture |
| nothing-stays-settled 6.2 (context stays flat) | Workers return verdicts, not trajectories |
| nothing-stays-settled 6.5 (you can leave) | Sidebar queue, no interruption |
| ROADMAP 14 (constraint loop) | Gate registry feeds worker constraints |
| PHASE2-PLAN 3.7 (SuperChat rules) | Ownership model, sidecar-before-prompt |
| Section 13 (ACP driver) | Possible dispatch/control mechanism |
| Quarterdeck session grid | Observation layer SuperChat can read from |
| Quarterdeck approval gates | Hold mechanism for dangerous tool calls |

## 10. Build sequence (sketch)

1. **Layout.** Central chat + sidebar + steering panel. Static mock first.
2. **Dispatch with plan.** SuperChat formulates, dispatches one session,
   shows result in sidebar. No parallelism yet.
3. **Model routing.** Agent configs with model tier. SuperChat picks tier
   per task.
4. **Parallel dispatch.** Path-disjointness check, concurrent workers,
   result queue.
5. **Steering panel.** View/edit, lock-during-execution.
6. **Polish.** Attention escalation, course correction, findings extraction.

## 11. Open questions

1. **Where does SuperChat's own context live?** It is a long-running
   session, so it will compact. Its durable state must live outside the
   context window (plan file, constraint registry, worker results log).
2. **Model for SuperChat itself.** Always strong? Or does it route
   itself to cheap for simple dispatches?
3. **Multiple SuperChats per project?** Or one per project, one per user?
4. **How does "test on your side" work?** User runs tests locally, reports
   back. Does SuperChat poll for test results, or wait for user input?
5. **Phone UX.** The sidebar collapses to a notification list on mobile.
   Approval and steering from phone. Is that enough?
