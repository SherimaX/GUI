#!/usr/bin/env python3
"""Dash web dashboard for Simulink UDP stream.

Features implemented in this first cut:
1. Toggle *zero_signal* between 0↔1 via a button. Sends a 4-float control packet.
2. Live chart of `ankle_angle` (y-range −60…+60 deg).
3. Live chart of the 8 plantar pressure signals (shared axis 0…1000 N).

Run:
    python app.py  # then open http://127.0.0.1:8050 in a browser

Make sure Simulink is broadcasting the 112-byte data packets defined in
`config.yaml`. The app listens on the configured port and updates at ~5 Hz.
"""

from __future__ import annotations

import struct
import threading
import time
import collections
from typing import Dict, Any, Deque, List
from itertools import islice

import yaml
import socket
import pandas as pd
import os
import csv
import json
from queue import Queue
from flask import Response
from dash_extensions import EventSource

import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objs as go

CONFIG_FILE = "config.yaml"
CONTROL_FMT = "<4f"  # zero, motor, assist, k  (4 × float32 = 16 bytes)
HISTORY = 5000  # number of samples to keep for plotting (increased)
UPDATE_MS = 100  # UI poll interval in milliseconds
N_WINDOW_SEC = 10  # how many seconds of data to show in plots
LOG_FILE = "data_log.csv"
HEADER_FIELDS = ["time", "ankle_angle"] + [f"pressure_{i}" for i in range(1, 9)]

# Shared state for plotting (producer: UDP listener, consumer: Dash callback)
plot_lock = threading.Lock()
plot_state: Dict[str, Any] = {
    "times": collections.deque(maxlen=1000),
    "ankle": collections.deque(maxlen=1000),
    "pressures": {i: collections.deque(maxlen=1000) for i in range(1, 9)},
}

# Queue for server-sent events (SSE) to push fresh samples to the browser
event_q: Queue = Queue(maxsize=10000)

# --------------------------------------------------------------------------------------
# Config & helpers
# --------------------------------------------------------------------------------------

def load_config(path: str = CONFIG_FILE) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


def decode_packet(data: bytes, fmt: str, mapping: Dict[str, int]) -> Dict[str, float]:
    """Decode *data* (binary) into a dict using *fmt* and *mapping*."""
    values = struct.unpack(fmt, data)
    return {name: values[idx] for name, idx in mapping.items()}


def send_control_packet(cfg: Dict[str, Any], zero: float, motor: float = 0.0, assist: float = 0.0, k_val: float = 0.0) -> None:
    """Send a 4-float packet containing the four control signals."""
    payload = struct.pack(CONTROL_FMT, zero, motor, assist, k_val)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(payload, (cfg["udp"]["send_host"], cfg["udp"]["send_port"]))


# --------------------------------------------------------------------------------------
# Background UDP listener (fills a deque with decoded packets)
# --------------------------------------------------------------------------------------

def start_udp_listener(cfg: Dict[str, Any], buffer: Deque[Dict[str, float]]) -> None:
    fmt = cfg["packet"]["format"]
    expected = struct.calcsize(fmt)
    mapping = cfg["signals"]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Allow quick rebinding if the address was in use (e.g., after a crash)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Some systems (not all Windows versions) support SO_REUSEPORT as well
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass  # ignore if not supported
    sock.bind((cfg["udp"]["listen_host"], cfg["udp"]["listen_port"]))
    sock.setblocking(True)
    sock.settimeout(1.0)           # add right after sock.setblocking(True)
    packets_rcvd = 0               # counter
    print(f"Listening for data on {cfg['udp']['listen_host']}:{cfg['udp']['listen_port']}")

    # Log file is assumed to exist with header (created at startup)

    csv_buffer: collections.deque[str] = collections.deque(maxlen=1000)  # keep last 1000 rows
    last_flush_wall: float = time.time()
    last_saved_sim: float | None = None  # for 0.01s sim-time throttling

    # debug helper
    dbg_last_print = time.time()

    while True:
        try:
            data, _ = sock.recvfrom(expected)
        except socket.timeout:
            continue

        packets_rcvd += 1
        if len(data) != expected:
            continue  # ignore malformed packet
        decoded = decode_packet(data, fmt, mapping)
        decoded["timestamp"] = time.time()
        # Extract fields for logging and plotting
        sim_t = decoded.get("time", decoded.get("Time", 0.0))
        ankle = decoded.get("ankle_angle", 0.0)
        buffer.append(decoded)

        # Update in-memory plots
        with plot_lock:
            plot_state["times"].append(sim_t)
            plot_state["ankle"].append(ankle)
            for i in range(1, 9):
                plot_state["pressures"][i].append(decoded.get(f"pressure_{i}", 0.0))

        # Append to CSV every 0.01 s of simulation time
        if sim_t is not None and (last_saved_sim is None or (sim_t - last_saved_sim) >= 0.01):
            last_saved_sim = sim_t
            row_vals = [
                f"{sim_t:.4f}",
                f"{ankle:.4f}",
            ] + [
                f"{decoded.get(f'pressure_{i}', 0.0):.1f}" for i in range(1, 9)
            ]
            csv_buffer.append(",".join(row_vals))

            # Flush CSV to disk at most once per second to reduce I/O
            now_wall = time.time()
            if now_wall - last_flush_wall >= 1.0:
                last_flush_wall = now_wall
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write(",".join(HEADER_FIELDS) + "\n")
                    f.write("\n".join(csv_buffer))

        # ------------------------------------------------------------------
        # Push latest sample to SSE queue (non-blocking)
        # ------------------------------------------------------------------
        sample = {
            "t": sim_t,
            "ankle": ankle,
            "press": [decoded.get(f"pressure_{i}", 0.0) for i in range(1, 9)],
        }
        try:
            event_q.put_nowait(sample)
            if packets_rcvd % 100 == 0:
                # Every 100 packets give a small hint that data flows.
                print(f"Enqueued {packets_rcvd} samples. Queue size: {event_q.qsize()}")
        except Exception:
            # queue full – drop sample to avoid blocking UDP thread
            pass

# --------------------------------------------------------------------------------------
# Dash application
# --------------------------------------------------------------------------------------

def build_dash_app(cfg: Dict[str, Any], data_buf: Deque[Dict[str, float]]) -> dash.Dash:
    app = dash.Dash(__name__)

    app.layout = html.Div(
        [
            html.H2("Simulink UDP Dashboard"),
            html.Div(
                [
                    html.Button("Toggle zero_signal (0)", id="zero-btn", n_clicks=0, style={"width": "220px"}),
                    dcc.Store(id="zero-state", data=0),
                ]
            ),
            EventSource(id="es", url="/events"),
            html.Hr(),
            dcc.Graph(id="ankle", figure=go.Figure(
                data=[go.Scatter(x=[], y=[], mode="lines", name="ankle_angle")],
                layout=dict(yaxis=dict(range=[-60, 60]), title="Ankle Angle (deg)")
            )),
            dcc.Graph(id="press", figure=go.Figure(
                data=[
                    go.Scatter(x=[], y=[], mode="lines", name=f"pressure_{i}") 
                    for i in range(1, 9)
                ],
                layout=dict(yaxis=dict(range=[0, 1000]), title="Pressure",
                            legend=dict(orientation="h", yanchor="bottom",
                                        y=1.02, xanchor="left", x=0),
                            margin=dict(t=60))
            )),
        ]
    )

    # ------------------------------------------------------------------
    # Callback: Toggle zero_signal & send packet
    # ------------------------------------------------------------------
    @app.callback(
        Output("zero-btn", "children"),
        Output("zero-state", "data"),
        Input("zero-btn", "n_clicks"),
        State("zero-state", "data"),
        prevent_initial_call=True,
    )
    def toggle_zero(n_clicks: int, current_state: int):  # pylint: disable=unused-argument
        next_state = 1 - (current_state or 0)
        send_control_packet(cfg, zero=next_state)
        label = f"Toggle zero_signal ({next_state})"
        return label, next_state

    # ------------------------------------------------------------------
    # Callback: Update graphs on *each* SSE message (near real-time)
    # ------------------------------------------------------------------
    @app.callback(
        Output("ankle", "extendData"),
        Output("press", "extendData"),
        Input("es", "message"),
        prevent_initial_call=True,
    )
    def push_sample(msg):
        """Handle EventSource messages that can be either a raw JSON string
        or a dict with a "data" key containing that string (depending on
        dash-extensions version)."""

        if msg is None:
            raise dash.exceptions.PreventUpdate

        # dash-extensions >=0.1.5 passes the raw string, earlier versions wrap
        # it in a dict under "data".
        if isinstance(msg, str):
            json_str = msg
        elif isinstance(msg, dict) and "data" in msg:
            json_str = msg["data"]
        else:
            # Unexpected format; skip update.
            print(f"Unknown SSE message format: {type(msg)} -> {msg}")
            raise dash.exceptions.PreventUpdate

        try:
            payload = json.loads(json_str)
        except json.JSONDecodeError:
            print(f"Failed to decode JSON from SSE: {json_str[:100]}")
            raise dash.exceptions.PreventUpdate

        t = payload.get("t")
        ankle = payload.get("ankle")
        press = payload.get("press", [])

        # Debug: show first few samples to confirm flow
        if len(plot_state["times"]) < 5:
            print(f"push_sample first payload → t={t} ankle={ankle}")

        ankle_payload = dict(x=[[t]], y=[[ankle]], traceIndices=[0], maxPoints=1000)
        press_payload = dict(
            x=[[t] for _ in range(8)],
            y=[[v] for v in press],
            traceIndices=list(range(8)),
            maxPoints=1000,
        )
        return ankle_payload, press_payload

    # ------------------------------------------------------------------
    # SSE endpoint: /events  (one single connection per browser client)
    # ------------------------------------------------------------------
    @app.server.route("/events")
    def sse_stream():  # type: ignore
        print("[SSE] Client connected.")
        def generate():
            while True:
                data = event_q.get()
                yield f"data:{json.dumps(data)}\n\n"

        return Response(generate(), mimetype="text/event-stream")

    return app


# --------------------------------------------------------------------------------------
# Entry-point
# --------------------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config()

    # Recreate log file with header on each run
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(",".join(HEADER_FIELDS) + "\n")

    data_queue: Deque[Dict[str, float]] = collections.deque(maxlen=HISTORY)

    # Spin up the listener in a daemon thread
    listener_t = threading.Thread(target=start_udp_listener, args=(cfg, data_queue), daemon=True)
    listener_t.start()

    dash_app = build_dash_app(cfg, data_queue)
    dash_app.run(host="192.168.7.15", port=8050, debug=True, use_reloader=False)