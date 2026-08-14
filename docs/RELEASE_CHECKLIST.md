# Quarterdeck release checklist

Use this for a candidate build, on the Mac that will sign or publish it. A
checked box means the behavior was observed in that build, not inferred from
source.

## Automated baseline

- [ ] Working tree contains only the intended release changes.
- [ ] `python -m pytest tests/ -q` passes from the repository root.
- [ ] `npm --prefix frontend run build` succeeds.
- [ ] The GitHub Actions CI run is green.

## Package and identity

- [ ] `./build-app.sh` produces `dist/Quarterdeck.app`.
- [ ] `defaults read dist/Quarterdeck.app/Contents/Info CFBundleIdentifier`
      reports `com.vidanov.quarterdeck`.
- [ ] The Finder name, Dock name, window title, login page, and in-app heading
      all read **Quarterdeck**.
- [ ] The app icon shows the Quarterdeck `Q` monogram at small and large sizes.
- [ ] Release notes say that the bundle-ID change resets WKWebView-local view,
      theme, filter, and input-history preferences once.

## Upgrade and restart behavior

- [ ] Install over a machine that previously ran Deck.
- [ ] Shared settings and managed-session state remain under `~/.osa-kiro/`.
- [ ] Change a shared setting, quit, relaunch, and verify it survived.
- [ ] Change a per-device view or theme, quit, relaunch, and verify it survived.
- [ ] Double-click the app while it is already open; the existing window comes
      to the front and no error dialog appears.

## Session control

- [ ] Dispatch a session and watch it move from `starting` to `managed`.
- [ ] Send input, queue and reorder a later task, then send the queued task.
- [ ] End and resume a session without losing its transcript.
- [ ] Take over a foreign session and hand a managed session to Terminal.app.
- [ ] Confirm the last assistant message on a waiting card matches the
      transcript.
- [ ] Open `/api/sessions/{id}/messages` and verify stable `seq` values.

## Hooks, gating, and audit

- [ ] Settings reports every editable agent hook as current; reinstall if any
      entry is missing or stale.
- [ ] Start a gated session and cause a harmless tool call such as `pwd`.
- [ ] Allow one held call from the phone and confirm it runs exactly once.
- [ ] Deny another held call and confirm Kiro receives the denial.
- [ ] Turn gating off while a call is held and confirm the call is released.
- [ ] Verify the audit view contains the remote request, human decision, and
      `postToolUse` result with secrets redacted.

## Remote access

- [ ] Start remote serving from Settings and scan the one-time QR code.
- [ ] Reusing the same QR URL fails after the first redemption.
- [ ] Install the LaunchAgent, log out and back in, and verify remote serving
      returns.
- [ ] A token-bearing request from a non-Tailscale address receives HTTP 403.
- [ ] More than 10 dispatches or 60 input submissions in one minute receives
      HTTP 429 with `Retry-After`; ordinary use recovers after the window.
- [ ] The UI reports Tailscale-down and remote-stopped states honestly.
- [ ] On a laptop, verify and document the expected behavior while on battery
      and with the lid closed.

## Demo and publication

- [ ] Record `docs/assets/quarterdeck-phone-approval.gif` from this build: a
      real gated `preToolUse` call answered from a phone.
- [ ] Restore the GIF to the README only after verifying it shows Quarterdeck
      branding and the structural approval flow.
- [ ] Check the README install, remote-access, security, and upgrade instructions
      against a clean environment.
- [ ] Tag only the exact commit used to produce the verified app bundle.
