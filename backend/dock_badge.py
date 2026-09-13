"""Optional dock integration, installed only by the native GUI launcher.

Headless/dev servers must never import AppKit or send Apple events to another
Quarterdeck process. Coalesce changes while the GUI queue is busy.
"""
import threading


class DockBadge:
    def __init__(self):
        self._lock = threading.Lock()
        self._schedule = None
        self._apply = None
        self._pending = False
        self._latest = ""
        self._applied = None

    def install(self, schedule, apply):
        with self._lock:
            self._schedule, self._apply = schedule, apply
            self._applied = None
        # Clear a previous launch's tile before the first frontend poll arrives.
        self.set("")

    def set(self, label):
        with self._lock:
            if self._schedule is None:
                return {"ok": True, "skipped": "no native dock"}
            self._latest = label
            if self._pending or label == self._applied:
                return {"ok": True, "unchanged": True}
            self._pending = True
            schedule = self._schedule
        try:
            schedule(self._flush)
        except Exception:
            self._disable()
            return {"ok": True, "skipped": "native dock unavailable"}
        return {"ok": True, "via": "dock-tile"}

    def _disable(self):
        with self._lock:
            self._schedule = None
            self._pending = False

    def _flush(self):
        with self._lock:
            label, apply = self._latest, self._apply
        try:
            apply(label)
        except Exception:
            self._disable()
            return
        with self._lock:
            self._applied = label
            again = self._latest != label
            self._pending = again
            schedule = self._schedule
        if again:
            try:
                schedule(self._flush)
            except Exception:
                self._disable()


badge = DockBadge()
