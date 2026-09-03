# Task: Analyze Kiro CLI session logs for context-length effects

You have no prior context on this project. Everything you need is in this document. Do not infer intent beyond what is written here.

## Background

Kiro CLI is a command-line coding agent. Each session is written to disk as a JSON Lines file, one JSON object per line, in `~/.kiro/sessions/cli/`. There is also a `.json` file per session holding state. You care about the `.jsonl` event logs.

We want to know whether long sessions perform worse than short ones, and if so, whether the degradation tracks context length rather than task difficulty. This is a retrospective analysis of existing logs. There is nothing to fix and no code to change in the agent itself.

## Hard rules

1. **Do not assume the schema.** The field names below are guesses. Discover the real structure before computing anything.
2. **Do not fabricate a metric you cannot compute.** If the data does not support one, say so explicitly and move on. A short honest report beats a complete-looking one.
3. **Read-only.** Never modify or delete anything in `~/.kiro/`. Copy files to a working directory if needed.
4. **Report sample sizes with every number.** A percentage over 4 sessions is not a finding.

## Step 1: Discover the schema

Before any analysis:

- Count the files and their date range.
- Print the first 3 lines of the largest file, pretty-printed.
- Print the first 3 lines of a small file.
- Enumerate every distinct top-level key across all lines, with occurrence counts.
- For any field that looks like a type or event discriminator, enumerate its distinct values with counts.

Write this to `schema.md` before proceeding. If the structure differs materially from what the metrics below assume, adapt the metrics and note what you changed.

## Step 2: Metrics

Compute what the data supports. Definitions are precise on purpose.

### M1. Context growth curve

For each session, per turn in order: cumulative character count of all message content up to and including that turn. Use characters if token counts are not present in the data. If a token count field exists, use it and say so.

Output: one row per (session_id, turn_index, cumulative_size, turn_size).

### M2. Repeated identical actions

A **tool call** is any event representing the agent invoking a tool. Two tool calls are **identical** if the tool name and the full arguments match exactly after JSON normalization (sorted keys, no whitespace).

For each session, count groups of 2 or more identical tool calls. Record the tool name, the repeat count, and the turn indices where each occurrence happened.

This is the signal of interest: an agent repeating itself exactly is not making progress.

### M3. Session outcome

Classify each session into exactly one of:

- **completed** — the log ends after an assistant message with no pending tool call and no error
- **compacted** — the log contains a context-compaction event at any point
- **errored** — the log ends with an error event
- **abandoned** — the log ends immediately after a user message or mid-tool-call, with no assistant response

If the data cannot distinguish these, define the categories you *can* distinguish and use those. State the mapping you used.

### M4. Correlation

Cross-tabulate M3 outcome against session size, bucketing sessions by total cumulative size into quartiles. Report counts per cell, not just percentages.

Then: for sessions containing repeated identical actions (M2), report the cumulative context size at the point where the first repeat occurred. Compare against the median session size.

**The question this answers:** do repeats cluster at high context sizes, or are they spread evenly? Do not overstate. Correlation here is weak evidence and the sample is small.

## Step 3: Deliverables

Write to the output directory:

1. `schema.md` — findings from Step 1.
2. `sessions.csv` — one row per session: session_id, file, start time if available, turn count, total size, outcome, repeat_group_count, size_at_first_repeat.
3. `turns.csv` — one row per turn: session_id, turn_index, turn_size, cumulative_size, is_tool_call, tool_name.
4. `findings.md` — the analysis. Structure it as: what the data is, what each metric showed with sample sizes, what could not be computed and why, and what a reader should not conclude from it.

Keep `findings.md` under 400 words. Lead with the sample size and the single clearest result. If there is no clear result, say that first.

## Step 4: What not to do

- Do not recommend architecture changes. Not in scope.
- Do not summarize the content of the sessions. The subject is their shape, not their topic.
- Do not chart anything until the CSVs are correct. If you produce a chart, it must be reproducible from the CSVs alone.
- Do not report a correlation as causal. Long sessions may simply be hard tasks. Note this limitation in `findings.md` explicitly.

## Known limitation to state in your report

These logs cannot answer the underlying question, which is whether restarting with a short summary would have done better. There is no counterfactual on disk. This analysis establishes only what long sessions cost, not what an alternative would have saved. Write that sentence, or your own version of it, into `findings.md`.
