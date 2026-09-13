# Auto-approval and dock badge investigation — 2026-09-06

The reported symptom was backend loss and repeated badge notifications after
turning on auto-approval. No Quarterdeck/Python crash report was found in the
available macOS DiagnosticReports. Available development logs include historical
tmux timeouts, but do not establish the exact cause of this reported shutdown.

Two concrete failure paths were found and fixed:

- The badge route imported AppKit from the backend request thread even in a
  standalone development server. Its fallback sent Apple events to the installed
  Quarterdeck app. A new thread was started for every changed count, while the
  failure limit was only updated after subprocess completion; multiple blocked
  automation requests could therefore overlap. The fallback is removed. Only
  the GUI launcher registers native badge handling after pywebview initialization.
  Badge requests in headless/dev servers are no-ops. Native updates are combined
  while waiting for the main queue, and a native failure disables updates.
- Pane polling could overlap, and auto-approval sent another trust response after
  1.5 seconds even if its previous response was still pending. It also ignored
  errors in successful HTTP responses. Pane reads now have an in-flight guard;
  automatic responses share a per-session guard across panel remounts and start
  their cooldown on completion. An approval failure removes the saved automatic
  approval setting and shows one error if the panel is still active.

Validation: focused Python badge/startup tests, Node approval concurrency/error
regressions, frontend production build, and authenticated requests to the running
dev backend on port 19419. Repeated badge changes return `no native dock`; the
sessions endpoint returns HTTP 200. Startup tests initially encountered sandbox
socket restrictions and passed when allowed to open temporary local ports.
Native dock rendering and the user's live approval scenario were not exercised.

Existing working-tree edits were preserved. The installed application was not
rebuilt, and no commit or push was made.
