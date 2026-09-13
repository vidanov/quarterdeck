# Profile switching — 2026-09-11

## Findings

Profile switching committed `auth_kv` before separately updating the subscription
in `state`. If the second write failed, the endpoint returned `ok: true` with a
warning despite the mixed login/subscription. It also marked shared-token,
different-subscription changes as unchanged and ran a synchronous model CLI
lookup before replying. Header and Settings held separate identity state; delayed
polls could overwrite a newly selected identity.

## Changes

- Credentials and subscription now commit in one SQLite transaction. Failed
  writes roll back both. Overlapping requests are serialized by SQLite across
  processes and refused promptly by a process-local switch reservation.
- Incomplete or inconsistent snapshots are rejected before changing the login.
  An unchanged selection requires both matching tokens and subscription ARN;
  matching refresh tokens preserve the newer live access token.
- Switch responses carry the committed selection immediately. Saved model
  entitlements seed the cache, avoiding a CLI lookup on the switch path.
- Current-profile reads use the matching snapshot's identity metadata when
  available, avoiding repeated `whoami` processes during routine polling.
- Both selectors share a hook and change events. Old reads cannot overwrite
  switch results; duplicate clicks are blocked and switching progress is shown.
- The dropdown stays open after selection, with an explicit action to apply the
  profile to visible sessions by restarting them. New-session versus existing-
  session behavior is explained in both selectors.

## Verification

84 relevant tests passed, including atomic rollback, no-op token preservation,
shared-token subscription switching, missing metadata, overlap handling, current
identity, backend availability, caches, runtime diagnostics, and frontend imports.
The frontend production build passed. Browser inspection verified the updated
profile menu and apply action with no console errors. Live development sessions,
current-profile, and build-status requests all returned 200; current-profile took
0.023 seconds. Real credentials were not switched for testing; transaction tests
use a temporary SQLite database.

The app bundle is prepared with both fixes after development verification. Installation and restart
remain pending the explicit approval requested after automatic approval review
blocked the earlier deployment. No installation retry has been made.
