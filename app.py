from threading import Thread, Event
import signal

from utils import load_config, is_host_reachable
from network import start_tcp_client, start_fake_data, request_shutdown
from dash_app import build_dash_app


if __name__ == "__main__":
    cfg = load_config()

    simulink_ok = is_host_reachable(cfg["tcp"]["host"])
    target_fn = start_tcp_client if simulink_ok else start_fake_data

    stop_event = Event()

    def _handle_signal(signum, frame):
        request_shutdown()
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    listener_t = Thread(target=target_fn, args=(cfg, stop_event))
    listener_t.start()

    dash_app = build_dash_app(cfg)
    host_addr = "192.168.7.15" if simulink_ok else "127.0.0.1"
    try:
        dash_app.run(host=host_addr, port=8050, debug=False, use_reloader=False, threaded=True)
    finally:
        _handle_signal(None, None)
        listener_t.join()
