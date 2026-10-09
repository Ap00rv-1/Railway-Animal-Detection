"""
SQLite storage for the control room.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

from models import DetectionEventOut

DB_PATH = Path(__file__).parent / "control_room.db"


def init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                received_at TEXT NOT NULL,
                location_km REAL NOT NULL,
                event_type TEXT NOT NULL,
                animal_type TEXT,
                confidence REAL,
                position TEXT,
                distance_from_rail_m REAL,
                animal_count INTEGER,
                severity_score REAL,
                severity_label TEXT,
                recommended_action TEXT,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_node ON events(node_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_event(event: DetectionEventOut):
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO events
            (event_id, node_id, timestamp, received_at, location_km, event_type,
             animal_type, confidence, position, distance_from_rail_m, animal_count,
             severity_score, severity_label, recommended_action, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.node_id,
                event.timestamp.isoformat(),
                event.received_at.isoformat(),
                event.location_km,
                event.event_type.value,
                event.sensors.camera.animal_type,
                event.sensors.camera.confidence,
                event.position.value,
                event.distance_from_rail_m,
                event.animal_count,
                event.severity_score,
                event.severity_label.value,
                event.recommended_action,
                event.model_dump_json(),
            ),
        )


def get_recent_events(limit: int = 200, node_id: Optional[str] = None) -> List[dict]:
    with _connect() as conn:
        if node_id:
            rows = conn.execute(
                "SELECT * FROM events WHERE node_id = ? ORDER BY timestamp DESC LIMIT ?",
                (node_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_node_history(node_id: str, limit: int = 500) -> List[dict]:
    """
    Used for future path-tracing: every event recorded at a given node,
    oldest first, so a simple 'animal X seen at node A then node B N minutes
    later' correlation can be built on top later.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE node_id = ? ORDER BY timestamp ASC LIMIT ?",
            (node_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_daily_stats() -> List[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT node_id, animal_type, COUNT(*) as count
            FROM events
            WHERE event_type = 'detection' AND date(timestamp) = date('now')
            GROUP BY node_id, animal_type
            ORDER BY count DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
