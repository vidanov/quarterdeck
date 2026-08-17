# Handover: profile and model-entitlement mismatch

Date: 2026-08-15
Status: diagnosed, evidence captured, no code changed
Mechanism reference: [kiro-profiles.md](kiro-profiles.md)

Provenance markers: **[VERIFIED]** measured on this machine during the session,
**[OPEN]** must be tested before building on it.

---

## 1. The report

> "Session card shows `○ StormDE_Paid`, it uses the paid profile, but it has the
> free model selection. The restart did not help."

Both halves of that sentence are true at the same time, and the restart could
not have helped. Nothing is stale. The label and the model list are read from
two different places, and only one of them is per session.

---

## 2. What was measured

**[VERIFIED]** The session behind the card is `4720cd06`, cwd
`~/Documents/PROJECTS/PERSONAL/osa-kiro`, running since 08-14 15:11, ownership
sidecar records `kiro_profile: StormDE_Paid`.

**[VERIFIED]** The global auth is on the *free* profile:

| Signal | Value | Matches |
|---|---|---|
| `auth_kv` refresh-token fingerprint | `1011e836577eb799` | `StormDE_Free.jsonl` |
| `state['api.codewhisperer.profile'].arn` | `…:791454908090:profile/7YP79HPVN9YE` | `StormDE_Free.meta.json` |
| `_previous.jsonl` mtime | 06:45:14 today | last switch happened then |

**[VERIFIED]** `kiro-cli chat --list-models` answers with nine models and
`claude-opus-5` is not among them. The running session `4720cd06` is using
`claude-opus-5`. So the process holds paid entitlements while the CLI, asked
fresh, reports free ones.

**[VERIFIED]** `config.MODELS`, the hardcoded fallback, is byte-identical in
content and order to the free entitlement list. A `--list-models` failure is
indistinguishable from being on the free profile.

---

## 3. Root cause

Three separate facts compose into the symptom.

**3.1 Auth is one global mutable row set, not a per-session attribute.**
`auth_kv` and `state['api.codewhisperer.profile']` in
`~/Library/Application Support/kiro-cli/data.sqlite3` are machine-wide. A
Quarterdeck profile switch rewrites them in place. It cannot reach a kiro-cli
process that is already running, which keeps the credentials and CW profile it
resolved at launch. Every switch therefore silently desynchronises every
already-running session from the global state.

**3.2 The badge is a launch-time record.** `POST /api/dispatch` calls
`_active_profile_name()` and writes `kiro_profile` plus `spawned_at` into
`~/.osa-kiro/owners/<id>.json`. It is correct about the past and says nothing
about the present. `SessionGrid.jsx` renders `◉` when the label is considered
verified and `○` when a switch has happened since spawn. The card in the report
shows `○`, so the UI was already telling the truth.

**3.3 The model list has no session dimension.** `GET /api/options` calls
`config.available_models()`, which spawns `kiro-cli chat --list-models` as a
fresh subprocess. That subprocess reads the *current global* auth, so it returns
the entitlements of the active profile, never those of the profile the session in
front of you was launched under. A restart re-reads the same global auth and
produces the same free list, correctly. That is why the restart did nothing.

---

## 4. Two defects found while reading the implementation

**D1. `profile_verified` unverifies itself on restart.**
`_last_profile_switch_at` in `backend/api.py` is a module global initialised to
`0.0`. `_ownership_fields()` computes:

```python
profile_verified = (_last_profile_switch_at == 0.0 or spawn_ts >= _last_profile_switch_at)
```

After a backend restart the timestamp is `0.0` again, so every session carrying a
recorded label reports `profile_verified: true`, including sessions whose profile
was switched out from under them before the restart. The one warning that would
surface defect 3.1 is erased by the restart a user reaches for when confused.

**D2. Credential snapshots are world-readable.**
**[VERIFIED]** `~/.kiro/profiles/*.jsonl` are mode `0644` and contain a live
OAuth refresh token. kiro-cli keeps the same secret at `0600`. `save_profile()`
writes with the default umask and never tightens it.

---

## 5. Fix design

Three tiers. Tier 1 makes the UI stop lying, Tier 2 fixes the reported symptom,
Tier 3 removes the class of bug and is probably not worth its cost.

### Tier 1: report the truth (small, do this first)

**1a. Persist the last-switch timestamp.** No new state file is needed:
`_previous.jsonl` is rewritten on every switch, so its mtime *is* the last-switch
time and it already survives a restart.

```python
def _last_switch_ts() -> float:
    """Wall-clock of the last profile switch, persisted across restarts."""
    try:
        return max(_last_profile_switch_at,
                   _profile_data_path("_previous").stat().st_mtime)
    except OSError:
        return _last_profile_switch_at
```

Then in `_ownership_fields()`, treat a label as verified only when both hold:
the recorded name still equals the live active profile, **and** no switch
happened after the session spawned.

```python
switch_ts = _last_switch_ts()
profile_verified = (recorded_at_dispatch
                    and profile == _cached_active_profile()
                    and (switch_ts == 0.0 or spawn_ts >= switch_ts))
```

The name comparison is the cheap half and catches the case where the mtime is
unavailable. Cost: one `stat()` behind the existing 10-second profile cache.

**1b. Record the ARN, not only the name.** Write `kiro_profile_arn` at dispatch
alongside the name. Names are user-editable and a profile can be deleted and
re-saved; the ARN is what the entitlement actually keys on. Keep the name for
display.

**1c. Move the warning next to the model picker.** The mismatch is *about* model
entitlement, but today it only appears as a glyph on the card badge. The picker
itself is `DetailPanel.jsx:1635`, a plain `<select>` over `options.models`. When
`profile_verified` is false it should carry the same warning: "this list is for the active profile `<active>`; this session
launched under `<recorded>`".

### Tier 2: give the dropdown a session dimension

The blocker is that entitlements can only be queried for the *currently active*
profile. **[VERIFIED]** `_KIRO_HOME=/tmp/... kiro-cli chat --list-models` still
authenticated against the real database and created nothing in the fake home, so
there is no cheap way to ask "what would profile X be entitled to" without
switching to X.

So memoise instead of querying. Record each profile's entitlement list at the
moment that profile is active, which is the only moment the answer is available:

1. In `save_profile()` and at the end of `switch_profile()`, after the forced
   `available_models(force=True)`, write the result into `<name>.meta.json` as
   `models` and `models_refreshed_at`.
2. Add `session_id` (or `profile`) as an optional query parameter to
   `GET /api/options`. When given, resolve the session's recorded profile and
   return `meta["models"]` if present.
3. Return provenance in the payload so the UI can say where the list came from:
   `source: "live" | "profile-cache" | "fallback"`.
4. Fall back to the global live list when a profile has no cached list yet, which
   is the current behaviour, so nothing regresses on first run.

This is self-healing: every switch refreshes the list for the profile being
switched *to*, and the first switch after deploying it populates both profiles.

**1d/2e. Kill the fallback lookalike.** `config.MODELS` matching the free list
exactly means a broken `--list-models` looks like a downgrade. Either return
`source: "fallback"` and have the UI show it, or reduce `MODELS` to `("auto",)`
so a failure is visibly a failure. The comment in `config.py` argues an empty
list is worse than a wrong one, which is right; a *labelled* wrong list is
better than either.

### Tier 3: real per-session isolation (feasible, expensive, not recommended now)

**[VERIFIED]** `HOME=/tmp/x kiro-cli chat --list-models` created
`/tmp/x/Library/Application Support/kiro-cli/data.sqlite3` and `/tmp/x/.kiro/settings/cli.json`,
then prompted for a browser login. So the auth database does follow `HOME`, and a
session could be pinned to a profile by spawning it with a private `HOME` whose
`data.sqlite3` is pre-seeded with that profile's `auth_kv` rows and state ARN.

Why not now: `.kiro/` follows `HOME` too, so agents, steering, hooks, and
`settings/cli.json` all relocate, and `.aws`, `.ssh`, `.gitconfig`, and the shell
profile would each need a symlink into the real home. That is a large blast
radius and a new class of "why did my steering vanish" bug, traded against a
mismatch that Tier 1 and Tier 2 make visible and correct. Record it as the only
true isolation path and leave it.

### Security fix, independent of the tiers

In `save_profile()`, after `data_path.write_text(...)`:

```python
data_path.chmod(0o600)
```

Apply the same to the `_previous.jsonl` write in `switch_profile()`, and tighten
the files that already exist:

```bash
chmod 600 ~/.kiro/profiles/*.jsonl
```

---

## 6. Verification for whoever picks this up

Tier 1 and Tier 2 both need the freshness gate from
`.kiro/steering/deployment.md` before any claim, because both touch the running
`.app`:

```bash
./scripts/verify-build-fresh.sh   # or /api/health/build, stale must be false
```

Functional checks:

1. **D1 regression test.** Dispatch a session, switch profiles, confirm the card
   shows `○`, restart the backend, confirm it *still* shows `○`. That is the exact
   sequence that produced this report.
2. **Tier 2.** With the global profile on free and a session recorded as paid,
   `GET /api/options?session_id=<paid session>` returns a list containing
   `claude-opus-5` and `source: "profile-cache"`, while `GET /api/options` with no
   session still returns the free list and `source: "live"`.
3. **Security.** `stat -f "%Sp" ~/.kiro/profiles/*.jsonl` reports `-rw-------`
   for every file, including one saved after the change.

---

## 7. Workaround until this is built

The dropdown reflects the globally active profile. To get the paid list back in
the UI, switch the global profile back to `StormDE_Paid`. A running session's own
entitlement is unaffected by any switch: to know what it is actually allowed to
use, look at the model it is running, not at the dropdown.
