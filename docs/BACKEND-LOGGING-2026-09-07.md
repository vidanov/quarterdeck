# Backend availability and logging — 2026-09-07

The installed Quarterdeck process (PID 77660) was listening on 127.0.0.1:19418.
Both stdout and stderr were attached to /dev/null, so there was no persistent
native-app backend exception log. Old /tmp development logs did not describe
this launch. The remote proxy's existing rotation support is separate.

During inspection, an unauthenticated request returned HTTP 401 in 0.22 seconds.
Authenticated health/build, sessions, and settings requests then returned 200
in 0.35, 0.39, and 0.48 seconds respectively. No ongoing backend outage was
reproduced. A native stack sample was saved to
/tmp/quarterdeck-hang-20260907.txt. It shows a live event loop and worker threads;
it does not establish the cause of the earlier user-visible unavailability.

## Implemented

The native launcher now initializes backend/runtime_diagnostics.py before its
backend thread. It captures Python stdout/stderr and native file descriptors
in ~/.osa-kiro/logs/runtime.log (runtime-dev.log for DECK_DEV native launches).
It enables fatal-error Python tracebacks and a SIGUSR1 thread-dump handler.
Only send that signal to a build known to have installed the handler.

A wrapper records HTTP response-header latency over 2 seconds and HTTP errors.
It records route templates, not raw query strings, headers, or message bodies.
Existing application print statements and exception tracebacks are also captured.
A daemon checks outstanding requests every 5 seconds; requests awaiting headers
for at least 15 seconds trigger Python thread stacks, at most once per minute.
Streaming connections cease counting once response headers have been sent.
This detects stuck requests, not every possible UI or OS-level freeze.

Logs are private to the user. Rotation checks run every 5 seconds and at launch;
over 8 MiB, the existing in-place rotation retains a 256 KiB tail as runtime.log.1.
The cap can be temporarily exceeded between checks. No new dependencies.

## Verification and deployment

15 tests passed: runtime diagnostics and existing log rotation. Tests verify
persistent stdout/stderr/native output in a separate process, private file
permissions, stack dumps, watchdog rate limiting, streaming exclusions,
exception propagation, and omission of query/header content from request logs.

Command: venv/bin/python -m pytest tests/test_runtime_diagnostics.py tests/test_logs.py -q
Exit: 0.
Diff whitespace check: passed.

The installed app was not rebuilt or restarted. Persistent logging and automatic
request diagnostics require a rebuild and relaunch. The normal ./start.sh dev
server still writes to its terminal; the new log installation belongs to app.py.
