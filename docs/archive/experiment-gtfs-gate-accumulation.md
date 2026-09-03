# Experiment: does an accumulated constraint corpus reduce recurring failures?

You have no prior context. Everything needed is in this document.

This is a measurement, not a product. The deliverable is a set of numbers plus an honest statement of what they do and do not show. A negative result is a successful outcome and must be reported as clearly as a positive one.

---

## 1. The claim under test

When a coding agent builds the same kind of software repeatedly, some failures recur across independent attempts. The claim is that compiling each failure into a mechanical check, stored on disk and applied on later attempts, reduces recurrence more than putting the same lesson into the prompt as instructions.

Three numbers decide it:

- **Recurrence rate.** How often a given failure class appears across independent attempts with no checks present.
- **Gate yield.** Fraction of accumulated checks that later block a real failure.
- **Transfer rate.** Fraction of checks built on dataset A that block real failures on dataset B.

If recurrence is near zero, there is nothing to accumulate and the idea is dead. If gate yield is near zero, the checks are superstition. If transfer is near zero, the approach is per-project tooling with a low ceiling.

## 2. Why public transport timetables

The task is a journey planner over GTFS (General Transit Feed Specification) data: given origin stop, destination stop, date and time, return an itinerary.

This domain was chosen because its characteristic failures produce **complete, well-formed, wrong answers** rather than crashes. Crashes need no checks, since a crash is already a perfect detector. Silent wrong answers are the target.

Known failure classes, all silent:

| Class | Mechanism |
|---|---|
| Midnight rollover | GTFS permits `stop_times` past `24:00:00` for services that begin the previous service day. Naive time parsing drops or misplaces them. |
| Service exceptions | `calendar_dates.txt` adds or removes service dates, overriding `calendar.txt`. Ignoring it returns trains that do not run. |
| DST transitions | One local day has a repeated hour, another a missing hour. Duration arithmetic breaks. |
| Agency timezones | `agency.txt` may define several timezones in one feed. |
| Transfer feasibility | `transfers.txt` defines minimum transfer times. Ignoring it yields physically impossible connections. |
| Frequency-based trips | `frequencies.txt` defines trips by headway rather than explicit stop times. Feeds mix both styles. |
| Continuous stopping | `continuous_pickup` / `continuous_drop_off` change what counts as a valid boarding point. |

Do not treat this table as complete or as a checklist to hand the agent. It is background for you, the experimenter. The point of the experiment is to discover which classes actually recur, not to confirm this list.

## 3. Data

Obtain **two independent GTFS feeds from different countries**. Both must be real production feeds, not toy fixtures. Requirements:

- Contain `calendar_dates.txt` with actual exceptions
- Contain at least one service crossing midnight
- Ideally contain `transfers.txt`
- Licence permits this use. Record the licence and retrieval date.

Candidate sources to evaluate: national open-data portals, Mobility Database, transitfeeds-style aggregators, individual operator developer pages. Verify licence terms yourself and record what you found.

Feed A is for building. Feed B is untouched until phase 4. **Do not inspect Feed B before phase 4.** Looking at it early contaminates the transfer measurement.

## 4. The oracle

Independent of the planner, and written before any attempts. This is the single most important artifact in the experiment. If it is weak, every number is meaningless.

Given a returned itinerary and the feed, assert:

1. Each leg's arrival is at or after its departure.
2. Each leg departs at or after the previous leg's arrival.
3. Where `transfers.txt` specifies a minimum transfer time between two stops, the gap meets it.
4. Total duration equals the sum of leg durations plus wait times.
5. Every trip used is active on the requested service date, honouring `calendar_dates.txt` exceptions.
6. Every claimed stop sequence exists in `stop_times.txt` in the claimed order.
7. Departure is at or after the requested time.

Rules for the oracle:

- It reads the feed independently. It must not import or call the planner.
- It never inspects planner source code, only its output.
- Each assertion reports separately. "Failed" is not useful; "failed check 5" is.
- Write it once, freeze it, version it. Changing the oracle mid-experiment invalidates comparisons.
- Ship a deliberately broken itinerary set and confirm the oracle catches each defect. An oracle that has never fired is not known to work.

## 5. Protocol

### Phase 1: baseline recurrence

Run **N = 10** independent attempts. Each attempt:

- Fresh agent session, no memory of prior attempts
- Identical prompt every time (write it once, freeze it, include it in the report)
- No checks present on disk
- Same model, same settings throughout
- Fixed budget per attempt: cap wall-clock time, tool calls or tokens. State the cap.

For each attempt record: which oracle checks failed, tokens or wall-clock used, and whether the agent finished within budget.

**Output: recurrence table.** Failure class by attempt number. This table alone determines whether the rest of the experiment is worth running.

**Stop condition.** If no class fails in more than 2 of 10 attempts, stop. Report that recurrence is too low to accumulate against. This is a valid and useful result.

### Phase 2: build the corpus

For each failure class observed in phase 2 or more attempts, write one check. Constraints:

- A check is an executable predicate. It exits non-zero on violation and prints why.
- It examines artifacts (source files, output), not intentions.
- One check, one failure class.
- It records provenance: which attempt produced it, which oracle check failed, the date.
- It must be scoped, naming which files or paths it applies to.

**Write these by hand.** Do not automate generation yet. You need to see what they look like before deciding whether generating them is feasible.

For each check, verify it fires on the attempt that produced it. A check that does not catch its own originating failure is broken.

### Phase 3: measure gate yield

Run **N = 10** fresh attempts on Feed A with the checks present. Identical prompt and settings to phase 1. The only difference is that the checks exist and are enforced.

Record per attempt: oracle failures, which checks fired, cost.

Compute:

- **Gate yield:** for each check, how many of the 10 attempts it blocked a real failure in.
- **False-block rate:** how often a check fired on correct behaviour. Requires reading what was blocked. Do not skip this; it is the cost side and it is what makes the result credible.
- **Residual failures:** oracle checks still failing despite the corpus.

### Phase 4: transfer

Point the unchanged corpus at Feed B. Run **N = 5** attempts.

Record which checks fire, and for each firing whether it caught a real failure or blocked correct behaviour.

**Transfer rate** = checks that blocked a real failure on Feed B, divided by total checks.

Feed B will have its own quirks. Note them, but do not add checks for them. The measurement is whether the existing corpus transfers, not whether you can extend it.

### Phase 5: the comparison arm

The result so far could be explained by the agent simply being told about the failures. Control for it.

Run **N = 10** attempts on Feed A with the checks **replaced by prose**: same content, expressed as instructions in the prompt or a guidance file, with no mechanical enforcement.

Compare oracle failure rates across three arms:

| Arm | Failure rate | Cost |
|---|---|---|
| No help (phase 1) | | |
| Prose instructions (phase 5) | | |
| Enforced checks (phase 3) | | |

**This is the load-bearing comparison of the entire experiment.** If prose performs as well as enforcement, mechanical enforcement is unnecessary complexity and the honest conclusion is to write better prompts.

## 6. Deliverables

1. `oracle/` — the frozen oracle, its self-test, and results of that self-test.
2. `prompt.txt` — the exact frozen prompt.
3. `checks/` — the corpus, each with provenance.
4. `runs/` — raw per-attempt records: oracle output, cost, which checks fired.
5. `results.md` — the report.

`results.md` structure, maximum 800 words:

- Sample sizes and the model used, first.
- The three-arm comparison table.
- Recurrence table.
- Gate yield and false-block rate per check.
- Transfer rate, with the caveat in section 7 stated plainly.
- What could not be measured.
- What a reader should not conclude.

## 7. Limitations to state explicitly

Write these into the report. Do not soften them.

**Small N.** Ten attempts per arm. Differences smaller than roughly 3 of 10 are not distinguishable from noise. Do not report percentages without the raw counts.

**Transfer is measured within one schema.** GTFS to GTFS is the easiest possible transfer case, since both feeds share a specification. A positive result here is weak evidence for transfer across genuinely different domains, and must not be presented as general.

**Single model, single prompt.** Results may not hold for other models, and the frozen prompt may itself induce specific failure modes.

**The oracle bounds everything.** Failures the oracle cannot detect are invisible to every arm equally. List what the oracle does not check.

**Non-determinism.** The same prompt produces different trajectories. That is why N > 1 exists, and it is also why small differences mean nothing.

## 8. Out of scope

- Building a good journey planner. Correctness is measured; quality, performance and interface are not.
- Automating check generation. Later, if phase 3 justifies it.
- Adding checks during phases 3 or 4. The corpus is frozen after phase 2.
- Changing the prompt, model, oracle or budget mid-experiment.
- Recommending an architecture. Report the numbers.

## 9. Anticipated result

Stated in advance so that hindsight cannot reshape it.

The likely outcome is that a small number of classes recur strongly, most notably service-date exceptions and midnight rollover, and that prose instructions capture most of the available benefit. Enforcement is expected to help mainly where the failure is a silent omission rather than a mistake the agent can be reminded about.

If that is what happens, say so. The useful contribution of this experiment is a measurement, not a validated architecture.
