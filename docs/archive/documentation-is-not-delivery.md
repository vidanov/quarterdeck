# Documentation Is Not Delivery

I spent a week trying to prove that coding agents need a governance layer. I ended up proving something narrower and more useful: the rules I had written were not reaching the model at the moment it needed them. Not ignored. Absent.

The distinction sounds pedantic. It determines what you build.

## The finding

I labelled 23 of my own agent sessions by hand. For each one I recorded which documented rules were applicable, whether the rule existed in writing before the session started, whether it ever entered the model's context, whether it was violated, and whether I corrected it.

39 eligible opportunities. 17 violations. In 15 of those 17, the rule had never appeared in context at all. Two arrived incidentally, inside a bulk steering block. Zero violations occurred where the model had actively retrieved the rule.

Four rules recurred across separate sessions. Two did not recur at all: read-before-write and ask-before-destructive-action held clean across eight opportunities. So documentation sometimes works unaided. The question is which rules, and the answer is not the one I expected.

Then I widened it. Across 418 sessions from thirty days, roughly 8.6 percent hit a genuine repair loop where the agent kept patching rather than stopping to diagnose. Those sessions ran 6.5 times longer than clean ones. Small population, expensive population.

The cost of one such loop, measured as tokens from the first wrong action through my correction, averaged around twenty thousand. Injecting the relevant rule at the right moment costs under two hundred. That ratio sets a break-even of roughly one to two percent: the injection has to work one time in fifty to pay for itself in tokens alone.

That is the business case. It rests on a rate I have not yet measured, which is whether injection works at all.

## Why the rules were absent

I write rules into steering files. The tool loads them at session start. I assumed loaded meant available.

It does not. A rule present in a ten-thousand-token block at session start is not a rule present at the decision three hours later. And in my case many rules were not even in that block, because the delivery mechanism I trusted was not firing at all in the session type I use.

That is the part worth generalising, and it is not a statement about model quality. It is a statement about plumbing. Every team running production agents has written a rules file. I have not found a published benchmark that checks whether those rules actually arrive.

## Two instruments that failed

Before I labelled anything by hand, I tried twice to get the answer cheaply. Both attempts failed, and the way they failed is the more transferable result.

First attempt: count repeated identical tool calls in session logs. If an agent repeats itself, the reasoning went, it has lost track. The data looked strong. Repeat rate climbed from 1 percent in the shortest quartile of sessions to 45 percent in the longest. Within each session, repeats clustered four times more heavily in the second half than the first.

Then I checked whether anything had changed between each pair of identical calls. 421 of 430 pairs had a mutation in between. The agent was re-running a check after modifying something, which is correct behaviour. Verification clusters at the end of a session because that is when you converge. Two percent were genuinely wasted, and all of those fired immediately with nothing intervening, which points at a double-submission artifact rather than forgetting.

The metric measured verification and I had read it as degradation.

Second attempt: build a journey planner over public transport timetable data, run ten independent attempts, count which failures recurred. Timetable logic is dense in defects that produce complete, plausible, wrong answers, which is exactly the category worth catching.

Four of the first eight attempts failed one check. The failure looked consistent. It was the check that was wrong: it applied minimum transfer times to legs where the passenger never changes trains. After the fix, zero of ten failed. The experiment had been measuring my own bug.

Both instruments shared one property. I chose them because they were easy to compute, not because they measured the claim. Repeated tool calls grep cleanly out of a log. A transfer-time assertion is five lines. Neither was chosen because it tested the hypothesis.

## The check that failed

The timetable case came close to producing a result rather than a null.

Had the broken check gone unexamined, the next phase would have compiled a constraint forcing agents into the representation the broken check accepted. Compliance would have risen. The measured yield would have looked strong. The gate and the checker would have been wrong in the same direction, and their agreement would have been reported as proof.

This is the correlated-failure result from 1986, arriving in a five-line Python assertion instead of redundant flight software. Knight and Leveson showed that independently developed programs written to the same specification fail together, because the correlation lives in the specification rather than in the implementations. Redundancy does not buy independence when the premise is shared.

Which gives the operational rule I did not have before: independence is a property of provenance, not of configuration. You cannot make a checker independent by running it in a separate process, on a different model, from another vendor. You make it independent by requiring it to derive from something the thing being checked did not produce and does not interpret. External state. An artifact that predates the work. A human who holds different assumptions.

For failures that live at the level of a shared premise, there is no cheap decorrelation. Where the check cannot read external state or predate the artifact, the arrangement is symmetric and the second layer adds cost without adding evidence.

The practical version is smaller and immediately actionable: every check needs a fixture where it must not fire, drawn from a case that actually occurred. I had written only fixtures where checks must fire. That gap is what let the broken assertion through.

## What is already built

The concept is crowded. I found that out after the measurement rather than before. Check prior art before you build, not after you've measured.

ZORO, published in April, is the same architecture: rules files are passive, so anchor them to every step, enrich the plan with relevant rules, enforce during implementation, evolve the ruleset from user feedback. Their evaluation shows agents follow rules more reliably with it than without.

The compiler that turns corrections into persistent rules already ships in three products. Cline writes its own rule files. Gemini CLI appends to its memory file. Codex CLI runs a background pipeline that consolidates memories without the model deciding what to keep.

The benchmarks exist too. MemoryCode tests whether an agent can retrieve and act on instructions across sessions with distractors present. SWE-Bench-CL restructures a standard benchmark into chronological per-repository streams with forgetting and transfer metrics. Both measure what I was preparing to measure by hand.

One thing I would keep from my own version. ZORO enforces by requiring the agent to prove each rule was followed. That is self-report, and self-report is exactly the anchor that fails the provenance test above. A non-zero exit code from a check the agent did not write is a different class of evidence.

## What is scarce

Nobody in that literature measures whether a specific tool actually delivers its rules. That is the thing I did, and the thing that turned out to matter.

The method transfers even though my numbers do not:

Label your corrections from your own logs, blind to the outcome. Establish a denominator, because a violation count without eligible opportunities is unfalsifiable in either direction. Record whether the rule was ever in context, by two mechanical definitions: it appeared in the assembled prompt, or the trace shows the file being opened. Never infer reading from the answer. Compute break-even against your real correction cost, not an assumed one.

The output is per-tool and per-version. Mine will be wrong the moment the vendor fixes the delivery path. The procedure will not be.

That is the smaller claim, and it is much harder to knock down. Not here is how to govern your agents. Here is how to find out whether your agents ever received the instructions you wrote, and here is what I found in mine.

Fifteen out of seventeen.
