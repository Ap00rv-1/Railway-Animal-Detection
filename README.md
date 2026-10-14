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
                                                ├─ PostgreSQL ──────────> [Control room dashboard]
                                                └─ priority.py (scoring + ranking)
```

- **server/main.py** — FastAPI app. Ingests events, runs them through the
  priority engine, stores them, and broadcasts to both dashboards over
  WebSocket.
- **server/priority.py** — the scoring logic (position on track, animal
  weight class, sensor agreement, confidence, herd size) → RED/YELLOW/GREEN,
  plus the multi-node ranking used to pick what the driver sees first.
- **server/database_pg.py** — PostgreSQL storage (asyncpg connection pool)
  for the control room's historical log and future path-tracing.
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

**Set up PostgreSQL.** Create a database (e.g. `trackwatch`) and a `.env`
file in `server/`:

DB_HOST=localhost
DB_PORT=5432
DB_NAME=trackwatch
DB_USER=...
DB_PASSWORD=...

The `events` table and indexes are created automatically on server startup.

3. **Start the server:**

   ```bash
   cd server
   python -m uvicorn main:app --host 0.0.0.0 --port 8000
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
- No real train GPS/speed integration yet — ranking uses static km markers.
- Path tracing across nodes (e.g. "same elephant seen at Node 2 then Node 3
  four minutes later") is not implemented yet; `database_pg.get_node_history()`
  gives you the raw per-node timeline to build that correlation logic on top of.

- Postgres credentials live in .env; keep it out of git.



# Track Watch — Communication Layer (BTP Prototype)

The communication, priority and dashboard layer of the railway animal-detection
system. It takes the events your edge nodes (ESP32-CAM + PIR + geophone) produce,
runs YOLOv8-nano inference on camera frames, scores how urgent each detection is,
and pushes colour-coded alerts to a driver dashboard and a control room dashboard
in real time.

## Architecture

```
[Node 1] ─┐
[Node 2] ─┼─ HTTP POST ─> [FastAPI server] ── WebSocket ──> [Driver dashboard]
[Node N] ─┘   /detect          │
              /infer           ├── PostgreSQL ────────────> [Control room dashboard]
              /heartbeat       └── priority.py (scoring + ranking)
```

The whole stack (FastAPI app + PostgreSQL) runs with Docker Compose, locally or on
an AWS EC2 instance.

## Project layout

| Path | Purpose |
|---|---|
| `server/main.py` | FastAPI app. Ingests events, scores them, stores them, broadcasts to both dashboards over WebSocket. |
| `server/priority.py` | Scoring logic (track position, animal weight class, sensor agreement, confidence, herd size) → RED/YELLOW/GREEN, plus multi-node ranking. |
| `server/database_pg.py` | PostgreSQL storage (asyncpg connection pool): schema, `save_event`, and the query functions for the control room. |
| `server/inference.py` | Loads the YOLOv8-nano weights and runs detection on uploaded frames. |
| `server/models.py` | Pydantic schema, mirrors `schema/detection_event_schema.json`. |
| `dashboard/driver.html` | Full-screen, high-contrast signal display for the cab. |
| `dashboard/control_room.html` | Live event feed, per-node status grid, today's stats, historical table. |
| `simulator/simulate_nodes.py` | Fakes 4 nodes posting realistic events, for testing before hardware is ready. |
| `firmware/railway_node/` | ESP32-CAM node firmware. |
| `Dockerfile`, `docker-compose.yml` | Container setup for the app and its database. |

## Configuration (`.env`)

Create a `.env` file in the project root. It is read by Docker Compose at runtime
and is never copied into the image or committed to git.

```
DB_HOST=db
DB_PORT=5432
DB_NAME=trackwatch
DB_USER=postgres
DB_PASSWORD=<strong password>
```

- Compose overrides `DB_HOST` and `DB_PORT` for the app container, so the database is always reached at `db:5432` inside Docker.
- Use letters and digits in the password (no `$`, quotes or spaces), with no quotes around the value. `openssl rand -hex 16` generates a suitable one.
- Postgres only reads the password when it first creates the data volume. Changing it later requires `docker compose down -v`, which **wipes the stored data**.

## Running locally with Docker

1. Install Docker Desktop and make sure it is running.
2. Create `.env` as above.
3. From the project root:

   ```bash
   docker compose up --build
   ```

   The first build is slow (CPU-only PyTorch and ultralytics).

4. Check it works:

   - `http://localhost:8000/api/v1/health`
   - `http://localhost:8000/api/v1/pg-test` → `{"postgres_says": 1}`
   - Driver dashboard: `http://localhost:8000/dashboard/driver.html`
   - Control room: `http://localhost:8000/dashboard/control_room.html`
   - API docs: `http://localhost:8000/docs`

5. Feed it fake data:

   ```bash
   cd simulator
   python simulate_nodes.py --server http://localhost:8000
   ```

6. Confirm rows are being stored:

   ```bash
   docker compose exec db psql -U <DB_USER> -d <DB_NAME> -c "SELECT count(*) FROM events;"
   ```

Stop with `docker compose down` (data is kept in the `pgdata` volume). Add `-v` only if you want to delete the data.

### Running without Docker

Install Python dependencies and have a local PostgreSQL running with the same `DB_*` values in `server/.env`, using `DB_HOST=localhost`:

```bash
pip install -r requirements.txt
cd server
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

On Windows, `python -m uvicorn` avoids the Application Control block that can stop `uvicorn.exe` from running.

## Model weights

The ESP32-CAM cannot run YOLOv8-nano itself (no NPU, limited RAM), so nodes only sense, capture and transmit. Inference runs on the server.

Place the trained weights at `server/models/wildlife_yolov8n.pt`. The folder is mounted into the container read-only, so the weights are not baked into the image and no rebuild is needed:

```bash
docker compose restart trackwatch
```

Until the file is present, `POST /api/v1/infer` returns a clean 503 instead of crashing.

## Deploying on AWS EC2

Tested on a `t3.small` (2 vCPU, 2 GiB) running Ubuntu 24.04. Smaller instances (1 GiB) are too tight for the image build.

1. **Launch the instance** with at least 30 GiB of gp3 storage and a key pair (`.pem`).
2. **Security group:**
   - SSH (22) from your own IP only.
   - Custom TCP 8000 from your own IP, plus the public IP of any network the ESP32 nodes will use.
   - Do not open port 8000 to the whole internet while the API has no authentication.
3. **Attach an Elastic IP** so the public address does not change when the instance is stopped and started.
4. **Connect and install tools:**

   ```bash
   ssh -i trackwatch-key.pem ubuntu@<elastic-ip>
   sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
   sudo usermod -aG docker $USER
   ```

   Log out and back in so the Docker group takes effect.

5. **Add swap** (protects the image build from running out of memory):

   ```bash
   sudo fallocate -l 4G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

6. **Deploy:**

   ```bash
   git clone https://github.com/Ap00rv-1/Railway-Animal-Detection && cd Railway-Animal-Detection
   nano .env                      # new strong DB_PASSWORD
   docker compose up -d --build
   ```

7. **Copy the model weights** from your machine:

   ```bash
   scp -i trackwatch-key.pem server/models/wildlife_yolov8n.pt \
       ubuntu@13.55.4.254:~/Railway-Animal-Detection/server/models/
   ```

8. **Verify:** `curl http://localhost:8000/api/v1/pg-test` on the server, then open `http://13.55.4.254:8000/dashboard/driver.html` from your machine and run the simulator with `--server http://13.55.4.254:8000`.

Useful commands on the server:

```bash
docker compose ps
docker compose logs -f trackwatch
docker compose restart trackwatch
```

Stop the instance (not terminate) when it is not being used. The disk and Elastic IP are kept, and you only pay for storage and the IP while it is stopped.

## ESP32-CAM node (to be flashed)

The node firmware lives in `firmware/railway_node/`. Not yet flashed; steps for when the hardware is ready:

1. **Configure:** copy `config.h.example` to `config.h` in the same folder and fill in:
   - the WiFi SSID and password
   - the server host: the EC2 **13.55.4.254** (or your laptop's LAN IP for local testing), port `8000`

   `config.h` holds credentials and is git-ignored; never commit it.
2. **Use plain HTTP.** The server has no TLS yet, so the node must call `http://`, not `https://`.
3. **Allow the node's network in the security group.** If the node connects through a different network than your laptop (for example a phone hotspot), add that network's public IP as a port 8000 rule. Hotspot IPs change, so a node that suddenly cannot connect is usually this.
4. **Flash:** board = AI Thinker ESP32-CAM, upload speed 115200, partition scheme "Huge APP". If you do not have the programmer shield, use an Arduino Mega 2560 as a USB-serial bridge.
5. **Behaviour once running:**
   - PIR motion wakes the node → it captures a frame → POSTs to `/api/v1/infer` → deep sleeps again.
   - Every 5 minutes it also wakes just to POST a heartbeat to `/api/v1/heartbeat`, so the control room can tell "asleep and fine" from "actually offline".
6. **Test:** trigger the PIR and watch `docker compose logs -f trackwatch` and the dashboards for the event.

## API endpoints

| Endpoint | Used by | Payload |
|---|---|---|
| `POST /api/v1/detect` | `simulate_nodes.py`, any node running its own inference | Pre-scored JSON |
| `POST /api/v1/infer` | ESP32-CAM node | multipart: image + PIR/geophone fields |
| `POST /api/v1/heartbeat` | ESP32-CAM node (timer wake) | form fields, no image |
| `GET /api/v1/events/recent` | Control room | `limit`, optional `node_id` |
| `GET /api/v1/events/active` | Control room | currently active, ranked alerts |
| `GET /api/v1/nodes/{node_id}/history` | Path tracing (future) | oldest-first events for one node |
| `GET /api/v1/stats/daily` | Control room | today's detections by node and animal |
| `GET /api/v1/health` | Monitoring | status and nodes seen |
| `GET /api/v1/pg-test` | Debugging | confirms the database connection |
| `WS /ws/driver`, `WS /ws/control` | Dashboards | live updates |

All three ingest endpoints funnel into the same `_process_event()` pipeline in `main.py`: scoring, storage and broadcast are identical regardless of entry point.

## Data storage

Events are stored in PostgreSQL (`events` table, created automatically on server startup). A few behaviours to know:

- Timestamps are `TIMESTAMPTZ`. Nodes must send timezone-aware timestamps; naive ones can be silently shifted.
- "Today" in `/api/v1/stats/daily` uses the `Asia/Kolkata` day.
- `raw_json` holds the full event as JSON.
- If a database write fails, the ingest request fails and nothing is broadcast, so dashboards never show an alert that was not stored.

## Priority engine — what to tune as you get real data

All weights live at the top of `server/priority.py`:

- `POSITION_BASE_SCORE`: how much on-track vs approaching vs near-track matters
- `ANIMAL_WEIGHT_CLASS`: per-species risk multiplier (extend as the model's class list grows)
- `RED_THRESHOLD` / `YELLOW_THRESHOLD`: where the score cuts over
- `_sensor_agreement_factor()`: how much to trust single-sensor vs multi-sensor triggers
- `STALE_AFTER_SECONDS`: how long an alert stays "active" without a fresh update

The multi-node ranking (`rank_active_events`) currently assumes the train moves through ascending `location_km` markers and breaks ties by proximity. This is a placeholder: once real train position and direction are available (GPS or track-circuit data), ranking should switch to distance ahead of the train in its direction of travel.

## Known unvalidated piece

`server/inference.py`'s `_estimate_position()` guesses on-track / approaching / near-track from bounding-box size and position in the frame. It has not been checked against the real camera's mounting height and angle. Once a camera is mounted, compare its guesses against known distances and retune the thresholds.

## Known limitations (intentional, for a prototype)

- **No authentication and no TLS.** The AWS security group is the only protection. Add HTTPS (for example a Caddy reverse proxy) and authentication on the ingest endpoints before any real deployment.
- `CORS` is open to all origins; restrict it for real use.
- The database runs as a container on the same instance. For production, move to a managed database (for example RDS) with backups.
- Latest-alert state (`latest_by_node`) is held in memory and is lost on restart.
- No real train GPS/speed integration yet; ranking uses static km markers.
- Path tracing across nodes (for example the same elephant at Node 2 then Node 3 minutes later) is not implemented; `database_pg.get_node_history()` provides the per-node timeline to build it on.
