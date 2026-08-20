# Quarterdeck — Development Workflow

## Two Modes, Two Ports

| Mode | How to start | Backend port | Frontend | Who uses it |
|---|---|---|---|---|
| **Dev** | `./start.sh` | `19419` | `http://localhost:5173` (Vite HMR) | Active development |
| **Stable app** | Open `/Applications/Quarterdeck.app` | `19418` | Bundled in `.app` | Daily use |

Both run simultaneously without conflict. The installed app is never touched during dev iteration.

---

## Default: always dev in dev mode

**Before writing any code**, confirm dev is running:

```bash
lsof -i :19419 | grep LISTEN   # backend up?
lsof -i :5173  | grep LISTEN   # vite up?
```

If not running:

```bash
cd /Users/a.vidanov/Documents/PROJECTS/PERSONAL/osa-kiro
source venv/bin/activate
./start.sh
```

Then open **http://localhost:5173** in Safari or Chrome. This is your working UI.

---

## Dev iteration loop (frontend changes)

1. Edit `frontend/src/**` files
2. Vite HMR reloads the browser tab automatically — **no restart, no rebuild**
3. Verify the change in the browser at `http://localhost:5173`
4. Done — the stable app at `19418` is untouched

## Dev iteration loop (backend changes)

1. Edit `backend/**/*.py` files
2. uvicorn `--reload` restarts the backend automatically — **no restart**
3. The frontend at `5173` picks up the new backend on next request
4. Verify against `http://localhost:5173`
5. Done — the stable app is untouched

---

## When to rebuild (shipping to stable app)

Only rebuild when:
- A feature is complete and verified in dev mode
- You want it available from the stable `.app` (phone access, menu bar, dock badge)
- You're preparing a commit

**Never rebuild just to test a change.** Test in dev first.

```bash
./build-app.sh --install
# Then: open /Applications/Quarterdeck.app manually
```

The build takes ~2 minutes. It quits the running stable app, replaces the bundle,
and requires a manual relaunch. **Do not interrupt the user's stable session for
a work-in-progress change.**

---

## Freshness check — before claiming any fix is done

### In dev mode
```bash
# Backend auto-reloads — always fresh. Confirm:
curl -s http://127.0.0.1:19419/api/health/build | python3 -c "
import sys,json; d=json.load(sys.stdin); print('stale:', d.get('stale'), '|', d.get('stale_reason',''))"
```

### In stable app mode (19418)
```bash
curl -s http://127.0.0.1:19418/api/health/build | python3 -c "
import sys,json; d=json.load(sys.stdin); print('stale:', d.get('stale'), '|', d.get('stale_reason',''))"
```

A claim is **invalid** when `stale: true` on the port you're testing against.

The stable app header shows an amber "Running build is behind source" banner when
source hash diverges. Treat this as a hard stop — don't present results from a
stale UI. Switch to dev mode instead.

---

## DECK_DEV behavioral differences

When `DECK_DEV=1` (i.e. `./start.sh`):
- Backend runs on port `19419` instead of `19418`
- Remote serving still binds `DEFAULT_PORT` (`19418`) — don't start remote from dev
- The stale-build banner compares against source on disk, not a bundle — always current
- `app.py` (pywebview) is NOT involved — the UI is a plain browser tab

Backend auto-reloads on file save. Frontend HMR fires on file save. No manual steps.

---

## Commit and push workflow

After verifying in dev mode and rebuilding to stable:

```bash
git add <specific files>        # never git add .
git status                      # review what's staged
git commit -m "type: short description

Longer explanation if needed."
```

**Do not push directly to main.** Create a branch and PR, or ask for explicit
permission before pushing main.

```bash
git checkout -b feat/my-feature
git push -u origin feat/my-feature
gh pr create --title "feat: short description" --body "..."
```

---

## Decision table

| Situation | Action |
|---|---|
| Iterating on UI or backend | Work in dev mode (`./start.sh` + `localhost:5173`) |
| Want to verify on phone | Rebuild + install, then start remote serving |
| Feature complete, ready to ship | Rebuild + install + commit |
| Bug in stable app, user is active | Fix in dev, verify, then rebuild at a natural break |
| Stale banner showing in stable app | Switch to `localhost:5173` — do not rebuild mid-session |
| Backend change needed for test | Edit `backend/`, uvicorn auto-reloads, test immediately |

---

## Quick reference

```bash
# Start dev
./start.sh                          # backend :19419, frontend :5173

# Dev frontend URL
open http://localhost:5173

# Verify backend alive (dev)
curl -s http://127.0.0.1:19419/api/sessions | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('sessions',[])), 'sessions')"

# Rebuild stable app (only when ready)
./build-app.sh --install
# Then manually: open /Applications/Quarterdeck.app

# Check stale (dev)
curl -s http://127.0.0.1:19419/api/health/build | python3 -c "import sys,json; d=json.load(sys.stdin); print('stale:', d.get('stale'))"

# Check stale (stable)
curl -s http://127.0.0.1:19418/api/health/build | python3 -c "import sys,json; d=json.load(sys.stdin); print('stale:', d.get('stale'))"
```
