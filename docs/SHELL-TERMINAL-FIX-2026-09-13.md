# Shell startup permission error

Quarterdeck's embedded shell printed `Kiro CLI had an Error!: Operation not
permitted (os error 1)` before the user's normal shell greeting. Reproduced
outside the Codex sandbox with the user's existing Kiro startup integration.

`pty.openpty()` and `Popen(start_new_session=True)` connected terminal stdio
and created a session, but did not assign a controlling terminal. The shell
had no foreground terminal process group. Kiro's startup wrapper failed.

`backend/pty_child.py` now acquires the inherited terminal with `TIOCSCTTY`
and execs the login shell. This runs in a fresh interpreter, avoiding a
Python `preexec_fn` in the threaded backend. The bundled app has a
`--pty-exec` entry point for the same helper.

Regression coverage uses a real PTY and checks foreground terminal ownership,
initial dimensions, resizing, Ctrl-C interruption, and normal exit, through
both the direct helper and app entry point. The user's real Kiro startup
integration also reaches its prompt without the permission error.

Changes apply to newly created shells in dev. The installed stable app needs
a later build to include them; it was not rebuilt for this fix.
