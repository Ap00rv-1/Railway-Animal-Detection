# Track Watch — Complete Technical Reference

**Your BTP scope: the communication and alerting layer.** Everything in this
document is the piece between "a node detected something" and "a driver or
control room operator knows about it and can act." The detection model
itself (YOLOv8-nano, already trained by you separately at 81.5% mAP50) is a
different concern — this system consumes its output.

---

## 1. The system in one sentence

Edge nodes sense an animal → server scores how dangerous it is → dashboards
show that severity to a driver (in real time) and a control room (as a
permanent log).

## 2. Full architecture / data flow

```
┌─────────────┐     ┌─────────────┐
│  ESP32-CAM   │     │  simulate_   │   (two possible SOURCES of events —
│  node        │     │  nodes.py    │    real hardware, or the fake one
│ (PIR+camera) │     │  (fake node) │    you use for testing/demos)
└──────┬──────┘     └──────┬──────┘
       │ image + sensors   │ pre-scored JSON
       │ (multipart POST)  │ (JSON POST)
       ▼                   ▼
   /api/v1/infer      /api/v1/detect
       │                   │
       ▼                   │
  inference.py              │   (runs your trained YOLO model
  (server/)                 │    on the uploaded image)
       │                   │
       └─────────┬─────────┘
                  ▼
         _process_event()          <- ONE shared pipeline, in main.py
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
  priority.py  database.py  WebSocket
  (scores it)  (stores it)  broadcast
       │                       │
       ▼                       ├──► /ws/driver ──► driver.html
  severity_label                │
  RED/YELLOW/GREEN               └──► /ws/control ──► control_room.html
```

Two things enter the server (a real node's image, or a simulator's/test
JSON), but from the moment they hit `_process_event()`, they're
indistinguishable — same scoring, same storage, same broadcast. That
convergence point is the most important design decision in the whole
project: **it's why you can convincingly demo the entire system before
hardware is finished.** The dashboards have no idea whether the data behind
them came from a real PIR sensor or a Python script.

---

## 3. File-by-file walkthrough

### `schema/detection_event_schema.json`
The **contract**. Defines exactly what fields a detection event must have —
`node_id`, `timestamp`, `location_km`, sensor readings, position, animal
count. Every other file either produces something matching this shape or
consumes something matching this shape. Written first, deliberately, so
that node firmware and server code could be developed independently without
one team member breaking the other's assumptions.

### `server/models.py`
Turns the JSON schema into **Pydantic classes** — `DetectionEventIn` (what
comes in), `DetectionEventOut` (what goes out, with scoring fields added).
This is what gives you free validation: if a node sends `confidence: "high"`
instead of a number, FastAPI rejects it with a 422 before any of your logic
runs. You never wrote an `if` statement to check that — declaring the type
is the check.

Key classes:
- `SensorBundle` — holds camera/PIR/geophone readings as nested objects, not a flat dict, so each sensor's fields are typed independently
- `EventType` (detection/heartbeat), `Position` (on_track/approaching/near_track/unknown), `SeverityLabel` (RED/YELLOW/GREEN) — all `Enum`s, so a typo like `"aproaching"` fails validation instead of silently becoming an unrecognized string deep in your logic

### `server/priority.py` — the intellectual core of your BTP
This is where "how dangerous is this?" gets decided. Two jobs:

**`score_event()`** — turns one event into a number, multiplicatively:
```
score = position_base_score × animal_weight × sensor_agreement × confidence × animal_count_factor
```
- `position_base_score`: on_track=100, approaching=60, near_track=30 — physical proximity to the rail matters most
- `animal_weight`: elephant=1.6, dog=0.7 — bigger/harder-to-move animals are worse
- `sensor_agreement`: 1 sensor firing=0.6× (damped, likely noise), 2=1.0×, all 3=1.15× (trusted) — this is your defense against false positives from a single twitchy PIR
- `confidence`: floored at 0.5 so a low camera confidence doesn't erase what PIR+geophone already corroborated
- `animal_count_factor`: herds are riskier, caps at 1.5× so it doesn't blow up

**Why multiplicative, not additive?** So no single factor can force a RED alert alone. A confident camera detection of an elephant standing far from the track still won't out-score an on-track detection, because `position_base_score` dominates the product. Every weight is a named constant at the top of the file specifically so you can retune them once you have real field data — this was a deliberate decision to make the "depth" of the priority logic something you can defend and adjust, not a black box.

**`rank_active_events()`** — the multi-node problem. When several nodes report at once, this drops stale/GREEN events (nothing update in >20s doesn't compete for attention) and sorts what's left by severity first, then proximity. This is explicitly flagged in the code as a placeholder — it assumes ascending km-markers = direction of travel, which only holds until you have real train GPS/direction data.

### `server/database.py`
SQLite persistence — every event (including heartbeats) gets logged
permanently here, separate from the in-memory "latest state" the ranking
logic uses. **Why two separate stores?** They answer different questions at
different speeds: "what's happening right now" needs to be instant (memory),
"what happened historically" needs to survive a server restart (disk). This
is also where your **future path-tracing** hooks in —
`get_node_history(node_id)` returns every event at a node in chronological
order, the raw material for later correlating "same elephant seen at Node 2,
then Node 3, four minutes later."

### `server/inference.py`
Runs your trained YOLOv8-nano model on an uploaded image. **Lazy-loaded** —
the model only loads into memory the first time `/api/v1/infer` is actually
called, so the server starts fine even before you've copied your weights
file in (verified: returns a clean 503 instead of crashing). Also contains
`_estimate_position()` — a bounding-box-size heuristic guessing on-track vs
approaching vs near-track, explicitly marked as unvalidated until a camera
is physically mounted at its real angle.

### `server/main.py`
The FastAPI app — the glue. Three ingestion endpoints, all funneling into
`_process_event()`:

| Endpoint | Who calls it | What it carries |
|---|---|---|
| `POST /api/v1/detect` | `simulate_nodes.py`, any pre-scored source | Full JSON, already has animal_type etc. |
| `POST /api/v1/infer` | Real ESP32-CAM node | Raw image + PIR/geophone flags — server runs the model |
| `POST /api/v1/heartbeat` | Real node, timer wake | Just node_id + location, no image |

Also hosts two WebSocket endpoints (`/ws/driver`, `/ws/control`) that
dashboards connect to for live pushes, and query endpoints
(`/api/v1/events/recent`, `/events/active`, `/stats/daily`) that dashboards
use to load their initial state on page open.

**Why two separate WebSocket channels instead of one?** They need different
shapes of the same event. The driver needs one decision ("what's the #1
alert right now") — not a feed. The control room needs every event,
including heartbeats and low-severity ones, for the permanent record.
Sending everything to both would mean each dashboard doing work to filter
out what it doesn't need; separating at the source is simpler.

### `dashboard/driver.html`
Single-page, no framework — plain JS + CSS. Connects to `/ws/driver` on
load, also fetches `/api/v1/events/active` immediately (a bug we found and
fixed: without this, opening the dashboard after an event already fired
would wrongly show "clear" until the *next* event). Renders three lamp
circles (RED/YELLOW/GREEN, only one lit) and three action cards
(SLOW/BRAKE/HORN), with the mapping: RED→HORN blinks, YELLOW→SLOW+BRAKE
glow steady, GREEN→nothing lit. Deliberately icon/color-driven rather than
sentence-driven — a driver has a second, not a paragraph's worth of
reading time.

### `dashboard/control_room.html`
Also single-page vanilla JS, connects to `/ws/control`. Three panels: live
node-status grid (color dot per node, greys out if stale >30s), today's
detection stats by node/species, and a scrolling historical event table.
Unlike the driver view, this one is meant to be read, not glanced at — more
information density is correct here.

### `simulator/simulate_nodes.py`
Fakes 4 nodes posting realistic JSON (random animal, random position,
correlated sensor-trigger probability — e.g. on-track sightings are more
likely to trigger PIR+geophone together than a far-off one) via HTTP, on a
loop, with jitter. Exists so the entire pipeline is demoable before hardware
is ready — this is precisely why your demo video works even without a
flashed node.

### `firmware/railway_node/railway_node.ino`
The ESP32-CAM sketch (unflashed/untested as of now — written and desk-
reviewed, not yet verified on hardware). Two wake sources checked at the top
of `setup()` — because deep sleep restarts execution from scratch every
time, there's no persistent `loop()`:
- **PIR wake (motion)** → connect WiFi, init camera, capture a JPEG, hand-build
  a multipart HTTP POST to `/api/v1/infer`, then wait for the PIR to drop LOW
  before re-arming sleep (so the same motion event can't immediately
  re-trigger a wake)
- **Timer wake (every 5 min)** → just a lightweight POST to `/api/v1/heartbeat`,
  no camera — this exists so the control room can tell "asleep, but fine"
  apart from "actually offline," since a pure PIR-only node would look dead
  most of the time

`camera_pins.h` is the standard AI-Thinker pin map (unmodified from
Espressif's reference). `config.h.example` holds WiFi/server details you
fill in locally — kept separate from the sketch so credentials aren't
hardcoded into a file you might share or commit.

---

## 4. Key architectural decisions, and why

| Decision | Reasoning |
|---|---|
| FastAPI over Flask/Django | Async support (needed for WebSocket + HTTP concurrently), automatic validation from Pydantic types, auto-generated `/docs` for testing |
| Inference on server, not on-node | ESP32-CAM has no NPU and limited RAM — running YOLOv8-nano there isn't a "try harder" problem, it's a hardware ceiling |
| Multiplicative scoring, not additive or rule-based if/else | No single weak signal can force an alert alone; every factor is independently tunable without restructuring logic |
| SQLite for prototype | Zero setup, fine for single-machine campus demo; explicitly flagged as swappable for Postgres in a real deployment |
| Two WebSocket channels | Driver and control room need fundamentally different shapes of the same underlying data |
| Deep sleep + dual wake source | Battery-realistic (you have li-ion cells), but pure PIR-only sleep would make the control room unable to distinguish "idle" from "dead" — timer heartbeat fixes that |
| Shared `_process_event()` pipeline | Guarantees scoring/storage/broadcast logic exists in exactly one place, regardless of whether the event came from real hardware, an image needing inference, or a test script |

## 5. What's real vs. placeholder — know this cold for your viva

Be ready to name these precisely; a professor pushing on any of them should
get a confident, specific answer, not a vague one:

- **Scoring thresholds (70/RED, 30/YELLOW) and all weights in `priority.py`**
  — arbitrary starting points, not derived from data. You have no real
  sensor data yet to calibrate against.
- **`_estimate_position()` in `inference.py`** — bounding-box heuristic,
  unvalidated against real camera mounting geometry.
- **Multi-node ranking by km-marker** — assumes ascending km = direction of
  travel; no real train GPS/direction integrated yet.
- **Firmware** — written, not yet flashed or field-tested.
- **No auth/TLS anywhere** — fine for a closed campus WiFi demo, explicitly
  not fine for real IR infrastructure.

Naming these yourself, unprompted, in a viva is far stronger than having
them found for you.
