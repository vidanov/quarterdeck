#!/bin/bash
# Build Quarterdeck as a native macOS .app bundle
#   --install   replace /Applications/Quarterdeck.app with the build
set -e
cd "$(dirname "$0")"

INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

echo "🔨 Building Quarterdeck.app..."

# Step 1: Build frontend
echo "  → Building frontend..."
cd frontend
npm run build
# Copy static assets that Vite doesn't include automatically (served under /app/)
cp -f public/icon-32.png dist/icon-32.png 2>/dev/null || true
cd ..

# Step 2: Ensure icon exists
if [ ! -f icon.icns ]; then
    echo "  → Generating app icon..."
    ./generate-icon.sh
fi

# Step 2.5: Write build stamp (git sha, dirty flag, source hash)
echo "  → Writing build stamp..."
python3 - <<'PYSTAMP'
import json, hashlib, subprocess, datetime, os, pathlib

def run(cmd):
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()

git_sha   = run(["git", "rev-parse", "HEAD"])
git_short = run(["git", "rev-parse", "--short", "HEAD"])
git_branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
dirty_out = run(["git", "status", "--porcelain"])
dirty = bool(dirty_out)

# Hash backend/*.py and frontend/src/** content
def source_hash(patterns):
    h = hashlib.sha256()
    files = []
    for p in patterns:
        files += sorted(pathlib.Path(".").glob(p))
    for f in files:
        try:
            h.update(f.read_bytes())
        except Exception:
            pass
    return h.hexdigest()[:16]

stamp = {
    "built_at":      datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "git_sha":       git_sha,
    "git_short":     git_short,
    "git_branch":    git_branch,
    "dirty":         dirty,
    "dirty_files":   dirty_out.splitlines() if dirty else [],
    "source_hashes": {
        "backend":      source_hash(["backend/*.py"]),
        "frontend_src": source_hash(["frontend/src/**/*.jsx", "frontend/src/**/*.js",
                                     "frontend/src/**/*.css"]),
    },
}

stamp_json = json.dumps(stamp, indent=2)

# Write to ~/.osa-kiro/ for the running backend to read
state_dir = pathlib.Path.home() / ".osa-kiro"
state_dir.mkdir(exist_ok=True)
(state_dir / "build-stamp.json").write_text(stamp_json)

# Write into the source tree so PyInstaller bundles it
(pathlib.Path("backend") / "build-stamp.json").write_text(stamp_json)

print(f"     sha={git_short}  dirty={dirty}  backend={stamp['source_hashes']['backend']}  frontend={stamp['source_hashes']['frontend_src']}")
PYSTAMP

# Step 3: Activate venv and run PyInstaller
echo "  → Packaging with PyInstaller..."
source venv/bin/activate
pyinstaller Quarterdeck.spec --noconfirm --clean

# Step 4: Report
APP_PATH="dist/Quarterdeck.app"
if [ -d "$APP_PATH" ]; then
    SIZE=$(du -sh "$APP_PATH" | cut -f1)
    echo ""
    echo "✅ Built: $APP_PATH ($SIZE)"
    echo "   Run:     open $APP_PATH"
    echo "   Install: ./build-app.sh --install   (or: osascript -e 'quit app \"Quarterdeck\"'; rm -rf /Applications/Quarterdeck.app && ditto $APP_PATH /Applications/Quarterdeck.app)"
    echo ""
    echo "   Do not use 'cp -r' into an existing install: it follows symlinks,"
    echo "   and this bundle has them (Python.framework/Versions/Current, the"
    echo "   Resources cross-links), so cp tries to copy a directory onto a"
    echo "   symlink and reports 'Not a directory' for each one. It also leaves"
    echo "   files from the previous build behind. Replace the bundle instead."
    echo ""
    if [ "$INSTALL" = "1" ]; then
        # Quit first: replacing the bundle of a running app leaves it running
        # from a directory that no longer exists.
        echo "  → Quitting a running Quarterdeck, if any..."
        osascript -e 'quit app "Quarterdeck"' 2>/dev/null || true
        sleep 1
        # Replace rather than merge. `ditto` handles the symlinks and the code
        # signature correctly, and the rm makes sure nothing from the previous
        # build survives — a stale `snapshots.json` from an older layout is
        # exactly what turned up in the last install.
        echo "  → Installing to /Applications/Quarterdeck.app..."
        rm -rf /Applications/Quarterdeck.app
        ditto "$APP_PATH" /Applications/Quarterdeck.app
        # Clear the WKWebView JS cache so the new bundle's JS is not masked by
        # a cached version of the old one. Without this, relaunching after an
        # install can show the previous build's behaviour until the cache
        # naturally expires — which does not happen on a fresh open.
        echo "  → Clearing WKWebView cache..."
        for CACHE_DIR in \
            "$HOME/Library/Caches/com.vidanov.quarterdeck" \
            "$HOME/Library/Caches/Quarterdeck" \
            "$HOME/Library/WebKit/com.vidanov.quarterdeck" \
            "$HOME/Library/WebKit/Quarterdeck"; do
            if [ -d "$CACHE_DIR" ]; then
                find "$CACHE_DIR" -name "*.js" -delete 2>/dev/null || true
                find "$CACHE_DIR" -name "*.sqlite*" -delete 2>/dev/null || true
            fi
        done
        # PyInstaller also emits an onedir build next to the bundle, and a copy
        # of *that* in /Applications is a working Quarterdeck with no Info.plist
        # — so it takes port 19418 while having no bundle identity, which means
        # the running-instance lookup cannot see it and the next launch shows a
        # port-clash alert instead of raising the window. Found exactly that.
        if [ -d /Applications/Quarterdeck ] && [ ! -d /Applications/Quarterdeck/Contents ]; then
            echo ""
            echo "⚠️  /Applications/Quarterdeck (no .app) also exists — the onedir"
            echo "   build. It will fight this one for port 19418 and cannot be"
            echo "   brought to the front, because it has no bundle identity."
            echo "   Remove it:  rm -rf /Applications/Quarterdeck"
        fi
        echo "✅ Installed. Remote serving runs in its own process, so restart it"
        echo "   from Settings → Remote access to pick up backend changes."
    fi
    echo "Requires tmux and kiro-cli on PATH (brew install tmux)."
    echo "On first run macOS will ask to allow controlling Finder/Terminal —"
    echo "that is used for the default directory and terminal hand-off only."
    # Refresh the stamp in ~/.osa-kiro/ to reflect the actual HEAD at completion
    # time. The stamp written before PyInstaller may have an older SHA if commits
    # happened during packaging. The running app reads from ~/.osa-kiro/, so this
    # is the authoritative copy.
    python3 - <<'STAMP_FINAL'
import json, hashlib, subprocess, datetime, pathlib
def run(cmd): return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
sha = run(["git","rev-parse","HEAD"])
short = run(["git","rev-parse","--short","HEAD"])
branch = run(["git","rev-parse","--abbrev-ref","HEAD"])
dirty_out = run(["git","status","--porcelain"])
def sh(pats):
    h = hashlib.sha256()
    for p in pats:
        for f in sorted(pathlib.Path(".").glob(p)):
            try: h.update(f.read_bytes())
            except: pass
    return h.hexdigest()[:16]
s = {"built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
     "git_sha": sha, "git_short": short, "git_branch": branch,
     "dirty": bool(dirty_out), "dirty_files": dirty_out.splitlines() if dirty_out else [],
     "source_hashes": {"backend": sh(["backend/*.py"]),
                       "frontend_src": sh(["frontend/src/**/*.jsx","frontend/src/**/*.js","frontend/src/**/*.css"])}}
(pathlib.Path.home()/".osa-kiro"/"build-stamp.json").write_text(json.dumps(s, indent=2))
STAMP_FINAL
else
    echo "❌ Build failed — no .app produced"
    exit 1
fi
