You are working as Person 4 (Camera / CV) on benchlog. Read CLAUDE.md, `src/benchlog/core/models.py`,
`src/benchlog/core/board.py`, and `examples/` first. You own `src/benchlog/vision/`, `examples/photos/`,
and the `/api/scan` endpoint in `src/benchlog/server/app.py`. Nobody else waits on you: your only output
is `Observation` objects, and manual wiring works without you. But the live demo's scan step is yours.

Setup: a fixed webcam over the board, and 4 ArUco markers (`cv2.aruco`, `DICT_4X4_50`) at the board's
corners. Install with `pip install -e ".[vision,server,dev]"`.

Tasks, in order. Propose a short plan first, then do one task at a time and stop after each so I can review:

1. **Capture + photos**: a small script to grab frames from the webcam, and a marker sheet to print. Help me
   collect `examples/photos/`: empty board, working circuit, moved wire, hands in frame, and 2 lighting
   setups. Real photos come before any algorithm work.
2. **Calibration**: detect the markers, warp to a top-down view, and map every bb830 hole name to a pixel
   position. Output a debug image with the hole grid drawn over the photo so we can check alignment by eye.
   Save calibration to a file so it's done once.
3. **Hole occupancy**: for each hole, compare a small patch in the before and after images and decide
   empty vs occupied. Handle the ESP32 covering columns C–H in rows 1–15. Keep it simple (patch statistics
   or a threshold) before reaching for anything learned. Reject scans with hands in frame.
4. **Observations**: turn changed holes into `Observation`s (added/removed/moved), pairing ends by wire color
   and by the last accepted circuit (a hole that emptied plus one that filled at the same color is likely a
   moved end). Put anything ambiguous in `uncertain_holes` with lower confidence.
   Target: the empty→working and working→moved photo pairs give the right observations; the second must
   match `examples/observations/moved-wire.json`.
5. **Endpoint**: `POST /api/scan` captures a frame (or accepts an uploaded image for testing), diffs it
   against the last accepted state, and returns observations. Also add a "load sample scan" path that
   returns the example observation, as the demo fallback.

Keep OpenCV imports inside `vision/` only. Add tests that run on the saved photos, and keep `pytest` green.
