"""Incremental delivery-history index; archive I/O never runs in HTTP workers."""
import hashlib
import json
import os
import threading
import time
from pathlib import Path


class DeliveryIndex:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.records = {}
        self.offsets = {}
        self.ready = False
        self.stop = threading.Event()
        self.cache = self.root / 'index.json'
        self.thread = threading.Thread(target=self.run, daemon=True, name='delivery-index')

    def start(self):
        self.thread.start()

    def add(self, record):
        sid = record.get('session_id')
        if not isinstance(sid, str):
            return
        with self.lock:
            entry = self.records.setdefault(sid, {'static': {}, 'probes': {}})
            if record.get('method') == 'static_inference':
                if record.get('ts', 0) >= entry['static'].get('ts', 0):
                    entry['static'] = record
            elif record.get('method') == 'probe_echo':
                key = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
                entry['probes'][key] = record

    def get(self, sid):
        with self.lock:
            entry = self.records.get(sid, {})
            return (dict(entry.get('static', {})),
                    list(entry.get('probes', {}).values()), self.ready)

    def load(self):
        try:
            data = json.loads(self.cache.read_text())
            if data.get('version') != 1:
                return
            for entry in data['records'].values():
                self.add(entry['static'])
                for probe in entry['probes'].values():
                    self.add(probe)
            self.offsets = data['offsets']
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.offsets = {}

    def checkpoint(self):
        # Records and offsets are serialized together, so a resumed reader
        # cannot skip archive records absent from its saved index.
        with self.lock:
            body = json.dumps({'version': 1, 'records': self.records, 'offsets': self.offsets})
        temp = self.cache.with_name(f'.index-{os.getpid()}-{id(self)}.tmp')
        try:
            temp.write_text(body)
            temp.replace(self.cache)
        finally:
            temp.unlink(missing_ok=True)

    def scan_file(self, path):
        st = path.stat()
        inode, offset = self.offsets.get(path.name, [st.st_ino, 0])
        if inode != st.st_ino or offset > st.st_size:
            offset = 0
        if offset == st.st_size:
            return
        last_checkpoint = time.monotonic()
        budget = 0
        with path.open('rb') as stream:
            stream.seek(offset)
            while not self.stop.is_set():
                start = stream.tell()
                line = stream.readline(1024 * 1024)
                if not line:
                    break
                if not line.endswith(b'\n'):
                    # A writer may still be appending this final line. Leave
                    # the checkpoint before it so the next pass retries it.
                    break
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        self.add(record)
                except (ValueError, UnicodeError):
                    pass
                self.offsets[path.name] = [st.st_ino, stream.tell()]
                budget += len(line)
                if budget >= 256 * 1024:
                    budget = 0
                    if self.stop.wait(0.02):
                        break  # yield CPU and disk time to interactive requests
                    if time.monotonic() - last_checkpoint >= 5:
                        self.checkpoint()
                        last_checkpoint = time.monotonic()

    def run(self):
        self.load()
        while not self.stop.is_set():
            try:
                # Current records arrive first while older history is imported.
                for path in sorted(self.root.glob('*.jsonl'), reverse=True):
                    self.scan_file(path)
                    if self.stop.is_set():
                        return
                with self.lock:
                    self.ready = True
                self.checkpoint()
            except OSError:
                pass  # retry transient filesystem errors without killing indexing
            self.stop.wait(5)
