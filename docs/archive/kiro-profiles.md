# Kiro CLI profiles

How Quarterdeck saves and switches kiro-cli logins, where the data actually
lives, and which guarantees the mechanism does not provide.

Implementation: `backend/api.py`, section `--- Kiro CLI profile switching ---`
(`_dump_auth_rows`, `_restore_auth_rows`, `_token_fingerprint`,
`_active_profile_name`, `/api/profiles*`).

## Where the credentials live

kiro-cli keeps its auth in a SQLite database, not in a config file:

```
~/Library/Application Support/kiro-cli/data.sqlite3   (mode 0600)
  auth_kv (key, value)     the OAuth material
  state   (key, value)     everything else, including the active CW profile
```

`auth_kv` holds four rows on an Identity Center login:

| key | contents |
|---|---|
| `kirocli:odic:token` | JSON: `access_token`, `refresh_token`, `expires_at`, `region`, `start_url` |
| `kirocli:odic:device-registration` | client registration for the token refresh |
| `codewhisperer:odic:token` | same shape, legacy/secondary |
| `codewhisperer:odic:device-registration` | as above |

One row in `state` decides which account and therefore which subscription tier
the tokens are spent against:

```
state['api.codewhisperer.profile'] = {"arn": "arn:aws:codewhisperer:eu-central-1:<acct>:profile/<id>",
                                      "profile_name": "QDevProfile-eu-central-1"}
```

Swapping the tokens alone is not enough. Two logins can share one SSO start URL
and differ only in this ARN, which is the case for a paid and a free profile on
the same Identity Center instance.

## What Quarterdeck stores

A "profile" is a snapshot of those `auth_kv` rows plus a metadata sidecar:

```
~/.kiro/profiles/
  <name>.jsonl        one {"key":…,"value":…} JSON object per line, dumped from auth_kv
  <name>.meta.json    email, provider, profile_arn, state_profile, token_fingerprint, saved_at
  _previous.jsonl     auto-written on every switch, holds the outgoing auth_kv
```

Note the location: this is the only Quarterdeck state that does not live under
`~/.osa-kiro/`. It sits next to kiro-cli's own configuration because that is
what it is a copy of.

`token_fingerprint` is `sha256(refresh_token)[:16]`. The refresh token survives
access-token rotation, so the fingerprint stays stable for the life of a login
and distinguishes two Identity Center accounts that share a start URL.

## Operations

| Endpoint | Effect |
|---|---|
| `POST /api/profiles/save` | `SELECT * FROM auth_kv` into `<name>.jsonl`; `kiro-cli whoami` and `state['api.codewhisperer.profile']` into `<name>.meta.json` |
| `POST /api/profiles/switch` | current `auth_kv` to `_previous.jsonl`; `DELETE FROM auth_kv`; insert the saved rows; `INSERT OR REPLACE` the saved `state_profile`; invalidate the active-profile cache; force-refresh the model list |
| `GET /api/profiles/current` | `kiro-cli whoami` plus the resolved active profile name |
| `POST /api/profiles/delete` | unlink both files |

`_active_profile_name()` resolves the active profile by comparing
`state['api.codewhisperer.profile'].arn` against each `meta.profile_arn`, and
falls back to token-fingerprint comparison for profiles saved before ARN
tracking existed.

## What this mechanism does not give you

**Auth is global, not per session.** There is one `auth_kv` table for the whole
machine. A switch rewrites it in place. It cannot reach into a kiro-cli process
that is already running: that process holds the credentials and the CodeWhisperer
profile it resolved at launch.

**The profile badge is a launch-time record, not a live reading.** Dispatch
stores `kiro_profile` and `spawned_at` in the ownership sidecar
(`~/.osa-kiro/owners/<id>.json`). The card renders `◉ <name>` when the label is
still trustworthy and `○ <name>` when a switch has happened since that session
spawned. `○` means: this session was launched under that profile, and the global
auth is no longer on it.

**The model list is global too.** `config.available_models()` shells out to
`kiro-cli chat --list-models`, a fresh subprocess that reads the current global
auth. It answers with the entitlements of whatever profile is active *now*, not
of the profile a given session is running under. Consequence, and this is a real
reported symptom:

> A session dispatched under a paid profile keeps its paid entitlements for its
> whole life, but after a switch to a free profile the model dropdown next to it
> shows the free list. Restarting Quarterdeck does not change this. Nothing is
> stale: the free list is the correct answer for the current global auth. Only
> the running agent is on a different profile, and it will stay there until it
> exits.

To get the paid model list back in the UI, switch the global profile back. To
know what a running session is actually entitled to, look at the model it is
using, not at the dropdown.

**The hardcoded fallback list is indistinguishable from the free list.**
`config.MODELS` currently matches the free entitlement set exactly, so a
`--list-models` failure looks identical to being on the free profile.

## Known defects

1. `_last_profile_switch_at` (`backend/api.py`) is in-memory only. A backend
   restart resets it to `0.0`, and `profile_verified` then reports `True` for
   every session carrying a recorded label, including sessions whose profile was
   switched out from under them before the restart. The `○` warning disappears
   exactly when it is still true. Persisting the last switch timestamp, or
   comparing `spawned_at` against the `_previous.jsonl` mtime, would close this.

2. `<name>.jsonl` is written with the default umask, so mode `0644`, and it
   contains a live OAuth refresh token. kiro-cli keeps the same secret at `0600`.
   The snapshot widens access to a credential. `save_profile` should `chmod 0600`
   after writing, and existing files should be tightened.
