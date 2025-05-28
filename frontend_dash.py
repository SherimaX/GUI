"""frontend_dash.py – minimal Dash app that consumes the /events SSE feed and
prints the latest sample plus a live graph.

Run this *after* you have listener.py and sse_server.py running.
This isolates the front-end side so you can test it independently.
"""

from __future__ import annotations

import dash
from dash import html, dcc, Output, Input
import plotly.graph_objs as go
from dash_extensions import EventSource
import json

# Serve Dash assets locally so this demo works without Internet access. This
# uses ``serve_locally=True`` for modern versions and falls back to the old
# ``css.config``/``scripts.config`` flags if present.
app = dash.Dash(__name__, serve_locally=True)
if hasattr(app, "css") and hasattr(app.css, "config"):
    app.css.config.serve_locally = True
if hasattr(app, "scripts") and hasattr(app.scripts, "config"):
    app.scripts.config.serve_locally = True

app.layout = html.Div(
    [
        html.H3("DEBUG Dash Frontend"),
        EventSource(id="es", url="http://127.0.0.1:8051/events"),
        html.Pre(
            id="raw",
            style={
                "height": "120px",
                "overflowY": "scroll",
                "border": "1px solid #ccc",
            },
        ),
        dcc.Graph(
            id="angle", figure=go.Figure(layout=dict(yaxis=dict(range=[-60, 60])))
        ),
    ]
)


@app.callback(
    Output("raw", "children"),
    Output("angle", "extendData"),
    Input("es", "message"),
    prevent_initial_call=True,
)
def got_msg(msg):
    if msg is None:
        raise dash.exceptions.PreventUpdate

    if isinstance(msg, str):
        payload = json.loads(msg)
    elif isinstance(msg, dict) and "data" in msg:
        payload = json.loads(msg["data"])
    else:
        raise dash.exceptions.PreventUpdate

    t = payload.get("t")
    ankle = payload.get("ankle")

    raw_line = json.dumps(payload, indent=2)

    extend = dict(x=[[t]], y=[[ankle]])
    # Return tuple as required by dcc.Graph.extendData
    return raw_line, (extend, [0], 1000)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=True)
