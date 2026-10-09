"""
Pydantic models for the railway animal-detection pipeline.

These mirror schema/detection_event_schema.json. Keeping them as Pydantic
models (rather than hand-parsing dicts) gives free request validation on
the FastAPI side and auto-generated OpenAPI docs at /docs.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    detection = "detection"
    heartbeat = "heartbeat"


class Position(str, Enum):
    on_track = "on_track"
    approaching = "approaching"
    near_track = "near_track"
    unknown = "unknown"


class SeverityLabel(str, Enum):
    red = "RED"
    yellow = "YELLOW"
    green = "GREEN"


class CameraReading(BaseModel):
    detected: bool = False
    animal_type: Optional[str] = None
    confidence: float = 0.0
    bbox: Optional[List[float]] = None


class PIRReading(BaseModel):
    triggered: bool = False


class GeophoneReading(BaseModel):
    triggered: bool = False
    vibration_intensity: float = 0.0


class SensorBundle(BaseModel):
    camera: CameraReading = Field(default_factory=CameraReading)
    pir: PIRReading = Field(default_factory=PIRReading)
    geophone: GeophoneReading = Field(default_factory=GeophoneReading)


class DetectionEventIn(BaseModel):
    """What a node sends to POST /api/v1/detect"""
    node_id: str
    timestamp: datetime
    location_km: float
    event_type: EventType = EventType.detection
    sensors: SensorBundle = Field(default_factory=SensorBundle)
    position: Position = Position.unknown
    distance_from_rail_m: Optional[float] = None
    animal_count: int = 1


class DetectionEventOut(DetectionEventIn):
    """What the server stores/broadcasts, with computed fields added."""
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity_score: float = 0.0
    severity_label: SeverityLabel = SeverityLabel.green
    recommended_action: str = ""
