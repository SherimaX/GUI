#!/usr/bin/env python3
"""Dash web dashboard for Simulink UDP stream.

Features implemented:
1. Toggle four control signals via buttons, sending a 4‑float packet.
2. Live chart of `ankle_angle` (y-range −60…+60 deg).
3. Live chart of the 8 plantar pressure signals (shared axis 0…1000 N).
4. Live chart of actual torque and the first 3 IMU channels.

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
import platform
import subprocess
import math

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

# Consistent colors for pressure/IMU traces
COLOR_CYCLE = [
    "#0B74FF",
    "#12C37E",
    "#FF7F0E",
    "#D62728",
    "#9467BD",
    "#8C564B",
    "#E377C2",
    "#7F7F7F",
]

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
    "torque": collections.deque(maxlen=1000),
    "pressures": {i: collections.deque(maxlen=1000) for i in range(1, 9)},
    "imus": {i: collections.deque(maxlen=1000) for i in range(1, 13)},
}

# Queue for server-sent events (SSE) to push fresh samples to the browser
event_q: Queue = Queue(maxsize=10000)

# Limit concurrent SSE clients
MAX_CLIENTS = 5
_active_clients = 0
_client_lock = threading.Lock()

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


def is_host_reachable(host: str) -> bool:
    """Return True if *host* responds to a single ping."""
    param = "-n" if platform.system().lower().startswith("win") else "-c"
    try:
        result = subprocess.run(
            ["ping", param, "1", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


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
        torque = decoded.get("actual_torque", 0.0)
        buffer.append(decoded)

        # Update in-memory plots
        with plot_lock:
            plot_state["times"].append(sim_t)
            plot_state["ankle"].append(ankle)
            plot_state["torque"].append(torque)
            for i in range(1, 9):
                plot_state["pressures"][i].append(decoded.get(f"pressure_{i}", 0.0))
            for i in range(1, 13):
                plot_state["imus"][i].append(decoded.get(f"imu_{i}", 0.0))

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
            "torque": torque,
            "press": [decoded.get(f"pressure_{i}", 0.0) for i in range(1, 9)],
            "imu": [decoded.get(f"imu_{i}", 0.0) for i in range(1, 13)],
        }
        try:
            event_q.put_nowait(sample)
            if packets_rcvd % 100 == 0:
                # Every 100 packets give a small hint that data flows.
                print(f"Enqueued {packets_rcvd} samples. Queue size: {event_q.qsize()}")
        except Exception:
            # queue full – drop sample to avoid blocking UDP thread
            pass


def start_fake_data(
    cfg: Dict[str, Any], buffer: Deque[Dict[str, float]], logger: DataLogger
) -> None:
    """Generate synthetic samples when the Simulink host is unreachable."""
    print("Simulink host unreachable – using fake data generator")
    t = 0.0
    dt = 0.01
    while True:
        ankle = 20.0 * math.sin(t)
        torque = 5.0 * math.sin(t / 2.0)
        pressures = [500.0 + 100.0 * math.sin(t + i) for i in range(8)]
        imus = [math.sin(t + i * 0.1) for i in range(12)]

        sample = {
            "time": t,
            "ankle_angle": ankle,
            "actual_torque": torque,
        }
        for i, p in enumerate(pressures, 1):
            sample[f"pressure_{i}"] = p
        for i, val in enumerate(imus, 1):
            sample[f"imu_{i}"] = val

        buffer.append(sample)

        with plot_lock:
            plot_state["times"].append(t)
            plot_state["ankle"].append(ankle)
            plot_state["torque"].append(torque)
            for i, p in enumerate(pressures, 1):
                plot_state["pressures"][i].append(p)
            for i, val in enumerate(imus, 1):
                plot_state["imus"][i].append(val)

        logger.log(t, ankle, pressures)

        try:
            event_q.put_nowait(
                {
                    "t": t,
                    "ankle": ankle,
                    "torque": torque,
                    "press": pressures,
                    "imu": imus,
                }
            )
        except Exception:
            pass

        time.sleep(dt)
        t += dt


# --------------------------------------------------------------------------------------
# Dash application
# --------------------------------------------------------------------------------------


def build_dash_app(cfg: Dict[str, Any], data_buf: Deque[Dict[str, float]]) -> dash.Dash:
    """Create and configure the Dash application."""
    # Serve JS/CSS assets locally so the dashboard works without Internet
    # access. ``serve_locally`` is available on newer Dash versions but we
    # also fall back to the older ``css.config``/``scripts.config`` flags
    # for backwards compatibility.
    # ``update_title`` is set to ``None`` so the browser tab always reads
    # "AFO Dashboard" instead of the default "Updating..." message while
    # callbacks are running.
    app = dash.Dash(__name__, serve_locally=True, update_title=None)
    app.title = "AFO Dashboard"
    if hasattr(app, "css") and hasattr(app.css, "config"):
        app.css.config.serve_locally = True
    if hasattr(app, "scripts") and hasattr(app.scripts, "config"):
        app.scripts.config.serve_locally = True

    app.layout = html.Div(
        className="dashboard",
        children=[
            html.H2("AFO Dashboard"),
            html.Div(
                className="controls-dock",
                children=[
                    html.Div(
                        className="controls",
                        children=[
                            html.Button("zero", id="zero-btn", n_clicks=0),
                            html.Button("motor", id="motor-btn", n_clicks=0),
                            html.Button("assist", id="assist-btn", n_clicks=0),
                            html.Button("k", id="k-btn", n_clicks=0),
                        ],
                    )
                ],
            ),
            dcc.Store(id="zero-state", data=0),
            dcc.Store(id="motor-state", data=0),
            dcc.Store(id="assist-state", data=0),
            dcc.Store(id="k-state", data=0),
            html.Div(id="signal-sent", style={"display": "none"}),
            dcc.Interval(id="zero-interval", interval=100, n_intervals=0),
            html.Div(EventSource(id="es", url="/events"), style={"display": "none"}),
            dcc.Store(id="tab-index", data=0),
            html.Div(
                className="tab-buttons",
                children=[
                    html.Button("Angle + Torque", id="tab-angle", n_clicks=0, className="active"),
                    html.Button("Insole", id="tab-insole", n_clicks=0),
                ],
            ),
            html.Div(
                className="swipe-container",
                children=[
                    html.Div(
                        className="swipe-page",
                        children=[
                            html.Div(
                                className="plots",
                                children=[
                                    dcc.Graph(
                                        id="torque",
                                        style={"height": "360px"},
                                        figure=go.Figure(
                                            data=[
                                                go.Scatter(
                                                    x=[],
                                                    y=[],
                                                    mode="lines",
                                                    name="actual_torque",
                                                    line=dict(width=3, color="#0B74FF"),
                                                )
                                            ],
                                            layout=dict(
                                                yaxis=dict(
                                                    range=[-5, 15],
                                                    title="Actual Torque",
                                                    gridcolor="#EEF1F4",
                                                    gridwidth=2,
                                                    zeroline=True,
                                                    zerolinecolor="#EEF1F4",
                                                    zerolinewidth=2,
                                                    tickfont=dict(size=16),
                                                ),
                                                xaxis=dict(
                                                    showgrid=False,
                                                    tickfont=dict(size=16),
                                                ),
                                                title=None,
                                                plot_bgcolor="rgba(0,0,0,0)",
                                                paper_bgcolor="rgba(0,0,0,0)",
                                                font=dict(
                                                    family="IBM Plex Sans Condensed",
                                                    size=18,
                                                ),
                                            ),
                                        ),
                                        config={"displayModeBar": False, "staticPlot": True},
                                    ),
                                    dcc.Graph(
                                        id="ankle",
                                        style={"height": "360px"},
                                        figure=go.Figure(
                                            data=[
                                                go.Scatter(
                                                    x=[],
                                                    y=[],
                                                    mode="lines",
                                                    name="ankle_angle",
                                                    line=dict(width=3, color="#12C37E"),
                                                )
                                            ],
                                            layout=dict(
                                                yaxis=dict(
                                                    range=[-60, 60],
                                                    title="Ankle Angle (deg)",
                                                    gridcolor="#EEF1F4",
                                                    gridwidth=2,
                                                    zeroline=True,
                                                    zerolinecolor="#EEF1F4",
                                                    zerolinewidth=2,
                                                    tickfont=dict(size=16),
                                                ),
                                                xaxis=dict(
                                                    showgrid=False,
                                                    tickfont=dict(size=16),
                                                ),
                                                title=None,
                                                plot_bgcolor="rgba(0,0,0,0)",
                                                paper_bgcolor="rgba(0,0,0,0)",
                                                font=dict(
                                                    family="IBM Plex Sans Condensed",
                                                    size=18,
                                                ),
                                            ),
                                        ),
                                        config={"displayModeBar": False, "staticPlot": True},
                                    ),
                                ],
                            )
                        ],
                    ),
                    html.Div(
                        className="swipe-page",
                        children=[
                            html.Div(
                                className="plots",
                                children=[
                                    dcc.Graph(
                                        id="press",
                                        style={"height": "360px"},
                                        figure=go.Figure(
                                            data=[
                                                go.Scatter(
                                                    x=[],
                                                    y=[],
                                                    mode="lines",
                                                    name=f"pressure_{i}",
                                                    line=dict(
                                                        width=3,
                                                        color=COLOR_CYCLE[(i - 1) % len(COLOR_CYCLE)],
                                                    ),
                                                )
                                                for i in range(1, 9)
                                            ],
                                            layout=dict(
                                                yaxis=dict(
                                                    range=[0, 1000],
                                                    title="Pressure",
                                                    gridcolor="#EEF1F4",
                                                    gridwidth=2,
                                                    zeroline=True,
                                                    zerolinecolor="#EEF1F4",
                                                    zerolinewidth=2,
                                                    tickfont=dict(size=16),
                                                ),
                                                xaxis=dict(
                                                    showgrid=False,
                                                    tickfont=dict(size=16),
                                                ),
                                                title=None,
                                                legend=dict(
                                                    orientation="h",
                                                    yanchor="bottom",
                                                    y=1.02,
                                                    xanchor="left",
                                                    x=0,
                                                ),
                                                margin=dict(t=60),
                                                plot_bgcolor="rgba(0,0,0,0)",
                                                paper_bgcolor="rgba(0,0,0,0)",
                                                font=dict(
                                                    family="IBM Plex Sans Condensed",
                                                    size=18,
                                                ),
                                            ),
                                        ),
                                        config={"displayModeBar": False, "staticPlot": True},
                                    ),
                                    dcc.Graph(
                                        id="imu",
                                        style={"height": "360px"},
                                        figure=go.Figure(
                                            data=[
                                                go.Scatter(
                                                    x=[],
                                                    y=[],
                                                    mode="lines",
                                                    name=f"imu_{i}",
                                                    line=dict(
                                                        width=3,
                                                        color=COLOR_CYCLE[(i - 1) % len(COLOR_CYCLE)],
                                                    ),
                                                )
                                                for i in range(1, 4)
                                            ],
                                            layout=dict(
                                                yaxis=dict(
                                                    range=[-3, 3],
                                                    title="IMU",
                                                    gridcolor="#EEF1F4",
                                                    gridwidth=2,
                                                    zeroline=True,
                                                    zerolinecolor="#EEF1F4",
                                                    zerolinewidth=2,
                                                    tickfont=dict(size=16),
                                                ),
                                                xaxis=dict(
                                                    showgrid=False,
                                                    tickfont=dict(size=16),
                                                ),
                                                title=None,
                                                legend=dict(orientation="h"),
                                                margin=dict(t=60),
                                                plot_bgcolor="rgba(0,0,0,0)",
                                                paper_bgcolor="rgba(0,0,0,0)",
                                                font=dict(
                                                    family="IBM Plex Sans Condensed",
                                                    size=18,
                                                ),
                                            ),
                                        ),
                                        config={"displayModeBar": False, "staticPlot": True},
                                    ),
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )

    # ------------------------------------------------------------------
    # Client-side callbacks for instantaneous button feedback
    # ------------------------------------------------------------------

    # zero button behaves like a momentary switch
    app.clientside_callback(
        """
        function(n) {
            var active = document.getElementById('zero-btn').matches(':active');
            return [active ? 1 : 0, active ? 'on' : ''];
        }
        """,
        Output("zero-state", "data"),
        Output("zero-btn", "className"),
        Input("zero-interval", "n_intervals"),
        prevent_initial_call=False,
    )


    app.clientside_callback(
        """
        function(n, state){
            if(typeof state !== 'number') state = 0;
            if(n === undefined){ return [state, state ? 'on' : '']; }
            var newState = 1 - state;
            return [newState, newState ? 'on' : ''];
        }
        """,
        Output("motor-state", "data"),
        Output("motor-btn", "className"),
        Input("motor-btn", "n_clicks"),
        State("motor-state", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """
        function(n, state){
            if(typeof state !== 'number') state = 0;
            if(n === undefined){ return [state, state ? 'on' : '']; }
            var newState = 1 - state;
            return [newState, newState ? 'on' : ''];
        }
        """,
        Output("assist-state", "data"),
        Output("assist-btn", "className"),
        Input("assist-btn", "n_clicks"),
        State("assist-state", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """
        function(n, state){
            if(typeof state !== 'number') state = 0;
            if(n === undefined){ return [state, state ? 'on' : '']; }
            var newState = 1 - state;
            return [newState, newState ? 'on' : ''];
        }
        """,
        Output("k-state", "data"),
        Output("k-btn", "className"),
        Input("k-btn", "n_clicks"),
        State("k-state", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """
        function(n0, n1, idx){
            var ctx = dash_clientside.callback_context;
            if(typeof idx !== 'number') idx = 0;
            if(ctx.triggered.length){
                var id = ctx.triggered[0].prop_id.split('.')[0];
                if(id === 'tab-angle'){ idx = 0; }
                else if(id === 'tab-insole'){ idx = 1; }
            }
            var cont = document.querySelector('.swipe-container');
            if(cont){
                var width = cont.clientWidth;
                cont.scrollTo({left: width * idx, behavior: 'smooth'});
            }
            return [idx, idx===0 ? 'active' : '', idx===1 ? 'active' : ''];
        }
        """,
        Output("tab-index", "data"),
        Output("tab-angle", "className"),
        Output("tab-insole", "className"),
        Input("tab-angle", "n_clicks"),
        Input("tab-insole", "n_clicks"),
        State("tab-index", "data"),
        prevent_initial_call=False,
    )

    # ------------------------------------------------------------------
    # Callback: send control packet when signal states change
    # ------------------------------------------------------------------
    @app.callback(
        Output("signal-sent", "children"),
        Input("zero-state", "data"),
        Input("motor-state", "data"),
        Input("assist-state", "data"),
        Input("k-state", "data"),
        prevent_initial_call=True,
    )
    def update_signals(
        zero_state: int,
        motor_state: int,
        assist_state: int,
        k_state: int,
    ) -> str:
        send_control_packet(cfg, zero_state, motor_state, assist_state, k_state)
        return ""

    # ------------------------------------------------------------------
    # Callback: Update graphs on *each* SSE message (near real-time)
    # ------------------------------------------------------------------
    @app.callback(
        Output("torque", "extendData"),
        Output("ankle", "extendData"),
        Output("press", "extendData"),
        Output("imu", "extendData"),
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
        torques = payload.get("torque", [])
        pressures = payload.get("press", [])
        imus = payload.get("imu", [])

        if not isinstance(times, list):
            times = [times]
        if not isinstance(ankles, list):
            ankles = [ankles]
        if not isinstance(torques, list):
            torques = [torques]
        if pressures and isinstance(pressures[0], (int, float)):
            pressures = [pressures]
        if imus and isinstance(imus[0], (int, float)):
            imus = [imus]

        if len(plot_state["times"]) < 5:
            print(f"push_batch first payload → count={len(times)}")

        torque_payload = dict(x=[times], y=[torques])
        ankle_payload = dict(x=[times], y=[ankles])

        # transpose pressures -> 8 traces
        transposed = list(zip(*pressures)) if pressures else [[] for _ in range(8)]
        press_payload = dict(
            x=[times for _ in range(8)],
            y=[list(tr) for tr in transposed],
        )

        transposed_imu = list(zip(*imus)) if imus else [[] for _ in range(3)]
        transposed_imu = transposed_imu[:3]
        imu_payload = dict(
            x=[times for _ in range(3)],
            y=[list(tr) for tr in transposed_imu],
        )

        # dcc.Graph.extendData expects a tuple of (data, trace_indices, max_points)
        return (
            torque_payload,
            [0],
            1000,
        ), (
            ankle_payload,
            [0],
            1000,
        ), (
            press_payload,
            list(range(8)),
            1000,
        ), (
            imu_payload,
            list(range(3)),
            1000,
        )

    # ------------------------------------------------------------------
    # SSE endpoint: /events  (one single connection per browser client)
    # ------------------------------------------------------------------
    @app.server.route("/events")
    def sse_stream():  # type: ignore
        global _active_clients

        with _client_lock:
            if _active_clients >= MAX_CLIENTS:
                return Response("Too many clients", status=503)
            _active_clients += 1
        print(f"[SSE] Client connected. total={_active_clients}")

        def generate():
            try:
                global _active_clients
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
                        "torque": [s["torque"] for s in batch],
                        "press": [s["press"] for s in batch],
                        "imu": [s["imu"] for s in batch],
                    }
                    batch.clear()
                    yield f"data:{json.dumps(payload)}\n\n"
            finally:
                with _client_lock:
                    _active_clients -= 1
                print(f"[SSE] Client disconnected. total={_active_clients}")

        return Response(generate(), mimetype="text/event-stream")

    return app


# --------------------------------------------------------------------------------------
# Entry-point
# --------------------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config()

    logger = DataLogger(LOG_FILE)
    data_queue: Deque[Dict[str, float]] = collections.deque(maxlen=HISTORY)

    # Spin up the UDP listener, falling back to a fake data generator if the
    # Simulink host cannot be reached.
    target_fn = start_udp_listener
    if not is_host_reachable(cfg["udp"]["send_host"]):
        target_fn = start_fake_data

    listener_t = threading.Thread(
        target=target_fn,
        args=(cfg, data_queue, logger),
        daemon=True,
    )
    listener_t.start()

    try:
        dash_app = build_dash_app(cfg, data_queue)
        dash_app.run(
            host="127.0.0.1",
            port=8050,
            debug=True,
            use_reloader=False,
            threaded=True,
        )
    finally:
        logger.stop()
