"""
Central server for the railway animal-detection communication pipeline.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    POST /api/v1/detect            <- nodes post detection/heartbeat events here
    GET  /api/v1/events/recent      -> control room: recent event log
    GET  /api/v1/events/active      -> currently active, ranked alerts
    GET  /api/v1/stats/daily        -> control room: today's counts by node/animal
    WS   /ws/driver                 <- driver dashboard subscribes here
    WS   /ws/control                <- control room dashboard subscribes here

Everything is served over plain HTTP/WS on the local WiFi network — no auth,
no TLS. That's fine for a campus prototype; note this loudly in the README
as something to fix before any real deployment.
"""

from datetime import datetime, timezone
from typing import Dict, List

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import database_pg
import database
import inference
import priority
from models import (
    CameraReading,
    DetectionEventIn,
    DetectionEventOut,
    EventType,
    GeophoneReading,
    PIRReading,
    Position,
    SensorBundle,
)

app = FastAPI(title="Railway Animal Detection — Communication Layer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # prototype only — lock this down for real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# Latest known event per node — this is what the priority ranking works off.
latest_by_node: Dict[str, DetectionEventOut] = {}


class ConnectionManager:
    def __init__(self):
        self.driver_sockets: List[WebSocket] = []
        self.control_sockets: List[WebSocket] = []

    async def connect(self, ws: WebSocket, channel: str):
        await ws.accept()
        (self.driver_sockets if channel == "driver" else self.control_sockets).append(ws)

    def disconnect(self, ws: WebSocket, channel: str):
        pool = self.driver_sockets if channel == "driver" else self.control_sockets
        if ws in pool:
            pool.remove(ws)

    async def broadcast(self, channel: str, payload: dict):
        pool = self.driver_sockets if channel == "driver" else self.control_sockets
        dead = []
        for ws in pool:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            pool.remove(ws)


manager = ConnectionManager()


@app.on_event("startup")
async def startup():
    database.init_db()
    app.state.db_pool = await database_pg.init_pool()
    await database_pg.init_schema(app.state.db_pool) 


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

async def _process_event(event_in: DetectionEventIn) -> DetectionEventOut:
    """
    Shared pipeline for every event, regardless of where it came from
    (a pre-scored JSON POST from /detect, or an inferred result from
    /infer). Scoring, storage, ranking, and broadcast all happen exactly
    once, here.
    """
    event = priority.enrich(event_in)

    latest_by_node[event.node_id] = event
    database.save_event(event)

    active_ranked = priority.rank_active_events(list(latest_by_node.values()))
    top_alert = active_ranked[0] if active_ranked else None

    driver_payload = {
        "type": "alert_update",
        "top_alert": top_alert.model_dump(mode="json") if top_alert else None,
        "secondary_alerts": [e.model_dump(mode="json") for e in active_ranked[1:5]],
    }
    control_payload = {
        "type": "new_event",
        "event": event.model_dump(mode="json"),
    }

    await manager.broadcast("driver", driver_payload)
    await manager.broadcast("control", control_payload)

    return event


@app.post("/api/v1/detect")
async def ingest_detection(event_in: DetectionEventIn):
    """
    Accepts an already-scored JSON event. Used by simulate_nodes.py and by
    any future node that runs its own inference locally.
    """
    event = await _process_event(event_in)
    return {
        "status": "ok",
        "event_id": event.event_id,
        "severity_label": event.severity_label,
        "severity_score": event.severity_score,
    }


@app.post("/api/v1/infer")
async def ingest_image(
    node_id: str = Form(...),
    location_km: float = Form(...),
    pir_triggered: bool = Form(...),
    geophone_triggered: bool = Form(False),
    geophone_intensity: float = Form(0.0),
    animal_count: int = Form(1),
    image: UploadFile = File(...),
):
    """
    Accepts a raw image + sensor metadata from a real ESP32-CAM node.
    Runs the trained YOLOv8-nano model on it, then feeds the result through
    the exact same pipeline as /detect.

    If the model weights haven't been dropped into server/models/ yet,
    this returns a clear 503 instead of crashing the server.
    """
    image_bytes = await image.read()

    try:
        animal_type, confidence, bbox, position = inference.infer(image_bytes)
    except inference.ModelNotLoadedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    event_type = EventType.detection if animal_type else EventType.heartbeat

    event_in = DetectionEventIn(
        node_id=node_id,
        timestamp=datetime.now(timezone.utc),
        location_km=location_km,
        event_type=event_type,
        sensors=SensorBundle(
            camera=CameraReading(
                detected=animal_type is not None,
                animal_type=animal_type,
                confidence=confidence,
                bbox=bbox,
            ),
            pir=PIRReading(triggered=pir_triggered),
            geophone=GeophoneReading(triggered=geophone_triggered, vibration_intensity=geophone_intensity),
        ),
        position=Position(position),
        distance_from_rail_m=None,
        animal_count=animal_count,
    )

    event = await _process_event(event_in)
    return {
        "status": "ok",
        "event_id": event.event_id,
        "animal_type": animal_type,
        "severity_label": event.severity_label,
        "severity_score": event.severity_score,
    }


@app.post("/api/v1/heartbeat")
async def ingest_heartbeat(node_id: str = Form(...), location_km: float = Form(...)):
    """
    Lightweight endpoint for the timer-wake heartbeat (no image, no inference)
    -- keeps the control room's node-status grid meaningful for a node that's
    normally asleep between PIR triggers.
    """
    event_in = DetectionEventIn(
        node_id=node_id,
        timestamp=datetime.now(timezone.utc),
        location_km=location_km,
        event_type=EventType.heartbeat,
    )
    event = await _process_event(event_in)
    return {"status": "ok", "event_id": event.event_id}


# ---------------------------------------------------------------------------
# Query endpoints (control room dashboard uses these on load / refresh)
# ---------------------------------------------------------------------------

@app.get("/api/v1/pg-test")
async def pg_test(conn = Depends(database_pg.get_db_connection)):
    result = await conn.fetchval("SELECT 1")
    return {"postgres_says": result}


@app.get("/api/v1/events/recent")
def recent_events(limit: int = 200, node_id: str = None):
    return database.get_recent_events(limit=limit, node_id=node_id)


@app.get("/api/v1/events/active")
def active_events():
    active_ranked = priority.rank_active_events(list(latest_by_node.values()))
    return [e.model_dump(mode="json") for e in active_ranked]


@app.get("/api/v1/nodes/{node_id}/history")
def node_history(node_id: str, limit: int = 500):
    return database.get_node_history(node_id, limit=limit)


@app.get("/api/v1/stats/daily")
def daily_stats():
    return database.get_daily_stats()


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "nodes_seen": list(latest_by_node.keys())}


# ---------------------------------------------------------------------------
# WebSockets
# ---------------------------------------------------------------------------

@app.websocket("/ws/driver")
async def ws_driver(websocket: WebSocket):
    await manager.connect(websocket, "driver")
    try:
        while True:
            await websocket.receive_text()  # keepalive pings from client, ignored
    except WebSocketDisconnect:
        manager.disconnect(websocket, "driver")


@app.websocket("/ws/control")
async def ws_control(websocket: WebSocket):
    await manager.connect(websocket, "control")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, "control")


# Serve the dashboard HTML files directly for convenience during the prototype
app.mount("/dashboard", StaticFiles(directory="../dashboard", html=True), name="dashboard")
