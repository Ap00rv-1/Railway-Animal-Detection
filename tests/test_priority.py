from datetime import datetime, timezone

import pytest

import priority
from models import (
    CameraReading,
    DetectionEventIn,
    EventType,
    GeophoneReading,
    PIRReading,
    Position,
    SensorBundle,
    SeverityLabel,
)


def make_event(animal="elephant", position=Position.on_track, camera=True,
               pir=True, geophone=True, confidence=0.9, count=1,
               event_type=EventType.detection):
    """Builds a test event, so each test only states what it cares about."""
    return DetectionEventIn(
        node_id="NODE-TEST",
        timestamp=datetime.now(timezone.utc),
        location_km=240.0,
        event_type=event_type,
        sensors=SensorBundle(
            camera=CameraReading(detected=camera, animal_type=animal, confidence=confidence),
            pir=PIRReading(triggered=pir),
            geophone=GeophoneReading(triggered=geophone),
        ),
        position=position,
        animal_count=count,
    )


def test_heartbeat_scores_zero():
    assert priority.score_event(make_event(event_type=EventType.heartbeat)) == 0.0


def test_elephant_on_track_all_sensors_is_red():
    score = priority.score_event(make_event())
    assert score == pytest.approx(165.6)  # 100 * 1.6 * 1.15 * 0.9
    assert priority.classify(score) == SeverityLabel.red


def test_dog_near_track_single_sensor_is_green():
    event = make_event(animal="dog", position=Position.near_track,
                       pir=False, geophone=False, confidence=0.8)
    score = priority.score_event(event)
    assert score == pytest.approx(10.08)  # 30 * 0.7 * 0.6 * 0.8
    assert priority.classify(score) == SeverityLabel.green


def test_more_agreeing_sensors_raise_the_score():
    all_three = priority.score_event(make_event())
    camera_only = priority.score_event(make_event(pir=False, geophone=False))
    assert all_three > camera_only


def test_bigger_animal_scores_higher_than_smaller():
    elephant = priority.score_event(make_event(animal="elephant"))
    dog = priority.score_event(make_event(animal="dog"))
    assert elephant > dog


def test_unknown_species_falls_back_to_default_weight():
    assert priority._animal_weight("unicorn") == 1.0
    assert priority._animal_weight(None) == 1.0
    assert priority._animal_weight("Elephant") == 1.6  # case-insensitive


def test_herd_factor_is_capped():
    assert priority._animal_count_factor(1) == 1.0
    assert priority._animal_count_factor(100) == 1.5


@pytest.mark.parametrize("score, expected", [
    (priority.RED_THRESHOLD, SeverityLabel.red),
    (priority.RED_THRESHOLD - 0.01, SeverityLabel.yellow),
    (priority.YELLOW_THRESHOLD, SeverityLabel.yellow),
    (priority.YELLOW_THRESHOLD - 0.01, SeverityLabel.green),
])
def test_classify_thresholds(score, expected):
    assert priority.classify(score) == expected