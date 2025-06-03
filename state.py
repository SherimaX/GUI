from queue import Queue
import threading

# Queue for server-sent events (SSE) to push fresh samples to the browser.
# Only a single sample is stored; the browser keeps its own circular buffer.
event_q: Queue = Queue(maxsize=1)

# Limit concurrent SSE clients
MAX_CLIENTS = 5
_active_clients = 0
_client_lock = threading.Lock()
