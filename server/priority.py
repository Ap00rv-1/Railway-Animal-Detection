"""
Priority engine.

Two jobs:
1. score_event()      -> turn one DetectionEventIn into a severity score + RED/YELLOW/GREEN
2. rank_active_events() -> across multiple simultaneously-active nodes, decide what
                           the driver dashboard shows as the PRIMARY alert vs the
                           secondary list.

All the weights below are named constants specifically so they're easy to find and
retune later — this is the part of the project with the most "research depth", so
expect to revisit these numbers once you have real sensor data instead of simulated
data. Nothing else in the codebase needs to change when you retune them.
"""

from datetime import datetime, timezone
from typing import List, Optional

from models import DetectionEventIn, DetectionEventOut, Position, SeverityLabel, EventType

# ---------------------------------------------------------------------------
# Tunable weights
# ---------------------------------------------------------------------------

# Base score by fused position of the animal relative to the rail.
POSITION_BASE_SCORE = {
    Position.on_track: 100.0,
    Position.approaching: 60.0,
    Position.near_track: 30.0,
    Position.unknown: 15.0,
}

# Animal weight class -> multiplier. Larger / herd animals cause more severe
# collisions and are harder to scare off, so they push the score up.
# Extend this dict as your dataset's class list grows.
ANIMAL_WEIGHT_CLASS = {
    "elephant": 1.6,
    "buffalo": 1.5,
    "camel": 1.4,
    "cattle": 1.3,
    "horse": 1.3,
    "nilgai": 1.2,
    "deer": 1.0,
    "boar": 1.0,
    "dog": 0.7,
    "goat": 0.7,
    "sheep": 0.7,
    "unknown": 1.0,  # fallback when the classifier is unsure of species
}

# Thresholds for the final 0-100+ severity score.
RED_THRESHOLD = 70.0
YELLOW_THRESHOLD = 30.0

# An event is considered "stale" (no longer trusted as a live alert) after
# this many seconds without a fresh detection/heartbeat from that node.
STALE_AFTER_SECONDS = 20.0


def _animal_weight(animal_type: Optional[str]) -> float:
    if not animal_type:
        return ANIMAL_WEIGHT_CLASS["unknown"]
    return ANIMAL_WEIGHT_CLASS.get(animal_type.lower(), ANIMAL_WEIGHT_CLASS["unknown"])


def _sensor_agreement_factor(event: DetectionEventIn) -> float:
    """
    Multi-sensor fusion trust factor.

    If camera + PIR + geophone all agree an animal is present, we trust the
    detection fully. If only one sensor fired, we damp the score so a single
    noisy PIR trigger (wind, small debris) doesn't escalate straight to RED.
    """
    signals = [
        event.sensors.camera.detected,
        event.sensors.pir.triggered,
        event.sensors.geophone.triggered,
    ]
    agreeing = sum(1 for s in signals if s)

    if agreeing >= 3:
        return 1.15   # all three agree -> slight trust boost
    if agreeing == 2:
        return 1.0    # two agree -> full trust, no boost/penalty
    if agreeing == 1:
        return 0.6    # only one sensor -> damp, likely to land in YELLOW not RED
    return 0.0        # nothing actually triggered (shouldn't normally reach scoring)


def _confidence_factor(event: DetectionEventIn) -> float:
    conf = event.sensors.camera.confidence or 0.0
    # Floor at 0.5 so a low camera confidence doesn't zero out a detection that
    # PIR + geophone both strongly corroborate.
    return max(0.5, conf) if event.sensors.camera.detected else 0.75


def _animal_count_factor(animal_count: int) -> float:
    # Herd crossings are riskier: more chances of an animal being on the rail
    # and more time needed to clear the track. Caps out so one huge herd
    # doesn't blow the scale to absurd numbers.
    return min(1.0 + 0.1 * max(0, animal_count - 1), 1.5)


def score_event(event: DetectionEventIn) -> float:
    """Returns a raw severity score, roughly in the 0-130 range."""
    if event.event_type == EventType.heartbeat:
        return 0.0

    base = POSITION_BASE_SCORE.get(event.position, POSITION_BASE_SCORE[Position.unknown])
    animal_type = event.sensors.camera.animal_type
    weight = _animal_weight(animal_type)
    agreement = _sensor_agreement_factor(event)
    confidence = _confidence_factor(event)
    count_factor = _animal_count_factor(event.animal_count)

    score = base * weight * agreement * confidence * count_factor
    return round(score, 2)


def classify(score: float) -> SeverityLabel:
    if score >= RED_THRESHOLD:
        return SeverityLabel.red
    if score >= YELLOW_THRESHOLD:
        return SeverityLabel.yellow
    return SeverityLabel.green


def recommended_action(label: SeverityLabel, event: DetectionEventIn) -> str:
    animal = (event.sensors.camera.animal_type or "animal").capitalize()
    if label == SeverityLabel.red:
        return f"{animal} on track — impact imminent. Sound horn/siren continuously. Prepare emergency brake if it does not clear."
    if label == SeverityLabel.yellow:
        return f"{animal} near track — reduce speed, sound horn, be ready to stop."
    return "Track clear."


def enrich(event: DetectionEventIn) -> DetectionEventOut:
    """Turn a raw inbound event into a fully-scored outbound event."""
    score = score_event(event)
    label = classify(score)
    action = recommended_action(label, event)
    return DetectionEventOut(
        **event.model_dump(),
        severity_score=score,
        severity_label=label,
        recommended_action=action,
    )


def _seconds_since(ts: datetime) -> float:
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds()


def rank_active_events(
    latest_by_node: List[DetectionEventOut],
) -> List[DetectionEventOut]:
    """
    Given the latest known event per node, drop stale ones and rank the rest.

    Ranking rule (this is the "multi-node priority" logic):
      1. Higher severity score wins the top slot — a RED elephant two nodes
         away still needs the driver's attention over a YELLOW dog right next
         to the current node.
      2. Ties broken by lower location_km distance from the *nearest* node
         (i.e. assume the train is moving forward through ascending km
         markers; nodes closer to the front of the list are "sooner").
    """
    fresh = [
        e for e in latest_by_node
        if e.event_type == EventType.detection and _seconds_since(e.received_at) <= STALE_AFTER_SECONDS
        and e.severity_label != SeverityLabel.green
    ]
    fresh.sort(key=lambda e: (-e.severity_score, e.location_km))
    return fresh
