#!/usr/bin/env python3
"""Dash web dashboard for Simulink UDP stream.

Features implemented:
1. Toggle four control signals via buttons, sending a 4‑float packet.
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

import yaml
import socket
import json
from queue import Queue
from flask import Response
from dash_extensions import EventSource
from data_handler import DataLogger

import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objs as go

CONFIG_FILE = "config.yaml"
CONTROL_FMT = "<4f"  # zero, motor, assist, k  (4 × float32 = 16 bytes)
HISTORY = 5000  # number of samples to keep for plotting (increased)
UPDATE_MS = 100  # UI poll interval in milliseconds
N_WINDOW_SEC = 10  # how many seconds of data to show in plots
LOG_FILE = "data_log.csv"

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


def send_control_packet(
    cfg: Dict[str, Any],
    zero: float,
    motor: float = 0.0,
    assist: float = 0.0,
    k_val: float = 0.0,
) -> None:
    """Send a 4-float packet containing the four control signals."""
    payload = struct.pack(CONTROL_FMT, zero, motor, assist, k_val)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(payload, (cfg["udp"]["send_host"], cfg["udp"]["send_port"]))


# --------------------------------------------------------------------------------------
# Background UDP listener (fills a deque with decoded packets)
# --------------------------------------------------------------------------------------


def start_udp_listener(
    cfg: Dict[str, Any], buffer: Deque[Dict[str, float]], logger: DataLogger
) -> None:
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
    sock.settimeout(1.0)  # add right after sock.setblocking(True)
    packets_rcvd = 0  # counter
    print(
        f"Listening for data on {cfg['udp']['listen_host']}:{cfg['udp']['listen_port']}"
    )

    # Data logging helpers
    last_saved_sim: float | None = None  # for 0.01s sim-time throttling

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
        if sim_t is not None and (
            last_saved_sim is None or (sim_t - last_saved_sim) >= 0.01
        ):
            last_saved_sim = sim_t
            pressures = [decoded.get(f"pressure_{i}", 0.0) for i in range(1, 9)]
            logger.log(sim_t, ankle, pressures)

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
        className="dashboard",
        children=[
            html.H2("Simulink UDP Dashboard"),
            html.Div(
                className="controls",
                children=[
                    html.Button("zero (0)", id="zero-btn", n_clicks=0),
                    html.Button("motor (0)", id="motor-btn", n_clicks=0),
                    html.Button("assist (0)", id="assist-btn", n_clicks=0),
                    html.Button("k (0)", id="k-btn", n_clicks=0),
                ],
            ),
            dcc.Store(id="zero-state", data=0),
            dcc.Store(id="motor-state", data=0),
            dcc.Store(id="assist-state", data=0),
            dcc.Store(id="k-state", data=0),
            EventSource(id="es", url="/events"),
            html.Hr(),
            html.Div(
                className="plots",
                children=[
                    dcc.Graph(
                        id="ankle",
                        figure=go.Figure(
                            data=[
                                go.Scatter(x=[], y=[], mode="lines", name="ankle_angle")
                            ],
                            layout=dict(
                                yaxis=dict(range=[-60, 60]),
                                title="Ankle Angle (deg)",
                            ),
                        ),
                        config={"displayModeBar": False, "staticPlot": True},
                    ),
                    dcc.Graph(
                        id="press",
                        figure=go.Figure(
                            data=[
                                go.Scatter(x=[], y=[], mode="lines", name=f"pressure_{i}")
                                for i in range(1, 9)
                            ],
                            layout=dict(
                                yaxis=dict(range=[0, 1000]),
                                title="Pressure",
                                legend=dict(
                                    orientation="h",
                                    yanchor="bottom",
                                    y=1.02,
                                    xanchor="left",
                                    x=0,
                                ),
                                margin=dict(t=60),
                            ),
                        ),
                        config={"displayModeBar": False, "staticPlot": True},
                    ),
                ],
            ),
        ],
    )

    # ------------------------------------------------------------------
    # Callback: Toggle control signals & send packet
    # ------------------------------------------------------------------
    @app.callback(
        Output("zero-btn", "children"),
        Output("motor-btn", "children"),
        Output("assist-btn", "children"),
        Output("k-btn", "children"),
        Output("zero-state", "data"),
        Output("motor-state", "data"),
        Output("assist-state", "data"),
        Output("k-state", "data"),
        Input("zero-btn", "n_clicks"),
        Input("motor-btn", "n_clicks"),
        Input("assist-btn", "n_clicks"),
        Input("k-btn", "n_clicks"),
        State("zero-state", "data"),
        State("motor-state", "data"),
        State("assist-state", "data"),
        State("k-state", "data"),
        prevent_initial_call=True,
    )
    def toggle_signals(
        n_zero: int,
        n_motor: int,
        n_assist: int,
        n_k: int,
        zero_state: int,
        motor_state: int,
        assist_state: int,
        k_state: int,
    ) -> tuple[str, str, str, str, int, int, int, int]:
        ctx = dash.callback_context
        if not ctx.triggered:
            raise dash.exceptions.PreventUpdate
        triggered = ctx.triggered[0]["prop_id"].split(".")[0]

        zero_state = zero_state or 0
        motor_state = motor_state or 0
        assist_state = assist_state or 0
        k_state = k_state or 0

        if triggered == "zero-btn":
            zero_state = 1 - zero_state
        elif triggered == "motor-btn":
            motor_state = 1 - motor_state
        elif triggered == "assist-btn":
            assist_state = 1 - assist_state
        elif triggered == "k-btn":
            k_state = 1 - k_state
        else:
            raise dash.exceptions.PreventUpdate

        send_control_packet(cfg, zero_state, motor_state, assist_state, k_state)

        return (
            f"zero ({zero_state})",
            f"motor ({motor_state})",
            f"assist ({assist_state})",
            f"k ({k_state})",
            zero_state,
            motor_state,
            assist_state,
            k_state,
        )

    # ------------------------------------------------------------------
    # Callback: Update graphs on *each* SSE message (near real-time)
    # ------------------------------------------------------------------
    @app.callback(
        Output("ankle", "extendData"),
        Output("press", "extendData"),
        Input("es", "message"),
        prevent_initial_call=True,
    )
    def push_batch(msg):
        """Handle SSE messages that contain batched samples."""

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

        times = payload.get("t", [])
        ankles = payload.get("ankle", [])
        pressures = payload.get("press", [])

        if not isinstance(times, list):
            times = [times]
        if not isinstance(ankles, list):
            ankles = [ankles]
        if pressures and isinstance(pressures[0], (int, float)):
            pressures = [pressures]

        if len(plot_state["times"]) < 5:
            print(f"push_batch first payload → count={len(times)}")

        ankle_payload = dict(x=[times], y=[ankles])

        # transpose pressures -> 8 traces
        transposed = list(zip(*pressures)) if pressures else [[] for _ in range(8)]
        press_payload = dict(
            x=[times for _ in range(8)],
            y=[list(tr) for tr in transposed],
        )

        # dcc.Graph.extendData expects a tuple of (data, trace_indices, max_points)
        return (
            ankle_payload,
            [0],
            1000,
        ), (
            press_payload,
            list(range(8)),
            1000,
        )

    # ------------------------------------------------------------------
    # SSE endpoint: /events  (one single connection per browser client)
    # ------------------------------------------------------------------
    @app.server.route("/events")
    def sse_stream():  # type: ignore
        print("[SSE] Client connected.")

        def generate():
            # Drop all but the newest 1000 samples so the client isn't flooded
            while event_q.qsize() > 1000:
                try:
                    event_q.get_nowait()
                except Exception:
                    break
            batch: List[Dict[str, float]] = []
            while True:
                item = event_q.get()
                batch.append(item)
                # pull everything that's waiting to minimise messages
                while not event_q.empty() and len(batch) < 50:
                    batch.append(event_q.get())

                payload = {
                    "t": [s["t"] for s in batch],
                    "ankle": [s["ankle"] for s in batch],
                    "press": [s["press"] for s in batch],
                }
                batch.clear()
                yield f"data:{json.dumps(payload)}\n\n"

        return Response(generate(), mimetype="text/event-stream")

    return app


# --------------------------------------------------------------------------------------
# Entry-point
# --------------------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config()

    logger = DataLogger(LOG_FILE)
    data_queue: Deque[Dict[str, float]] = collections.deque(maxlen=HISTORY)

    # Spin up the listener in a daemon thread
    listener_t = threading.Thread(
        target=start_udp_listener,
        args=(cfg, data_queue, logger),
        daemon=True,
    )
    listener_t.start()

    try:
        dash_app = build_dash_app(cfg, data_queue)
        dash_app.run(host="192.168.7.15", port=8050, debug=True, use_reloader=False)
    finally:
        logger.stop()
