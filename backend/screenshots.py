"""Screenshots folder watcher for Quarterdeck.

Watches a configured directory for new image files and makes them available
to the session composer for insertion. When a file appears (or is modified),
it's added to a bounded queue that the frontend polls. The composer can then
offer the file path for insertion into the next message.

The watcher is started lazily when a directory is configured and exists.
It stops when the directory is removed from settings.
"""
import os
import threading
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .config import STATE_DIR, read_settings

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".heic"}
MAX_QUEUE = 20
MAX_RECENT = 10
DEBOUNCE_SECONDS = 5.0

_lock = threading.Lock()
_observer: Observer | None = None
_queue: list[dict] = []
_recent: list[dict] = []  # last N screenshots, never cleared — for hover previews
_last_event: dict[str, float] = {}  # path → last event time (debounce)


class _ScreenshotHandler(FileSystemEventHandler):
    def on_created(self, event):
        self._handle(event.src_path)

    def on_modified(self, event):
        self._handle(event.src_path)

    def _handle(self, path: str):
        if not path:
            return
        p = Path(path)
        if p.is_dir():
            return
        if p.name.startswith('.'):
            return  # skip hidden/temp files (e.g. Monosnap's .Screenshot... temp)
        if p.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        now = time.time()
        with _lock:
            last = _last_event.get(path, 0)
            if now - last < DEBOUNCE_SECONDS:
                return
            # Also check mtime — if the file hasn't changed, don't re-queue it
            try:
                mtime = p.stat().st_mtime if p.exists() else 0
            except OSError:
                mtime = 0
            last_mtime_key = f"{path}:mtime"
            if mtime and _last_event.get(last_mtime_key) == mtime:
                return
            _last_event[path] = now
            if mtime:
                _last_event[last_mtime_key] = mtime
            entry = {
                "path": str(p),
                "name": p.name,
                "size": p.stat().st_size if p.exists() else 0,
                "at": now,
            }
            # Replace any existing entry with the same filename
            _queue[:] = [q for q in _queue if q["name"] != p.name]
            _queue.append(entry)
            while len(_queue) > MAX_QUEUE:
                _queue.pop(0)
            # Also keep in _recent ring buffer (never cleared)
            _recent[:] = [r for r in _recent if r["name"] != p.name]
            _recent.append(entry)
            while len(_recent) > MAX_RECENT:
                _recent.pop(0)


def configured_path() -> str:
    """The watched directory from settings, or empty."""
    return read_settings().get("screenshots_folder", "")


def is_watching() -> bool:
    with _lock:
        return _observer is not None and _observer.is_alive()


def start(path: str = "") -> dict:
    """Start watching the given directory. Returns status."""
    global _observer
    folder = path or configured_path()
    if not folder:
        return {"error": "No screenshots folder configured"}
    p = Path(folder).expanduser()
    if not p.is_dir():
        return {"error": f"Not a directory: {folder}"}

    stop()  # stop any existing watcher first

    with _lock:
        _observer = Observer()
        _observer.schedule(_ScreenshotHandler(), str(p), recursive=False)
        _observer.daemon = True
        _observer.start()
    return {"ok": True, "watching": str(p)}


def stop() -> dict:
    """Stop the watcher."""
    global _observer
    with _lock:
        if _observer and _observer.is_alive():
            _observer.stop()
            _observer.join(timeout=2)
        _observer = None
    return {"ok": True}


def pending() -> list[dict]:
    """Return and clear the queue of new screenshots."""
    with _lock:
        items = list(_queue)
        _queue.clear()
        return items


def recent(n: int = 10) -> list[dict]:
    """Return the last N screenshots (newest last). Never clears."""
    with _lock:
        return list(_recent[-n:])


_files_lock = threading.Lock()
_files_cache = {"folder": "", "at": 0, "items": []}
_files_scanning = False


def _scan_recent_files(folder):
    results = []
    cutoff = time.time() - 24 * 3600
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.name.startswith('.') or Path(entry.name).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                st = entry.stat()
                if st.st_mtime >= cutoff and entry.is_file():
                    results.append({"path": entry.path, "name": entry.name,
                                    "size": st.st_size, "at": st.st_mtime})
            except OSError:
                continue
    return sorted(results, key=lambda item: item['at'])


def recent_files(minutes: int = 5) -> list[dict]:
    """Return cached recent images while one background directory scan refreshes."""
    global _files_scanning
    folder = configured_path()
    if not folder:
        return []
    folder = str(Path(folder).expanduser())
    with _files_lock:
        matching = _files_cache['folder'] == folder
        items = _files_cache['items'] if matching else []
        if not _files_scanning and (not matching or time.monotonic() - _files_cache['at'] >= 5):
            _files_scanning = True

            def refresh():
                global _files_scanning
                try:
                    results = _scan_recent_files(folder)
                    with _files_lock:
                        _files_cache.update(folder=folder, at=time.monotonic(), items=results)
                except OSError:
                    pass
                finally:
                    with _files_lock:
                        _files_scanning = False

            try:
                threading.Thread(target=refresh, daemon=True, name='screenshots-scan').start()
            except BaseException:
                _files_scanning = False
                raise
    cutoff = time.time() - max(1, min(minutes, 1440)) * 60
    return [item for item in items if item['at'] >= cutoff]


def status() -> dict:
    path = configured_path()
    return {
        "configured": bool(path),
        "path": path,
        "watching": is_watching(),
        "pending": len(_queue),
    }
