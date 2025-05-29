from __future__ import annotations

"""Utility classes for efficient data logging and streaming."""

import collections
import threading
import time
from typing import Sequence

HEADER_FIELDS = ["time", "ankle_angle"] + [f"pressure_{i}" for i in range(1, 9)]

class DataLogger:
    """Asynchronous CSV logger that keeps only the most recent rows."""

    def __init__(self, path: str, flush_interval: float = 1.0, max_rows: int = 1000) -> None:
        self.path = path
        self.flush_interval = flush_interval
        self._lock = threading.Lock()
        self._buf: collections.deque[str] = collections.deque()
        # Ring buffer to retain the newest ``max_rows`` entries on disk
        self._ring: collections.deque[str] = collections.deque(maxlen=max_rows)
        self._stop = threading.Event()
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(",".join(HEADER_FIELDS) + "\n")
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def log(self, t: float, ankle: float, pressures: Sequence[float]) -> None:
        row = [f"{t:.4f}", f"{ankle:.4f}"] + [f"{p:.1f}" for p in pressures]
        with self._lock:
            line = ",".join(row)
            self._buf.append(line)
            self._ring.append(line)

    def _worker(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.flush_interval)
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._buf and not self._ring:
                return
            # _buf entries are already in _ring via ``log``
            self._buf.clear()
            lines = list(self._ring)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(",".join(HEADER_FIELDS) + "\n")
            if lines:
                f.write("\n".join(lines) + "\n")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self.flush()
