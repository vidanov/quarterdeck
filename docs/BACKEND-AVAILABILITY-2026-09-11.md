# Backend stalls — 2026-09-11

## Evidence

The installed app's runtime.log contains repeated long requests and thread dumps
on September 11. At 17:10, multiple HTTP worker threads and the background
refresher were simultaneously inside `_do_sessions_scan`. Duration requests took
90–92 seconds. Session requests failed with a tmux timeout and with
FileNotFoundError when a session JSON disappeared during sorting. The server's
event loop was still alive in the captured stacks; these establish overload and
request failures, not a fatal process crash.

## Changes

- Background refreshes, stale polls, and cold polls share one nonblocking scan
  reservation. Stale polls return the last successful snapshot while one refresh
  runs. Concurrent cold polls receive HTTP 503 with Retry-After rather than
  occupying more workers or displaying an empty session grid.
- Invalidation has a generation counter so a scan started before a mutation
  cannot republish the invalidated snapshot. Failures release the reservation
  and preserve the previous successful snapshot for later retries.
- Session sorting tolerates files disappearing during deletion.
- The build-status endpoint runs its blocking work in a worker thread rather
  than the event loop. Its three Git subprocess calls each have a 3-second limit.

A stalled underlying filesystem or tmux call can still delay the single scan;
the dashboard retains its last snapshot during refresh. This change prevents
polling from multiplying the scan load. Other expensive endpoints, including
screenshot enumeration and duration aggregation, remain possible follow-up work.

## Verification

- Seven new regression tests cover 60 overlapping stale polls, concurrent cold
  polls, recovery after a failed scan, invalidation during a scan, directory
  isolation, deletion during sorting, and another HTTP request completing while
  the build check is blocked in Git.
- Availability, API, cache, diagnostics, and JSON response checks: 207 passed,
  2 skipped, 8 failed. All eight failures reproduce using the exact pre-change
  api.py in a separate test process. They concern old dock-badge tests and
  sandbox-dependent shell/deletion/collection tests.
- Live development backend: 29 sessions returned in 0.050 seconds. All 36
  concurrent authenticated session requests returned 200; maximum 0.476 seconds.
  Build-status returned 200 in 0.065 seconds with dev_mode=true.
- Diff whitespace check passed.

## Packaging and rollout

`./build-app.sh` completed successfully after development verification. The
fixed bundle is at `dist/Quarterdeck.app`.

Installation was not executed: automatic approval review rejected replacing
`/Applications/Quarterdeck.app` and quitting/restarting the running application
without explicit user approval for that service interruption. The installed app
remains unchanged. The proposed install stages the new bundle, keeps the old
bundle for rollback, and preserves WebKit preferences and session state.

## Follow-up outage: delivery history and screenshots

At 17:35 the running app still had HTTP workers scanning delivery JSONL and
screenshot folders, with delivery requests taking 88 seconds. The delivery
archive had grown to 6.58 GiB because identical static-inference records were
appended on every session poll. Each delivery read parsed the entire archive.
Unbounded screenshot fetches every five seconds could also occupy the browser's
connection slots while old requests remained pending.

Implemented an incremental delivery index with resumable file offsets and an
atomic JSON checkpoint. HTTP reads use memory; historical import streams in a
background thread and yields between chunks. New observations update the index
immediately, unchanged static records are deduplicated, and an empty workspace
no longer scans the process working directory. Historical logs are preserved.
Screenshot polling now serves a cached list while at most one directory scan
runs; the frontend waits for each request and aborts on timeout/unmount.
Transient refresh and timeout messages are distinguished from unreachable errors.

Verification: 91 relevant tests passed and the frontend production build passed.
36 concurrent mixed development reads (sessions, screenshots, delivery) all
returned 200; maximum 0.432 seconds. The historical checkpoint reached 6.58 GiB
indexed with 2,164 session summaries.

The installed bundle's backend and frontend source hashes match the current
source, and it contains backend.delivery_index. However, the running native
process (PID 90552, startup logged at 17:51:03) still shows the OLD archive-reading
get_session_delivery and synchronous screenshot scan in its thread stacks.
The bundle was replaced while that process was running. A bounded installed-app
check was stopped after the first screenshot/delivery timeouts; it does not
validate the new code in production. Quit/relaunch is required before repeating
installed-backend verification. Explicit restart approval remains pending.

## Restart and installed verification completed

With explicit user approval, Quarterdeck was quit and relaunched from
`/Applications/Quarterdeck.app` at 17:54:48 (new PID 98663). Delivery responses now
include `history_loading: false`, confirming the new indexed implementation is
running and historical import is complete.

A 30-round installed-backend check made 90 mixed session, screenshot, and
delivery requests: all returned 200, zero errors, maximum 0.016 seconds.
Build-status returned 200 in 0.075 seconds with bundle_mode=true and fresh uptime;
current-profile returned 200 in 0.004 seconds. This verifies recovery and
short-term repeated request handling, not a guarantee of indefinite uptime.
