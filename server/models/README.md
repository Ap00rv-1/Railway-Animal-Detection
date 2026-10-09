# Model weights go here

Copy your trained YOLOv8-nano wildlife detection weights file to:

    server/models/wildlife_yolov8n.pt

This is the `best.pt` file produced by your training run (81.5% mAP50 on
your 9,302-image wildlife dataset). The server loads it lazily on the first
call to `/api/v1/infer` -- if the file isn't here yet, that endpoint returns
a clear 503 error instead of crashing, so the rest of the server still works
for testing via `/api/v1/detect` in the meantime.
