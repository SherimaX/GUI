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
`config.yaml`. The app listens on the configured port and updates at
~SAMPLE_RATE_HZ Hz (see :data:`SAMPLE_RATE_HZ`).
"""

from __future__ import annotations

import struct
import threading
import time
from typing import Dict, Any
import platform
import subprocess
import math

import yaml
import socket
import json
from queue import Queue, Full
from flask import Response
from dash_extensions import EventSource

import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objs as go
import string  # added to use Template for JS string substitution to avoid brace-escaping issues

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

# Configuration constants
CONFIG_FILE = "config.yaml"
CONTROL_FMT = "<4f"  # zero, motor, assist, k  (4 × float32 = 16 bytes)
# Unused legacy constant retained for compatibility
HISTORY = 1000  # currently unused
# Throttle SSE updates to roughly the incoming sample rate
UPDATE_MS = 10
N_WINDOW_SEC = 10  # how many seconds of data to show in plots
SAMPLE_RATE_HZ = 100  # expected UDP sample rate
max_points = int(N_WINDOW_SEC * SAMPLE_RATE_HZ)

# Queue for server-sent events (SSE) to push fresh samples to the browser.
# Only a single sample is stored; the browser keeps its own circular buffer.
event_q: Queue = Queue(maxsize=1)

# Limit concurrent SSE clients
MAX_CLIENTS = 5
_active_clients = 0
_client_lock = threading.Lock()

# Helper to create a line trace with a separate legend marker
def make_line_with_marker(name: str, color: str) -> list[go.Scattergl]:
    """Return a line trace and a marker-only trace for the legend."""
    line = go.Scattergl(
        x=[],
        y=[],
        mode="lines",
        name=name,
        line=dict(width=3, color=color),
        legendgroup=name,
        showlegend=False,
        )
    marker = go.Scattergl(
        x=[None],
        y=[None],
        mode="markers",
        name=name,
        marker=dict(size=8, color=color, symbol="circle"),
        legendgroup=name,
        showlegend=True,
        )
    return [line, marker]

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
# Background UDP listener (pushes decoded packets to the SSE queue)
# --------------------------------------------------------------------------------------


def start_udp_listener(cfg: Dict[str, Any]) -> None:
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
    print(
        f"Listening for data on {cfg['udp']['listen_host']}:{cfg['udp']['listen_port']}"
        )


    while True:
        try:
            data, _ = sock.recvfrom(expected)
        except socket.timeout:
            continue

        if len(data) != expected:
            continue  # ignore malformed packet
        decoded = decode_packet(data, fmt, mapping)
        decoded["timestamp"] = time.time()
        # Extract fields for logging and plotting
        sim_t = decoded.get("time", decoded.get("Time", 0.0))
        ankle = decoded.get("ankle_angle", 0.0)
        torque = decoded.get("actual_torque", 0.0)
        gait = decoded.get("gait_percentage", 0.0)


        # ------------------------------------------------------------------
        # Push latest sample to SSE queue (non-blocking)
        # ------------------------------------------------------------------
        sample = {
            "t": sim_t,
            "ankle": ankle,
            "torque": torque,
            "gait": gait,
            "press": [decoded.get(f"pressure_{i}", 0.0) for i in range(1, 9)],
            "imu": [decoded.get(f"imu_{i}", 0.0) for i in range(1, 13)],
            "statusword": decoded.get("statusword", 0.0),
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



def start_fake_data(cfg: Dict[str, Any]) -> None:
    """Generate synthetic samples when the Simulink host is unreachable."""
    print("Simulink host unreachable – using fake data generator")
    t = 0.0
    # match the expected sample rate
    dt = 1.0 / SAMPLE_RATE_HZ
    while True:
        ankle = 20.0 * math.sin(t)
        torque = 5.0 * math.sin(t / 2.0)
        pressures = [500.0 + 100.0 * math.sin(t + i) for i in range(8)]
        imus = [math.sin(t + i * 0.1) for i in range(12)]
        gait = (t % 1.0) * 100.0

        sample = {
            "t": t,
            "ankle": ankle,
            "torque": torque,
            "gait": gait,
            "press": pressures,
            "imu": imus,
            "statusword": 1591,
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


# --------------------------------------------------------------------------------------
# Dash application
# --------------------------------------------------------------------------------------


def build_dash_app(cfg: Dict[str, Any]) -> dash.Dash:
    """Create and configure the Dash application."""
    # Serve JS/CSS assets locally so the dashboard works without Internet
    # access. ``serve_locally`` is available on newer Dash versions but we
    # also fall back to the older ``css.config``/``scripts.config`` flags
    # for backwards compatibility.
    # ``update_title`` is set to ``None`` so the browser tab always reads
    # "AFO Dashboard" instead of the default "Updating..." message while
    # callbacks are running.
    # Include a meta tag so the site can be added to the iPad/iOS home screen
    # without Safari chrome. This enables the "Add to Home Screen" feature to
    # launch the dashboard as a standalone web app.
    meta = [{"name": "apple-mobile-web-app-capable", "content": "yes"}]
    app = dash.Dash(
        __name__,
        serve_locally=True,
        update_title=None,
        meta_tags=meta,
    )
    app.title = "AFO Dashboard"
    if hasattr(app, "css") and hasattr(app.css, "config"):
        app.css.config.serve_locally = True
    if hasattr(app, "scripts") and hasattr(app.scripts, "config"):
        app.scripts.config.serve_locally = True

    app.layout = html.Div(
        className="dashboard",
        children=[
            html.H2("AFO Dashboard"),
            dcc.Store(id="zero-state", data=0),
            dcc.Store(id="motor-state", data=0),
            dcc.Store(id="assist-state", data=0),
            dcc.Store(id="k-state", data=0),
            html.Div(id="signal-sent", style={"display": "none"}),
            dcc.Interval(id="zero-interval", interval=100, n_intervals=0),
            dcc.Interval(id="tab-interval", interval=1000, n_intervals=0),
            html.Div(EventSource(id="es", url="/events"), style={"display": "none"}),
            dcc.Store(id="tab-index", data=0),
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
                                            data=make_line_with_marker(
                                                "actual_torque", "#0B74FF"
                                            ),
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
                                                showlegend=False,
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
                                            data=make_line_with_marker(
                                                "ankle_angle", "#12C37E"
                                            ),
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
                                                showlegend=False,
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
                                        id="gait",
                                        style={"height": "360px"},
                                        figure=go.Figure(
                                            data=make_line_with_marker(
                                                "gait_percentage", "#FF7F0E"
                                            ),
                                            layout=dict(
                                                yaxis=dict(
                                                    range=[0, 100],
                                                    title="Gait %",
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
                                                showlegend=False,
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
                                                tr
                                                for i in range(1, 9)
                                                for tr in make_line_with_marker(
                                                    f"pressure_{i}",
                                                    COLOR_CYCLE[(i - 1) % len(COLOR_CYCLE)],
                                                )
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
                                                tr
                                                for i in range(1, 4)
                                                for tr in make_line_with_marker(
                                                    f"imu_{i}",
                                                    COLOR_CYCLE[(i - 1) % len(COLOR_CYCLE)],
                                                )
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
                                ],
                            )
                        ],
                    ),
                ],
            ),
            html.Div(
                id="tab-dots",
                style={"display": "flex", "justifyContent": "center", "alignItems": "center", "marginTop": "0px", "marginBottom": "0px"},
                children=[
                    html.Span(className="tab-dot", id="dot-0"),
                    html.Span(className="tab-dot", id="dot-1"),
                ],
            ),
            html.Div(
                className="controls-dock",
                style={"marginTop": "100px"},
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
    # Client-side callback: Update graphs for each SSE batch
    # ------------------------------------------------------------------
    graph_update_js = string.Template(r"""
        function(msg){
            if(!msg){ return [null, null, null, null, null]; }

            var json_str = (typeof msg === 'string') ? msg : (msg && msg.data);
            if(!json_str){ return [null, null, null, null, null, null]; }

            var payload;
            try {
                payload = JSON.parse(json_str);
            } catch(e){
                console.error('failed to parse SSE payload', e);
                return [null, null, null, null, null, null];
            }

            var t = payload.t;
            var ankle = payload.ankle;
            var torque = payload.torque;
            var gait = payload.gait;
            var press = payload.press;
            var imu = payload.imu;
            var status = payload.statusword;

            if(!Array.isArray(t)) t = [t];
            if(!Array.isArray(ankle)) ankle = [ankle];
            if(!Array.isArray(torque)) torque = [torque];
            if(!Array.isArray(gait)) gait = [gait];
            if(press && typeof press[0] === 'number') press = [press];
            if(imu && typeof imu[0] === 'number') imu = [imu];

            var pressT = Array.from({length:8}, () => []);
            for(var i=0;i<press.length;i++){
                for(var j=0;j<8;j++){
                    pressT[j].push(press[i][j]);
                }
            }

            var imuT = Array.from({length:3}, () => []);
            for(var i=0;i<imu.length;i++){
                for(var j=0;j<3;j++){
                    imuT[j].push(imu[i][j]);
                }
            }

            var torque_payload = {x:[t], y:[torque]};
            var ankle_payload = {x:[t], y:[ankle]};
            var gait_payload = {x:[t], y:[gait]};
            var press_payload = {x:Array(8).fill(t), y:pressT};
            var imu_payload = {x:Array(3).fill(t), y:imuT};

            var colorReady = '#FFD280';  // light orange
            var colorFault = '#FF9E9E';  // light red
            var colorReached = '#8FE38F'; // light green
            var colorDefault = '#cccccc';

            function textColorFor(bg){
                if(!bg || bg.charAt(0) !== '#') return '#000000';
                var r = parseInt(bg.slice(1,3), 16);
                var g = parseInt(bg.slice(3,5), 16);
                var b = parseInt(bg.slice(5,7), 16);
                var brightness = (r*299 + g*587 + b*114)/1000;
                return brightness > 150 ? '#000000' : '#ffffff';
            }

            var color = colorDefault;
            if(status != null){
                if(status & 0x0008){ // fault bit
                    color = colorFault;
                } else if((status & 0x0002) && (status & 0x0400)){
                    color = colorReached; // switched on + target reached
                } else if(status & 0x0001){
                    color = colorReady; // ready to switch on
                }
            }
            var btn_style = {backgroundColor: color, color: textColorFor(color)};

            // Slide x-axis window to show only the last 10 seconds
            var latestT = t[t.length - 1];
            if(typeof latestT !== 'number') latestT = Number(latestT);
            var xrange = [latestT - 10.0, latestT];

            // Apply relayout on each graph individually (if already rendered)
            ['torque', 'ankle', 'gait', 'press', 'imu'].forEach(function(id) {
                var gd = document.getElementById(id);
                if(gd) {
                    try {
                        Plotly.relayout(gd, {'xaxis.range': xrange});
                    } catch(e) { /* ignore before initial render */ }
                }
            });

            return [
                [torque_payload, [0], ${max_points}],
                [ankle_payload, [0], ${max_points}],
                [gait_payload, [0], ${max_points}],
                [press_payload, [0,2,4,6,8,10,12,14], ${max_points}],
                [imu_payload, [0,2,4], ${max_points}],
                btn_style
            ];
        }
        """).substitute(max_points=max_points)

    app.clientside_callback(
        graph_update_js,
        Output("torque", "extendData"),
        Output("ankle", "extendData"),
        Output("gait", "extendData"),
        Output("press", "extendData"),
        Output("imu", "extendData"),
        Output("motor-btn", "style"),
        Input("es", "message"),
        prevent_initial_call=True,
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
        # Removed verbose console output for each SSE client connection.
        # Use logging/debugging as needed instead of printing.

        def generate():
            try:
                global _active_clients
                while True:
                    sample = event_q.get()
                    yield f"data:{json.dumps(sample)}\n\n"
            finally:
                with _client_lock:
                    _active_clients -= 1

        return Response(generate(), mimetype="text/event-stream")

    # Add a clientside callback to update the tab-index based on scroll position
    app.clientside_callback(
        '''
        function(n_intervals) {
            var swipe = document.querySelector('.swipe-container');
            if (!swipe) return window.dash_clientside.no_update;
            if (!swipe._dotScrollHandlerAttached) {
                swipe.addEventListener('scroll', function() {
                    var idx = Math.round(swipe.scrollLeft / swipe.clientWidth);
                    var dots = [document.getElementById('dot-0'), document.getElementById('dot-1')];
                    for (var i = 0; i < dots.length; ++i) {
                        if (dots[i]) {
                            dots[i].className = 'tab-dot' + (i === idx ? ' active' : '');
                        }
                    }
                });
                swipe._dotScrollHandlerAttached = true;
            }
            // Set initial state
            var idx = Math.round(swipe.scrollLeft / swipe.clientWidth);
            var dots = [document.getElementById('dot-0'), document.getElementById('dot-1')];
            for (var i = 0; i < dots.length; ++i) {
                if (dots[i]) {
                    dots[i].className = 'tab-dot' + (i === idx ? ' active' : '');
                }
            }
            return window.dash_clientside.no_update;
        }
        ''',
        Output('tab-index', 'data'),
        Input('zero-interval', 'n_intervals'),
        prevent_initial_call=False,
    )

    return app


# --------------------------------------------------------------------------------------
# Entry-point
# --------------------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config()

    # Spin up the UDP listener, falling back to a fake data generator if the
    # Simulink host cannot be reached.
    target_fn = start_udp_listener
    if not is_host_reachable(cfg["udp"]["send_host"]):
        target_fn = start_fake_data

    listener_t = threading.Thread(
        target=target_fn,
        args=(cfg,),
        daemon=True,
        )
    listener_t.start()

    dash_app = build_dash_app(cfg)
    dash_app.run(
        host="127.0.0.1",
        port=8050,
        debug=False,
        use_reloader=False,
        threaded=True,
        )
