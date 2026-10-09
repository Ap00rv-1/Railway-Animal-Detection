"""
Runs your trained YOLOv8-nano wildlife model on a frame captured by a node.

This exists because the ESP32-CAM can't run inference itself (see project
notes) -- so the model runs here, on whatever machine hosts this FastAPI
server. For the campus prototype that's your laptop; in a real deployment
this might move to a Raspberry Pi per node or a central inference server,
but nothing else in the pipeline needs to change when it does.

SETUP: copy your trained weights file to server/models/wildlife_yolov8n.pt
(see server/models/README.md).
"""

import io
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

MODEL_PATH = Path(__file__).parent / "models" / "wildlife_yolov8n.pt"
CONFIDENCE_FLOOR = 0.25  # detections below this are treated as "nothing found"

_model = None  # lazy-loaded so the server can start even before the weights file exists


class ModelNotLoadedError(Exception):
    pass


def _get_model():
    global _model
    if _model is None:
        if not MODEL_PATH.exists():
            raise ModelNotLoadedError(
                f"Model weights not found at {MODEL_PATH}. "
                "Copy your trained best.pt there -- see server/models/README.md."
            )
        from ultralytics import YOLO  # imported lazily: heavy import, only pay for it if needed
        _model = YOLO(str(MODEL_PATH))
    return _model


def infer(image_bytes: bytes) -> Tuple[Optional[str], float, Optional[list], str]:
    """
    Returns (animal_type, confidence, bbox_norm, position).
    If nothing is detected above CONFIDENCE_FLOOR, returns (None, 0.0, None, "unknown").

    bbox_norm is [x1, y1, x2, y2], each normalized 0-1 against frame width/height.
    """
    model = _get_model()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    results = model.predict(img, verbose=False)[0]

    if len(results.boxes) == 0:
        return None, 0.0, None, "unknown"

    best = max(results.boxes, key=lambda b: float(b.conf[0]))
    confidence = float(best.conf[0])
    if confidence < CONFIDENCE_FLOOR:
        return None, 0.0, None, "unknown"

    animal_type = model.names[int(best.cls[0])]
    x1, y1, x2, y2 = [float(v) for v in best.xyxy[0]]
    bbox_norm = [x1 / w, y1 / h, x2 / w, y2 / h]

    position = _estimate_position(bbox_norm)
    return animal_type, confidence, bbox_norm, position


def _estimate_position(bbox_norm: list) -> str:
    """
    PLACEHOLDER HEURISTIC -- not validated against real camera geometry yet.

    Assumption: a bounding box that's both tall (large fraction of frame
    height) AND low in the frame (near the bottom, i.e. close to the camera)
    corresponds to an animal physically close to the track. This depends
    entirely on where and at what angle the camera ends up mounted on the
    real node -- retune these thresholds once you have a camera mounted at
    the actual field angle and can compare against known distances.
    """
    x1, y1, x2, y2 = bbox_norm
    box_height = y2 - y1
    bottom_edge = y2

    if box_height > 0.45 and bottom_edge > 0.6:
        return "on_track"
    if box_height > 0.25:
        return "approaching"
    return "near_track"
