# Quarterdeck deployment note

When running Quarterdeck from /Applications/Quarterdeck.app:
- Frontend changes are NOT visible until the app bundle is rebuilt
- Use: `./build-app.sh --install` to rebuild and reinstall
- This quits the running app, replaces the bundle, then relaunch manually from Applications
- `frontend/dist/` changes only affect `start.sh` dev mode (port 5173/19419)
- Backend (api.py) changes also require a full rebuild when using the .app

For dev iteration: use `./start.sh` and open http://localhost:5173 instead.

## Freshness check — required before claiming any fix is done

Before claiming any Quarterdeck bug or feature is complete, run:

```bash
curl -s http://127.0.0.1:19418/api/health/build | python3 -c "import sys,json; d=json.load(sys.stdin); print('stale:', d['stale'], '|', d.get('stale_reason',''))"
```

Or use the hook script:

```bash
./scripts/verify-build-fresh.sh
```

A claim is **invalid** when `stale: true`. The only valid completion evidence is:
1. `stale: false` from `/api/health/build`
2. The functional verification that proves the fix works

Rationale: three instances of the same bug were claimed fixed while the running
app still carried the old code. The source files had been edited but
`./build-app.sh --install` had not been run. Nothing in the system could
surface this. This check closes that gap.

### When the banner appears in the UI

The Quarterdeck header shows an amber "Running build is behind source" bar
whenever the running process's source hash diverges from what's on disk.
This fires within 30 seconds of an edit. Treat it as a hard stop — do not
present results from a UI that is showing this banner.
