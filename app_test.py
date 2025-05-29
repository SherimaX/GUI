#!/usr/bin/env python3
"""Offline Dash viewer for logged Simulink data.

Reads `data_log.csv` created by the live dashboard and displays:
1. Ankle angle over the entire recording (y-range −60…+60°).
2. All eight plantar pressure traces (y-range 0…1000).

The graphs refresh every 500 ms so new data appended to the CSV becomes
visible even though this viewer does **not** open any UDP sockets.
"""

from __future__ import annotations

import pandas as pd
import dash
from dash import dcc, html, Input, Output
import plotly.graph_objs as go
import os

LOG_FILE = "data_log.csv"
REFRESH_MS = 500  # refresh interval for file polling

# Consistent colors for pressure traces
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

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def load_csv(path: str = LOG_FILE) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:  # pragma: no cover
        return pd.DataFrame()


# -----------------------------------------------------------------------------
# Dash App
# -----------------------------------------------------------------------------

# Serve assets locally so the offline viewer does not depend on the network.
app = dash.Dash(__name__, serve_locally=True)
if hasattr(app, "css") and hasattr(app.css, "config"):
    app.css.config.serve_locally = True
if hasattr(app, "scripts") and hasattr(app.scripts, "config"):
    app.scripts.config.serve_locally = True

app.layout = html.Div(
    [
        html.H2("Simulink Data – Offline Viewer"),
        html.Div(f"Reading from {LOG_FILE}", style={"marginBottom": "10px"}),
        dcc.Interval(id="timer", interval=REFRESH_MS, n_intervals=0),
        dcc.Graph(id="ankle-graph"),
        dcc.Graph(id="pressure-graph"),
    ]
)


@app.callback(
    Output("ankle-graph", "figure"),
    Output("pressure-graph", "figure"),
    Input("timer", "n_intervals"),
)
def update_figures(_):  # noqa: D401
    df = load_csv()
    if df.empty or "time" not in df:
        return go.Figure(), go.Figure()

    times = df["time"].tolist()

    # Ankle angle
    fig_ankle = go.Figure()
    fig_ankle.add_trace(
        go.Scatter(
            x=times,
            y=df["ankle_angle"],
            mode="lines",
            name="ankle_angle",
            line=dict(width=3, color="#0B74FF"),
        )
    )
    fig_ankle.update_yaxes(
        range=[-60, 60],
        title="Ankle Angle (deg)",
        gridcolor="#EEF1F4",
        gridwidth=2,
        zeroline=True,
        zerolinecolor="#EEF1F4",
        zerolinewidth=2,
        tickfont=dict(size=12),
    )
    fig_ankle.update_xaxes(
        range=[times[0], times[-1]], showgrid=False, tickfont=dict(size=12)
    )
    fig_ankle.update_layout(
        title=None,
        plot_bgcolor="rgba(240,240,240,0.4)",
        paper_bgcolor="rgba(240,240,240,0.4)",
        font=dict(family="IBM Plex Sans Condensed", size=14),
    )

    # Pressures
    fig_press = go.Figure()
    for i in range(1, 9):
        key = f"pressure_{i}"
        if key in df:
            color = COLOR_CYCLE[(i - 1) % len(COLOR_CYCLE)]
            fig_press.add_trace(
                go.Scatter(
                    x=times,
                    y=df[key],
                    mode="lines",
                    name=key,
                    line=dict(width=3, color=color),
                )
            )
    fig_press.update_yaxes(
        range=[0, 1000],
        title="Pressure",
        gridcolor="#EEF1F4",
        gridwidth=2,
        zeroline=True,
        zerolinecolor="#EEF1F4",
        zerolinewidth=2,
        tickfont=dict(size=12),
    )
    fig_press.update_xaxes(
        range=[times[0], times[-1]], showgrid=False, tickfont=dict(size=12)
    )
    fig_press.update_layout(
        title=None,
        plot_bgcolor="rgba(240,240,240,0.4)",
        paper_bgcolor="rgba(240,240,240,0.4)",
        legend=dict(orientation="h"),
        font=dict(family="IBM Plex Sans Condensed", size=14),
    )

    return fig_ankle, fig_press


if __name__ == "__main__":
    app.run(debug=True) 