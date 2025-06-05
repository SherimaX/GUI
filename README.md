# Simulink UDP Web GUI

A lightweight, real-time dashboard built with Python that communicates with MATLAB/Simulink over UDP. The application streams live data coming from Simulink, visualises it in plots and numeric indicators, and allows you to send control commands back to Simulink through the same UDP channel.

---

## 1. Why this stack?

| Layer | Choice | Reason |
|-------|--------|--------|
| **Web framework** | **Dash** (`plotly-dash`) | Pure-Python, minimal boilerplate, works in the browser, supports live updating via callbacks, integrates Plotly charts out of the box. |
| **UI components** | `dash-bootstrap-components`, `dash-daq` | Off-the-shelf widgets for buttons, toggles, numeric LEDs, gauges, etc., plus Bootstrap styling for responsive layout. |
| **Realtime plotting** | `plotly` (comes with Dash) | High-performance WebGL rendering for streaming data. |
| **Networking** | Python standard library `asyncio` + `socket` | Non-blocking UDP client/server implementation without extra dependencies. |
| **Data handling** | Python built-ins (`collections`) | Fast buffering, filtering, and transformation of numeric data before visualisation. |
| **Packaging / runtime** | `uvicorn` (optional) or the built-in Dash dev server | Easy local development; `uvicorn` or `gunicorn` can be used for production deployment. |

> Feel free to replace Dash with alternatives such as **Streamlit**, **Panel**, or a custom **FastAPI + React** stack. Dash is chosen here because it keeps everything in pure Python and simplifies live callbacks.

---

## 2. Key Python packages

```
# core runtime
python>=3.9

# web dashboard
dash~=2.15  # web framework & core components
plotly~=5.18  # underlying charting engine
dash-bootstrap-components~=1.5  # nicer layout & styling
dash-daq~=0.5  # gauges, numeric LEDs, etc.


# async UDP networking (built-in)
asyncio  # part of the Python stdlib, no install needed

# production (optional)
uvicorn[standard]~=0.30
```

Save the list above as `requirements.txt` and run:

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

---

## 3. High-level architecture

```
┌─────────────────────┐     UDP     ┌──────────────────────┐
│  Simulink model     │◁──────────▷│  Python Dashboard     │
│  (UDP Send/Receive) │           │  (Dash + asyncio)     │
└─────────────────────┘           └──────────────────────┘
                                         ▲
                                         │ Dash callbacks update the DOM
                                         ▼
                             ┌──────────────────────────┐
                             │ Browser (HTML/JS/CSS)    │
                             └──────────────────────────┘
```

* **asyncio task**: Listens on a configurable UDP port, parses incoming bytes into numeric arrays, and stores them in a thread-safe queue.
* **Dash callback**: Polls the queue on a fixed interval (e.g. every 100 ms) and pushes the latest data to Plotly graphs and numeric readouts.
* **Sample rate**: The dashboard expects incoming packets at roughly 100&nbsp;Hz. Adjust the `SAMPLE_RATE_HZ` constant in `app.py` if your model uses a different rate.
* **Control panel**: UI widgets trigger another callback that sends UDP packets back to Simulink to adjust parameters or setpoint values.

---

## 4. Development workflow

1. Clone or copy this repo.
2. Create a virtual environment and install dependencies (see above).
3. Add your Simulink model with *UDP Send*/*Receive* blocks configured to the same IP/port.
4. Implement `udp_client.py` (receives data) and `udp_server.py` (optional, sends data).
5. Build your Dash layout in `app.py` with:
   - `dcc.Graph` components for plots
   - `dash_daq.LEDDisplay` or `html.Div` for numeric values
   - Buttons / sliders for control signals
6. Run the app:

   ```bash
   python app.py  # auto-reloads in development
   ```
7. Open `http://192.168.7.15:8050` in your browser. You should see live plots once
   Simulink starts streaming. If the board cannot be reached the app falls back to
   `http://127.0.0.1:8050` and generates fake data so you can exercise the
   dashboard offline.
8. Incoming data is kept in memory only—nothing is written to CSV.

---

## 5. Next steps

With the stack agreed, we can proceed in the following order:

1. **Set up the project skeleton**: folders `src/`, `app.py`, and the UDP helper modules.
2. **Implement UDP receiver**: Non-blocking listener storing data in an asyncio queue.
3. **Create initial Dash layout**: Static layout with placeholder plots and readouts.
4. **Wire up live updates**: Interval callback fetching data and updating the UI.
5. **Add control widgets**: Buttons/sliders linked to a UDP sender.
6. **Polish UI & deploy**: Styling, error handling, and optional packaging via Docker.

Ready when you are to move on to step 2 (creating the project skeleton). 