import json
import string
from typing import Dict, Any

import dash
from dash import dcc, html, Input, Output, State
from dash_extensions import EventSource
import plotly.graph_objs as go
from flask import Response

from constants import COLOR_CYCLE, N_WINDOW_SEC, SAMPLE_RATE_HZ
from state import event_q, MAX_CLIENTS, _active_clients, _client_lock
from network import send_control_packet


def make_line_with_marker(name: str, color: str) -> list[go.Scattergl]:
    """Return a line trace and a marker-only trace for the legend."""
    clean_name = name.replace("_", " ")
    line = go.Scattergl(
        x=[],
        y=[],
        mode="lines",
        name=clean_name,
        line=dict(width=3, color=color),
        legendgroup=clean_name,
        showlegend=False,
    )
    marker = go.Scattergl(
        x=[None],
        y=[None],
        mode="markers",
        name=clean_name,
        marker=dict(size=8, color=color, symbol="circle"),
        legendgroup=clean_name,
        showlegend=True,
    )
    return [line, marker]


def build_dash_app(cfg: Dict[str, Any]) -> dash.Dash:
    """Create and configure the Dash application."""
    meta = [
        {"name": "apple-mobile-web-app-capable", "content": "yes"},
        {
            "name": "viewport",
            "content": "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no",
        },
    ]
    app = dash.Dash(__name__, serve_locally=True, update_title=None, meta_tags=meta)
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
            dcc.Store(id="window-sec", data=N_WINDOW_SEC),
            html.Div(id="signal-sent", style={"display": "none"}),
            dcc.Interval(id="zero-interval", interval=100, n_intervals=0),
            dcc.Interval(id="tab-interval", interval=1000, n_intervals=0),
            html.Div(EventSource(id="es", url="/events"), style={"display": "none"}),
            dcc.Store(id="tab-index", data=0),
            html.Div(
                className="controls-dock",
                children=[
                    html.Div(
                        className="controls",
                        children=[
                            html.Button("2s", id="window-2-btn", n_clicks=0),
                            html.Button("10s", id="window-10-btn", n_clicks=0),
                        ],
                    )
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
                                            data=(
                                                make_line_with_marker("actual torque", "#0B74FF")
                                                + make_line_with_marker("demand torque", "#FF7F0E")
                                            ),
                                            layout=dict(
                                                yaxis=dict(
                                                    range=[-5, 15],
                                                    title="Torque (Nm)",
                                                    gridcolor="#EEF1F4",
                                                    gridwidth=2,
                                                    zeroline=True,
                                                    zerolinecolor="#EEF1F4",
                                                    zerolinewidth=2,
                                                    tickfont=dict(size=16),
                                                ),
                                                xaxis=dict(showgrid=False, tickfont=dict(size=16)),
                                                title=None,
                                                showlegend=True,
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
                                                font=dict(family="IBM Plex Sans Condensed", size=18),
                                            ),
                                        ),
                                        config={"displayModeBar": False, "staticPlot": True},
                                    ),
                                    dcc.Graph(
                                        id="ankle",
                                        style={"height": "360px"},
                                        figure=go.Figure(
                                            data=make_line_with_marker("ankle_angle", "#12C37E"),
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
                                                xaxis=dict(showgrid=False, tickfont=dict(size=16)),
                                                title=None,
                                                showlegend=False,
                                                plot_bgcolor="rgba(0,0,0,0)",
                                                paper_bgcolor="rgba(0,0,0,0)",
                                                font=dict(family="IBM Plex Sans Condensed", size=18),
                                            ),
                                        ),
                                        config={"displayModeBar": False, "staticPlot": True},
                                    ),
                                    dcc.Graph(
                                        id="gait",
                                        style={"height": "360px"},
                                        figure=go.Figure(
                                            data=make_line_with_marker("gait_percentage", "#FF7F0E"),
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
                                                xaxis=dict(showgrid=False, tickfont=dict(size=16)),
                                                title=None,
                                                showlegend=False,
                                                plot_bgcolor="rgba(0,0,0,0)",
                                                paper_bgcolor="rgba(0,0,0,0)",
                                                font=dict(family="IBM Plex Sans Condensed", size=18),
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
                                                xaxis=dict(showgrid=False, tickfont=dict(size=16)),
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
                                                font=dict(family="IBM Plex Sans Condensed", size=18),
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
                                                xaxis=dict(showgrid=False, tickfont=dict(size=16)),
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
                                                font=dict(family="IBM Plex Sans Condensed", size=18),
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
                children=[html.Span(className="tab-dot", id="dot-0"), html.Span(className="tab-dot", id="dot-1")],
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

    @app.callback(
        Output("signal-sent", "children"),
        Input("zero-state", "data"),
        Input("motor-state", "data"),
        Input("assist-state", "data"),
        Input("k-state", "data"),
        prevent_initial_call=True,
    )
    def update_signals(zero_state: int, motor_state: int, assist_state: int, k_state: int) -> str:
        print(
            "Control states -- zero: {0}, motor: {1}, assist: {2}, k: {3}".format(
                zero_state, motor_state, assist_state, k_state
            )
        )
        send_control_packet(cfg, zero_state, motor_state, assist_state, k_state)
        return ""

    @app.callback(
        Output("window-sec", "data"),
        Input("window-2-btn", "n_clicks"),
        Input("window-10-btn", "n_clicks"),
        State("window-sec", "data"),
        prevent_initial_call=True,
    )
    def update_window(n2, n10, current):
        ctx = dash.callback_context
        if not ctx.triggered:
            return current
        btn = ctx.triggered[0]["prop_id"].split(".")[0]
        if btn == "window-2-btn":
            return 0.4
        if btn == "window-10-btn":
            return 2
        return current

    graph_update_js = string.Template(
        r"""
        function(msg, window_sec){
            if(!msg){
                if(typeof window_sec === 'number'){
                    ['torque','ankle','gait','press','imu'].forEach(function(id){
                        var gd = document.getElementById(id);
                        if(gd && gd.data && gd.data.length && gd.data[0].x && gd.data[0].x.length){
                            var xData = gd.data[0].x;
                            var latest = xData[xData.length - 1];
                            if(typeof latest !== 'number') latest = Number(latest);
                            Plotly.relayout(gd, {
                                'xaxis.autorange': false,
                                'xaxis.range': [latest - window_sec, latest]
                            });
                        }
                    });
                }
                return [null, null, null, null, null, null];
            }

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
            var demand = payload.demand_torque;
            var gait = payload.gait;
            var press = payload.press;
            var imu = payload.imu;
            var status = payload.statusword;
            var avg_dt = payload.avg_dt;

            if(!Array.isArray(t)) t = [t];
            if(!Array.isArray(ankle)) ankle = [ankle];
            if(!Array.isArray(torque)) torque = [torque];
            if(!Array.isArray(demand)) demand = [demand];
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

            var torque_payload = {x:[t, t], y:[torque, demand]};
            var ankle_payload = {x:[t], y:[ankle]};
            var gait_payload = {x:[t], y:[gait]};
            var press_payload = {x:Array(8).fill(t), y:pressT};
            var imu_payload = {x:Array(3).fill(t), y:imuT};

            var colorReady = '#FFD280';
            var colorFault = '#FF9E9E';
            var colorReached = '#8FE38F';
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
                if(status & 0x0008){
                    color = colorFault;
                } else if((status & 0x0002) && (status & 0x0400)){
                    color = colorReached;
                } else if(status & 0x0001){
                    color = colorReady;
                }
            }
            var btn_style = {backgroundColor: color, color: textColorFor(color)};

            var winSec = (typeof window_sec === 'number') ? window_sec : ${default_window};
            var dt = (typeof avg_dt === 'number') ? avg_dt : 1.0/${sample_rate};
            var maxPoints = Math.round(winSec / dt);

            var latestT = t[t.length - 1];
            if(typeof latestT !== 'number') latestT = Number(latestT);
            var xrange = [latestT - winSec, latestT];

            ['torque', 'ankle', 'gait', 'press', 'imu'].forEach(function(id) {
                var gd = document.getElementById(id);
                if(gd) {
                    try {
                        Plotly.relayout(gd, {
                            'xaxis.autorange': false,
                            'xaxis.range': xrange
                        });
                    } catch(e) { /* ignore before initial render */ }
                }
            });
            return [
                [torque_payload, [0,2], maxPoints],
                [ankle_payload, [0], maxPoints],
                [gait_payload, [0], maxPoints],
                [press_payload, [0,2,4,6,8,10,12,14], maxPoints],
                [imu_payload, [0,2,4], maxPoints],
                btn_style
            ];
        }
        """
    ).substitute(sample_rate=SAMPLE_RATE_HZ, default_window=N_WINDOW_SEC)

    app.clientside_callback(
        graph_update_js,
        Output("torque", "extendData"),
        Output("ankle", "extendData"),
        Output("gait", "extendData"),
        Output("press", "extendData"),
        Output("imu", "extendData"),
        Output("motor-btn", "style"),
        Input("es", "message"),
        Input("window-sec", "data"),
        prevent_initial_call=True,
    )

    @app.server.route("/events")
    def sse_stream():  # type: ignore
        global _active_clients
        with _client_lock:
            if _active_clients >= MAX_CLIENTS:
                return Response("Too many clients", status=503)
            _active_clients += 1

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
