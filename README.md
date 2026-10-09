# Track Watch — Communication Layer (BTP Prototype)

This is the communication + priority + dashboard layer of the railway
animal-detection system: it takes the JSON your edge nodes (ESP32-CAM +
PIR + geophone + YOLOv8-nano) produce, decides how urgent each detection
is, and pushes color-coded alerts to a driver dashboard and a control room
dashboard in real time.

## Architecture

```
[Node 1] ─┐
[Node 2] ─┼─ HTTP POST /api/v1/detect ─> [FastAPI server] ─ WebSocket ─> [Driver dashboard]
[Node N] ─┘                                    │
                                                ├─ SQLite ──────────────> [Control room dashboard]
                                                └─ priority.py (scoring + ranking)
```

- **server/main.py** — FastAPI app. Ingests events, runs them through the
  priority engine, stores them, and broadcasts to both dashboards over
  WebSocket.
- **server/priority.py** — the scoring logic (position on track, animal
  weight class, sensor agreement, confidence, herd size) → RED/YELLOW/GREEN,
  plus the multi-node ranking used to pick what the driver sees first.
- **server/database.py** — SQLite storage for the control room's historical
  log and future path-tracing.
- **server/models.py** — Pydantic schema, mirrors `schema/detection_event_schema.json`.
- **dashboard/driver.html** — full-screen, high-contrast signal display for
  the cab. Big color state (green/yellow/red), plain-language action text,
  secondary alerts strip for other active nodes.
- **dashboard/control_room.html** — live event feed, per-node status grid,
  today's detection stats, historical table.
- **simulator/simulate_nodes.py** — fakes 4 nodes posting realistic JSON
  over HTTP so you can test/demo everything before hardware arrives.

## Running the prototype on campus WiFi

1. **Install dependencies** (on the machine that will act as the server —
   your laptop is fine):

   ```bash
   pip install -r requirements.txt
   ```

2. **Find your machine's LAN IP** (so phones/other laptops on the same WiFi
   can reach it):

   - Windows: `ipconfig` → look for IPv4 Address
   - Mac/Linux: `ifconfig` or `ip addr` → look for something like `192.168.x.x`

3. **Start the server:**

   ```bash
   cd server
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```

4. **Open the dashboards** from any device on the same WiFi (your phone can
   act as the "driver tablet"):

   - Driver: `http://<your-ip>:8000/dashboard/driver.html`
   - Control room: `http://<your-ip>:8000/dashboard/control_room.html`

5. **Feed it fake data** (no hardware needed yet):

   ```bash
   cd simulator
   python simulate_nodes.py --server http://<your-ip>:8000
   ```

   You should see the driver dashboard flip between green/yellow/red as
   simulated detections come in, and the control room table fill up live.

6. **API docs** are auto-generated at `http://<your-ip>:8000/docs` — useful
   for testing single POSTs by hand (e.g. via curl or Postman) before your
   ESP32 firmware is ready.

## Real node firmware (firmware/railway_node/)

The ESP32-CAM can't run YOLOv8-nano itself (no NPU, limited RAM) — so the
node's job is sense + capture + transmit only. Inference happens server-side,
using your already-trained model.

1. **Drop your trained weights** at `server/models/wildlife_yolov8n.pt`
   (see `server/models/README.md`). Until it's there, `/api/v1/infer`
   returns a clean 503 instead of crashing.
2. **Install the extra server dependency** (heavy, first install is slow):
   `pip install ultralytics --break-system-packages`
3. **Configure the node**: copy `firmware/railway_node/config.h.example` to
   `config.h` in the same folder, fill in your WiFi + laptop IP.
4. **Wire + flash** per the flashing-bridge instructions (Mega 2560 as
   USB-serial bridge if you don't have the shield yet). Board = AI Thinker
   ESP32-CAM, Upload Speed = 115200, Partition Scheme = Huge APP.
5. **Behavior once running**: PIR motion wakes the node -> captures a frame
   -> POSTs to `/api/v1/infer` for inference -> deep sleeps again. Every
   5 minutes (no motion needed) it also wakes just to send a heartbeat to
   `/api/v1/heartbeat`, so the control room can tell "asleep and fine" apart
   from "actually offline."

### Endpoints summary

| Endpoint | Used by | Payload |
|---|---|---|
| `POST /api/v1/detect` | `simulate_nodes.py`, any node doing its own inference | Pre-scored JSON |
| `POST /api/v1/infer` | Real ESP32-CAM node | multipart: image + PIR/geophone fields |
| `POST /api/v1/heartbeat` | Real ESP32-CAM node (timer wake) | form fields, no image |

All three funnel into the same `_process_event()` pipeline in `main.py` —
priority scoring, storage, and dashboard broadcast are identical regardless
of entry point.

### Known unvalidated piece

`server/inference.py`'s `_estimate_position()` guesses on-track vs
approaching vs near-track from bounding box size/position in frame. This is
a placeholder — it has not been checked against your actual camera's
mounting height/angle. Once a camera is physically mounted, compare its
guesses against known distances and retune the thresholds.

## Priority engine — what to tune as you get real data

All weights live at the top of `server/priority.py`:

- `POSITION_BASE_SCORE` — how much on-track vs approaching vs near-track matters
- `ANIMAL_WEIGHT_CLASS` — per-species risk multiplier (extend as your model's class list grows)
- `RED_THRESHOLD` / `YELLOW_THRESHOLD` — where the score cuts over
- `_sensor_agreement_factor()` — how much to trust single-sensor vs multi-sensor triggers
- `STALE_AFTER_SECONDS` — how long an alert stays "active" without a fresh update

The multi-node ranking (`rank_active_events`) currently assumes the train is
moving through ascending `location_km` markers and breaks ties by proximity.
That's a placeholder — once you have actual train position/direction (GPS or
track-circuit data), ranking should switch to "distance ahead of the train in
its direction of travel" rather than raw km-marker order.

## Known limitations (intentional, for a prototype)

- No auth/TLS — fine for a closed campus WiFi demo, **not** for anything
  connecting to real IR infrastructure.
- SQLite, not a production DB — swap for Postgres/Timescale for a real deployment.
- No real train GPS/speed integration yet — ranking uses static km markers.
- Path tracing across nodes (e.g. "same elephant seen at Node 2 then Node 3
  four minutes later") is not implemented yet; `database.get_node_history()`
  gives you the raw per-node timeline to build that correlation logic on top of.
