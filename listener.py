from __future__ import annotations

"""listener.py – standalone UDP listener that writes CSV and pushes samples on a queue.
Run this module directly if you only want to confirm the Simulink data stream -> CSV layer works.

Usage:
    python listener.py  # starts listening based on config.yaml and logs to data_log.csv

Nothing else (webserver / Dash) is started, so you can keep the output window focused on
packet/CSV statistics.
"""

import struct
import time
import collections
import threading
import socket
from queue import Queue
from typing import Dict, Any, Deque
import yaml

CONFIG_FILE = "config.yaml"
LOG_FILE = "data_log.csv"
HEADER_FIELDS = [
    "time",
    "ankle_angle",
] + [f"pressure_{i}" for i in range(1, 9)]

# Queue we expose so that other processes (like the SSE server) can subscribe.
# When you run listener.py directly, nothing consumes the queue – and that is totally fine.
EVENT_Q: Queue = Queue(maxsize=10_000)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def load_config(path: str = CONFIG_FILE) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


def decode_packet(data: bytes, fmt: str, mapping: Dict[str, int]) -> Dict[str, float]:
    values = struct.unpack(fmt, data)
    return {name: values[idx] for name, idx in mapping.items()}


def start_udp_listener(cfg: Dict[str, Any], buffer: Deque[Dict[str, float]]) -> None:
    """Identical to the listener inside app.py – only stripped down for isolation."""
    fmt = cfg["packet"]["format"]
    expected = struct.calcsize(fmt)
    mapping = cfg["signals"]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind((cfg["udp"]["listen_host"], cfg["udp"]["listen_port"]))
    sock.setblocking(True)
    sock.settimeout(1.0)

    print(
        f"[listener] Listening for data on {cfg['udp']['listen_host']}:{cfg['udp']['listen_port']}"
    )

    csv_buffer: collections.deque[str] = collections.deque(maxlen=1000)
    last_flush_wall: float = time.time()

    packets_rcvd = 0
    last_status = time.time()

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(",".join(HEADER_FIELDS) + "\n")

    while True:
        try:
            data, _ = sock.recvfrom(expected)
        except socket.timeout:
            continue

        packets_rcvd += 1
        if len(data) != expected:
            continue

        decoded = decode_packet(data, fmt, mapping)
        sim_t = decoded.get("time", 0.0)
        ankle = decoded.get("ankle_angle", 0.0)

        # push to optional consumer
        try:
            EVENT_Q.put_nowait(decoded)
        except Exception:
            pass

        # CSV logic – log every sample
        row = [f"{sim_t:.4f}", f"{ankle:.4f}"] + [
            f"{decoded.get(f'pressure_{i}', 0.0):.1f}" for i in range(1, 9)
        ]
        csv_buffer.append(",".join(row))

        now_wall = time.time()
        if now_wall - last_flush_wall >= 1.0:
            last_flush_wall = now_wall
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(",".join(HEADER_FIELDS) + "\n")
                f.write("\n".join(csv_buffer))
        if now_wall - last_status >= 10.0:
            last_status = now_wall
            print(
                f"[listener] Received {packets_rcvd} packets – csv rows {len(csv_buffer)}"
            )


# --------------------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config()
    ring_buffer: Deque[Dict[str, float]] = collections.deque(maxlen=5000)
    start_udp_listener(cfg, ring_buffer) 