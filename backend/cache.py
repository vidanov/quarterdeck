"""A size-capped cache, for the several places that were plain dicts.

Every hot path in Quarterdeck memoises per session id: the 64KB tail of a
`.jsonl`, the last assistant message, a pane capture. Written as bare dicts
they are correct and they never shrink — one entry per session the process has
ever looked at, held for the life of the process. With 558 sessions in the
archive and 64KB of decoded text per tail, browsing the archive alone is
hundreds of megabytes that nothing will ever free.

A cap turns that into a bounded working set. The access pattern is the reason a
plain "clear it when it gets big" would not do: the UI polls the same handful of
live sessions over and over while the archive is read once, so least-recently-
used keeps exactly the entries that are being hit and drops the drive-by reads.

Thread-safe because these are read from request threads and written from the
background refresh thread at the same time.
"""
import threading
from collections import OrderedDict
from typing import Any


class LruCache:
    """Mapping that evicts the least recently used key past `maxsize`.

    Supports the dict operations the call sites already use: `get`, `pop`,
    `[key] = value`, `key in cache`, `len(cache)`. Reads count as uses.
    """

    def __init__(self, maxsize: int = 64) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self.maxsize = maxsize
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key not in self._data:
                return default
            self._data.move_to_end(key)
            return self._data[key]

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            self._data.move_to_end(key)
            return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.pop(key, default)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
