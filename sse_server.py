from __future__ import annotations

"""sse_server.py – standalone Server-Sent Events endpoint that streams the
JSON packets coming from listener.EVENT_Q

Run this only *after* listener.py is running in another terminal.
Then test with:
   curl http://127.0.0.1:8051/events
You should see a continuous stream of lines beginning with "data:".
"""

import json
import time
from flask import Flask, Response
from listener import EVENT_Q, load_config, start_udp_listener
import threading
import collections

app = Flask(__name__)

@app.route("/events")
def sse():  # type: ignore
    def generate():
        while True:
            data = EVENT_Q.get()
            yield f"data:{json.dumps(data)}\n\n"
    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    # Expose on 8051 to keep port 8050 free for Dash frontend.
    # kick off the listener in the background
    cfg = load_config()
    t = threading.Thread(
        target=start_udp_listener,
        args=(cfg, collections.deque(maxlen=5000)),
        daemon=True,
    )
    t.start()

    app.run(host="0.0.0.0", port=8051, debug=True, threaded=True) 