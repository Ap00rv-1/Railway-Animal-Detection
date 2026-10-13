# Track Watch - FastAPI communication layer
# Place this file in the project ROOT (next to server/, dashboard/, requirements.txt)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System libs that OpenCV (pulled in by ultralytics) needs at import time
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch FIRST: the default build bundles CUDA and is several GB larger.
RUN pip install --no-cache-dir torch torchvision \
    --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Keep the same folder layout as your project so relative paths in main.py still work
COPY server/ server/
COPY dashboard/ dashboard/
COPY schema/ schema/

# SQLite file lives on a Docker volume so data survives container restarts
ENV SQLITE_PATH=/data/control_room.db
RUN mkdir -p /data

# Your imports are flat (from models import ...), so run from inside server/
WORKDIR /app/server
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
