"""
Node simulator.

Mimics N edge nodes on the track, each periodically POSTing detection or
heartbeat JSON to the server over HTTP — exactly what the real ESP32-CAM
firmware will do once hardware arrives. This lets you test/demo the entire
pipeline (priority engine, driver dashboard, control room) right now, on
campus WiFi, with zero hardware.

Usage:
    python simulate_nodes.py --server http://192.168.1.42:8000

Find your server machine's LAN IP with `ipconfig` (Windows) / `ifconfig` or
`ip addr` (Mac/Linux), then point every device (including this simulator,
if run from another machine) at http://<that-ip>:8000
"""

import argparse
import random
import threading
import time
from datetime import datetime, timezone

import requests

ANIMALS = [
    ("elephant", 0.9),
    ("cattle", 0.6),
    ("nilgai", 0.5),
    ("dog", 0.3),
    ("boar", 0.4),
    ("deer", 0.4),
]

POSITIONS = ["on_track", "approaching", "near_track"]

NODES = [
    {"node_id": "NODE-01", "location_km": 241.2},
    {"node_id": "NODE-02", "location_km": 243.8},
    {"node_id": "NODE-03", "location_km": 245.6},
    {"node_id": "NODE-04", "location_km": 248.1},
]


def make_heartbeat(node):
    return {
        "node_id": node["node_id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "location_km": node["location_km"],
        "event_type": "heartbeat",
        "sensors": {
            "camera": {"detected": False},
            "pir": {"triggered": False},
            "geophone": {"triggered": False, "vibration_intensity": 0.0},
        },
        "position": "unknown",
        "distance_from_rail_m": None,
        "animal_count": 1,
    }


def make_detection(node):
    animal, base_conf = random.choice(ANIMALS)
    position = random.choice(POSITIONS)

    # Roughly simulate sensor agreement: on-track events are more likely to
    # trigger PIR + geophone together than a distant "near_track" sighting.
    pir_trig = random.random() < (0.9 if position == "on_track" else 0.5)
    geo_trig = random.random() < (0.85 if position == "on_track" else 0.35)

    return {
        "node_id": node["node_id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "location_km": node["location_km"],
        "event_type": "detection",
        "sensors": {
            "camera": {
                "detected": True,
                "animal_type": animal,
                "confidence": round(min(0.99, base_conf + random.uniform(0, 0.3)), 2),
            },
            "pir": {"triggered": pir_trig},
            "geophone": {
                "triggered": geo_trig,
                "vibration_intensity": round(random.uniform(0.3, 0.9), 2) if geo_trig else 0.0,
            },
        },
        "position": position,
        "distance_from_rail_m": round(random.uniform(0.0, 12.0), 1),
        "animal_count": random.choice([1, 1, 1, 2, 3]),  # occasional herd
    }


def run_node(node, server_url, detection_prob, interval, stop_event):
    session = requests.Session()
    while not stop_event.is_set():
        payload = make_detection(node) if random.random() < detection_prob else make_heartbeat(node)
        try:
            r = session.post(f"{server_url}/api/v1/detect", json=payload, timeout=3)
            label = r.json().get("severity_label", "?") if r.ok else f"HTTP {r.status_code}"
            tag = payload["event_type"].upper()
            print(f"[{node['node_id']}] {tag:9s} -> {label}")
        except requests.RequestException as e:
            print(f"[{node['node_id']}] ERROR posting to server: {e}")
        time.sleep(interval + random.uniform(-0.5, 0.5))


def main():
    parser = argparse.ArgumentParser(description="Simulate railway edge nodes over WiFi")
    parser.add_argument("--server", default="http://localhost:8000", help="Base URL of the FastAPI server")
    parser.add_argument("--interval", type=float, default=4.0, help="Seconds between posts per node")
    parser.add_argument("--detection-prob", type=float, default=0.35, help="Probability a post is a detection vs heartbeat")
    args = parser.parse_args()

    stop_event = threading.Event()
    threads = []
    for node in NODES:
        t = threading.Thread(
            target=run_node,
            args=(node, args.server, args.detection_prob, args.interval, stop_event),
            daemon=True,
        )
        threads.append(t)
        t.start()

    print(f"Simulating {len(NODES)} nodes -> {args.server}  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        print("\nStopped.")


if __name__ == "__main__":
    main()
