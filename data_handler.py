from __future__ import annotations

"""Utility classes for efficient data logging and streaming."""

import collections
import threading
import time
from typing import Sequence

HEADER_FIELDS = ["time", "ankle_angle"] + [f"pressure_{i}" for i in range(1, 9)]

class DataLogger:
    """Asynchronous CSV logger with buffering."""

    def __init__(self, path: str, flush_interval: float = 1.0) -> None:
        self.path = path
        self.flush_interval = flush_interval
        self._lock = threading.Lock()
        self._buf: collections.deque[str] = collections.deque()
        self._stop = threading.Event()
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(",".join(HEADER_FIELDS) + "\n")
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def log(self, t: float, ankle: float, pressures: Sequence[float]) -> None:
        row = [f"{t:.4f}", f"{ankle:.4f}"] + [f"{p:.1f}" for p in pressures]
        with self._lock:
            self._buf.append(",".join(row))

    def _worker(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.flush_interval)
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._buf:
                return
            data = "\n".join(self._buf)
            self._buf.clear()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(data + "\n")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self.flush()
