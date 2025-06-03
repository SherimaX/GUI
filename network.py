import struct
import socket
import time
import math
from typing import Dict, Any
from queue import Full

from constants import CONTROL_FMT, SAMPLE_RATE_HZ
from state import event_q
from utils import decode_packet


def send_control_packet(
    cfg: Dict[str, Any],
    zero: float,
    motor: float = 0.0,
    assist: float = 0.0,
    k_val: float = 0.0,
) -> None:
    """Send a 4-float packet containing the four control signals."""
    payload = struct.pack(CONTROL_FMT, zero, motor, assist, k_val)
    host = cfg["tcp"]["host"]
    port = cfg["tcp"]["port"]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            sock.connect((host, port))
            sock.sendall(payload)
        except Exception:
            pass


def start_tcp_client(cfg: Dict[str, Any]) -> None:
    """Listen to the TCP stream and push decoded packets to ``event_q``."""
    fmt = cfg["packet"]["format"]
    expected = struct.calcsize(fmt)
    mapping = cfg["signals"]
    host = cfg["tcp"]["host"]
    port = cfg["tcp"]["port"]

    print(f"Connecting to TCP server {host}:{port}")

    prev_t: float | None = None
    avg_dt: float = 0.0
    count: int = 0

    while True:
        try:
            with socket.create_connection((host, port)) as sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(1.0)
                buffer = b""
                while True:
                    try:
                        chunk = sock.recv(expected - len(buffer))
                        if not chunk:
                            raise ConnectionResetError
                        buffer += chunk
                        if len(buffer) < expected:
                            continue
                        data = buffer[:expected]
                        buffer = buffer[expected:]
                    except socket.timeout:
                        continue

                    decoded = decode_packet(data, fmt, mapping)
                    decoded["timestamp"] = time.time()
                    sim_t = decoded.get("time", decoded.get("Time", 0.0))
                    ankle = decoded.get("ankle_angle", 0.0)
                    torque = decoded.get("actual_torque", 0.0)
                    demand = decoded.get("demand_torque", 0.0)
                    gait = decoded.get("gait_percentage", 0.0)

                    if prev_t is not None:
                        dt = sim_t - prev_t
                        avg_dt = (avg_dt * count + dt) / (count + 1)
                        count += 1
                    prev_t = sim_t

                    sample = {
                        "t": sim_t,
                        "ankle": ankle,
                        "torque": torque,
                        "demand_torque": demand,
                        "gait": gait,
                        "press": [decoded.get(f"pressure_{i}", 0.0) for i in range(1, 9)],
                        "imu": [decoded.get(f"imu_{i}", 0.0) for i in range(1, 13)],
                        "statusword": decoded.get("statusword", 0.0),
                        "avg_dt": avg_dt,
                    }
                    try:
                        event_q.put_nowait(sample)
                    except Full:
                        try:
                            event_q.get_nowait()
                        except Exception:
                            pass
                        try:
                            event_q.put_nowait(sample)
                        except Exception:
                            pass
        except Exception:
            time.sleep(1.0)


def start_fake_data(cfg: Dict[str, Any]) -> None:
    """Generate synthetic samples when the Simulink host is unreachable."""
    print("Simulink host unreachable – using fake data generator")
    t = 0.0
    prev_t: float | None = None
    avg_dt: float = 0.0
    count: int = 0
    dt = 1.0 / SAMPLE_RATE_HZ
    while True:
        ankle = 20.0 * math.sin(t)
        torque = 5.0 * math.sin(t / 2.0)
        demand = 4.0 * math.sin(t / 2.0 + 0.5)
        pressures = [500.0 + 100.0 * math.sin(t + i) for i in range(8)]
        imus = [math.sin(t + i * 0.1) for i in range(12)]
        gait = (t % 1.0) * 100.0

        if prev_t is not None:
            dt_sample = t - prev_t
            avg_dt = (avg_dt * count + dt_sample) / (count + 1)
            count += 1
        prev_t = t

        sample = {
            "t": t,
            "ankle": ankle,
            "torque": torque,
            "demand_torque": demand,
            "gait": gait,
            "press": pressures,
            "imu": imus,
            "statusword": 1591,
            "avg_dt": avg_dt,
        }
        try:
            event_q.put_nowait(sample)
        except Full:
            try:
                event_q.get_nowait()
            except Exception:
                pass
            try:
                event_q.put_nowait(sample)
            except Exception:
                pass

        time.sleep(dt)
        t += dt
