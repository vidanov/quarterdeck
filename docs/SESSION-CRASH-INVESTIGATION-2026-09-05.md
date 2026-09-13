# Kiro session termination investigation — 2026-09-05

## Finding and scope

Captain's development source contained a destructive registry cleanup path.
`backend/registry.py:reap_spawn_registry` scanned the shared Kiro session locks,
sent SIGTERM and then SIGKILL to every live PID absent from Captain's private
spawn registry, and deleted the Kiro lock. A terminal or Quarterdeck session
is not necessarily registered with Captain. Captain also records tmux pane
PIDs, which need not match the child Kiro PID in a Kiro lock.

The same function killed registered live processes past their TTL unless an
explicit active-session set protected them. The POST /api/processes/reap route
supplied no protected sessions. The UI invoked it through its Reap button;
no periodic invocation of this registry function was found.

More significantly, test_registry.py called this function with only the Captain
registry redirected to temporary storage. Its default Kiro session directory
remained the real one. Running these tests could therefore terminate live user
sessions. Eight pytest processes were observed running under the Captain backend
at the time of investigation. This establishes an actionable termination path,
not a historical signal trace proving which invocation caused each crash.

Quarterdeck's automatic dead-pane reaper checks tmux pane_dead before removing a
pane. No database failure was established by this investigation. Other Captain
cleanup paths exist (including idle unclaimed worker tmux cleanup and terminal
orphan cleanup); these were not changed or comprehensively validated here.

## Changes applied in the adjacent captain repository

- Registry cleanup only removes records for exited processes or abandoned
  zero-PID spawn intents. It no longer scans/deletes Kiro locks or terminates
  live processes, regardless of age or selected session.
- Retained the existing callable arguments and response keys for compatibility.
- Added automatic Kiro session-directory isolation for backend tests.
- Updated regression tests for unregistered sessions, expired TTL with absent,
  empty, or unrelated active-session sets, and session-directory isolation.
- Changed UI wording to Clean stale records and adjusted its tests.

Quarterdeck application code was not modified. Neither stable app was rebuilt.
The Captain source tree contained extensive pre-existing changes; they were
preserved. No commit or push was made.

## Verification

Commands below were run from the Captain repository unless specified.

| Check | Result |
| --- | --- |
| `venv/bin/python -m pytest backend/tests/test_registry.py backend/tests/test_live_state_isolation.py -q -k 'not dispatch' -p no:cacheprovider` | exit 0; final run 18 passed, 1 deselected |
| `npx vitest run src/__tests__/processRegistry.test.jsx` (frontend directory) | exit 0; 5 passed |
| `make check-serial` (venv activated) | exit 0; 2 files checked |
| `venv/bin/ruff check --no-cache backend/registry.py backend/tests/conftest.py backend/tests/test_registry.py backend/tests/test_live_state_isolation.py` | exit 0; all checks passed |
| `RUFF_NO_CACHE=true make lint` | exit 2; 29 errors elsewhere in the existing tree |
| `git diff --check` | exit 2; existing docs/PLAN.md trailing whitespace |
| Quarterdeck authenticated GET /api/sessions, port 19419 | exit 0; HTTP 200, 19 sessions |
| Captain GET /api/processes, port 19421 | exit 0; 118 records |

Initial dev startup in the restricted sandbox failed to bind ports; authorized
startup succeeded. Initial unauthenticated and Bearer-token Quarterdeck checks
returned 401; the final check used the required X-Local-Token header without
printing credentials. Initial lint attempts failed on sandbox cache writes and
then an invalid RUFF_NO_CACHE value; the final invocation above ran successfully
and reported existing lint failures.

Full `make verify` was deliberately not run: runtime files showed unrelated test
fixture writes, and the broad suite's live-state isolation has not been fully
audited. The focused registry test excludes the dispatch integration test for
that reason. Earlier Captain test processes were no longer present beneath the
observed backend parent on the final process check. No live cleanup endpoint
was invoked to test this fix.

## Concrete affected session and recovery

The user supplied session bd24d7fd-7050-40f0-88fb-f9f7245d8aeb (ping).
Its tmux pane and Kiro terminal wrapper were alive, but the agent child process
and Kiro lock were absent. The terminal displayed "Agent connection closed
unexpectedly" and the transcript was empty. This is consistent with the
registry termination mechanism, but no historical signal attribution was found.

Recovered through the dev reanimate endpoint on the same session ID. The new
agent created its lock. Automatic prompt delivery timed out because pane_status
recognized only an unprefixed composer; the observed Kiro composer starts with
`›  ask a question or describe a task`. Updated that recognition and added tests
for old/new composer forms, recovery delivery, and dropped-link precedence.
After the automatic delivery timeout, sent ping through the input endpoint.
The terminal subsequently showed pong replies and the transcript grew to 2219
bytes. The terminal output included multiple ping turns; this investigation
sent one explicit input request after the automatic retry reported its timeout.

`venv/bin/python -m pytest tests/test_recovery_composer.py tests/test_api.py -q
-k 'recovery or PromptDetection'`: final exit 0, 14 passed, 181 deselected.
Initial regression fixture lacked the actual footer separator and failed one
assertion; corrected it to reflect the observed terminal structure.
`git diff --check -- backend/api.py`: exit 0.
Quarterdeck backend reloaded the change in development; stable app not rebuilt.
