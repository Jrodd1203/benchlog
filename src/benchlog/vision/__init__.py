"""Vision pipeline: camera input and hole-occupancy detection for a BB830.

- `camera`: open a webcam / Continuity Camera and grab a frame once the board is still
- `capture`: detect, warp and register the board; classify each hole empty/occupied;
  baseline capture tool (`python -m benchlog.vision.capture`)
- `bb830_layout`: measured hole positions

Everything here stops at "which holes are occupied"; `benchlog.core.pairing` turns that into
observations. Nothing outside this package imports OpenCV.
"""
