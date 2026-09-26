"""Live camera input: a webcam or an iPhone via Continuity Camera, both plain OpenCV devices.

The camera is mounted over the bench; a scan waits until the board is in view and nothing is
moving (no hands, camera settled), then hands that frame to `capture.analyse_image`.

    open_camera(index)            -> cv2.VideoCapture at 1920x1080, with setup hints on failure
    read_frame(cap)               -> next landscape frame, tolerating Continuity Camera hiccups
    grab_steady_frame(cap, ...)   -> first frame after the board has been still for `steady_for` s
    list_cameras()                -> which indices open, and at what resolution
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from benchlog.vision.capture import MAX_CONSECUTIVE_READ_FAILURES, detect_board_downscaled, ensure_landscape

CAPTURE_W, CAPTURE_H = 1920, 1080

# Motion is judged on a small blurred copy of each frame: cheap, and immune to sensor noise.
MOTION_WIDTH = 320
MAX_CORNER_JITTER_PX = 2.0  # in full-resolution pixels
MAX_BOARD_MOTION = 4.0  # mean absolute gray-level change over the board; a hand is far above this

SETUP_HINTS = (
    "  - try another camera index (`benchlog camera list`); a built-in webcam often takes 0\n"
    "  - macOS: System Settings → Privacy & Security → Camera, allow Terminal (or your IDE)\n"
    "  - Continuity Camera: iPhone locked, still, near the Mac, same Apple ID, Wi-Fi + Bluetooth on\n"
    "  - make sure no other app is using the camera"
)


class CameraError(RuntimeError):
    pass


def open_camera(index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        raise CameraError(f"can't open camera {index}. Things to try:\n{SETUP_HINTS}")
    # Best effort: drivers pick the closest mode they support. The OpenCV default is often 640x480,
    # far too coarse for 830 holes.
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_H)
    return cap


def read_frame(cap: cv2.VideoCapture) -> np.ndarray:
    """The next frame, rotated to landscape. Skips transient read failures."""
    for _ in range(MAX_CONSECUTIVE_READ_FAILURES):
        ok, frame = cap.read()
        if ok and frame is not None:
            return ensure_landscape(frame)
    raise CameraError(f"the camera stopped delivering frames. Things to try:\n{SETUP_HINTS}")


def _motion_image(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (MOTION_WIDTH, max(1, round(h * MOTION_WIDTH / w))), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float32)


def board_motion(prev: np.ndarray, curr: np.ndarray, corners: np.ndarray, frame_width: int) -> float:
    """Mean gray-level change inside the board's bounding box between two motion images."""
    scale = curr.shape[1] / frame_width
    x0, y0 = np.floor(corners.min(axis=0) * scale).astype(int)
    x1, y1 = np.ceil(corners.max(axis=0) * scale).astype(int)
    h, w = curr.shape
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return float("inf")
    return float(np.mean(np.abs(curr[y0:y1, x0:x1] - prev[y0:y1, x0:x1])))


@dataclass
class _Steadiness:
    """Tracks how long the board has been in view with nothing moving."""

    steady_since: float | None = None
    last_corners: np.ndarray | None = None
    last_motion_image: np.ndarray | None = None
    seen_board: bool = False

    def update(self, frame: np.ndarray, corners: np.ndarray | None, now: float) -> float:
        """Seconds the board has been steady as of this frame (0 if it isn't)."""
        motion_image = _motion_image(frame)
        steady = False
        if corners is not None:
            self.seen_board = True
            if self.last_corners is not None and self.last_motion_image is not None:
                jitter = float(np.max(np.linalg.norm(corners - self.last_corners, axis=1)))
                motion = board_motion(self.last_motion_image, motion_image, corners, frame.shape[1])
                steady = jitter < MAX_CORNER_JITTER_PX and motion < MAX_BOARD_MOTION
        self.last_corners, self.last_motion_image = corners, motion_image
        if not steady:
            self.steady_since = None
            return 0.0
        if self.steady_since is None:
            self.steady_since = now
        return now - self.steady_since


def grab_steady_frame(
    cap: cv2.VideoCapture,
    timeout: float = 10.0,
    steady_for: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
) -> np.ndarray:
    """Wait until the board is in view and still for `steady_for` seconds, then return that frame."""
    tracker = _Steadiness()
    deadline = clock() + timeout
    while clock() < deadline:
        frame = read_frame(cap)
        if tracker.update(frame, detect_board_downscaled(frame), clock()) >= steady_for:
            return frame
    if not tracker.seen_board:
        raise CameraError(
            f"no breadboard in view after {timeout:.0f} s. Check the camera points at the board "
            "(`benchlog camera preview`) and that the board is on a dark, matte surface"
        )
    raise CameraError(
        f"the board didn't hold still for {steady_for:.0f} s within {timeout:.0f} s. "
        "Move hands out of the frame and make sure the camera is fixed"
    )


@dataclass(frozen=True)
class CameraInfo:
    index: int
    width: int
    height: int


def list_cameras(max_index: int = 5) -> list[CameraInfo]:
    """Cameras that open and deliver a frame, with the resolution they actually run at."""
    found = []
    for index in range(max_index + 1):
        try:
            cap = open_camera(index)
        except CameraError:
            continue
        try:
            ok, frame = cap.read()
            if ok and frame is not None:
                found.append(CameraInfo(index, frame.shape[1], frame.shape[0]))
        finally:
            cap.release()
    return found


def preview(cap: cv2.VideoCapture, calibration=None, window: str = "benchlog camera (ESC to close)") -> None:
    """Live window for aiming the camera: board outline and whether a scan would capture now.

    With a calibration, the tracked hole map is drawn too, so you can see the lock holding.
    """
    from benchlog.vision.calibration import CalibrationError, draw_holes, track

    tracker = _Steadiness()
    holes, lock_status, last_track = None, "", float("-inf")
    try:
        while True:
            frame = read_frame(cap)
            now = time.monotonic()
            corners = detect_board_downscaled(frame)
            steady = tracker.update(frame, corners, now)
            display = frame.copy()
            if calibration is not None and now - last_track > 0.3:
                last_track = now
                try:
                    tracking = track(calibration, frame)
                    holes = tracking.apply(calibration.holes)
                    lock_status = f"locked (match {tracking.correlation:.2f}, moved {tracking.shift_px:.0f} px)"
                except CalibrationError as e:
                    holes, lock_status = None, f"lock lost: {e}"
            if holes is not None:
                draw_holes(display, holes)
            if corners is None:
                status, color = "no board in view", (0, 0, 255)
            elif steady >= 1.0:
                status, color = "board steady: ready to scan", (0, 200, 0)
            else:
                status, color = "board found: hold still", (0, 200, 255)
            if corners is not None:
                cv2.polylines(display, [corners.astype(np.int32)], True, color, 3)
            for i, text in enumerate(t for t in (status, lock_status) if t):
                cv2.putText(display, text, (30, 60 + 50 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 6)
                cv2.putText(display, text, (30, 60 + 50 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.3, color, 3)
            h, w = display.shape[:2]
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window, min(w, 1400), round(h * min(w, 1400) / w))
            cv2.imshow(window, display)
            if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                return
    finally:
        cv2.destroyAllWindows()
