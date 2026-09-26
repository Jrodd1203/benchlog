"""Camera capture, ArUco calibration and hole-occupancy detection. Owner: Person 4.

Pipeline:
  1. calibrate(frame) -> BoardFrame | None   (finds 4 ArUco corners, perspective-warps)
  2. scan_occupancy(board_frame, background) -> dict[hole, bool]
  3. occupancy_diff(prev, curr) -> list[Observation]   (raw vision-only observations)

Output is Observation objects from benchlog.core.models; nothing else depends on OpenCV.
"""

from __future__ import annotations

import cv2
import numpy as np
from dataclasses import dataclass, field

from benchlog.core.board import BreadboardTemplate, BB830, LEFT_COLUMNS, RIGHT_COLUMNS
from benchlog.core.models import Observation, ObservationKind, ObservationStatus

# ── Canonical warp dimensions ─────────────────────────────────────────────────
# The board is perspective-corrected into this fixed rectangle for all processing.
WARP_W = 1260
WARP_H = 460

# ArUco dict: 4x4 markers, IDs 0-3 placed at board corners.
# 0=top-left  1=top-right  2=bottom-right  3=bottom-left
ARUCO_DICT_ID = cv2.aruco.DICT_4X4_50

# ── BB830 hole geometry in canonical pixel space ──────────────────────────────
# Standard 2.54 mm pitch. These constants assume markers sit just outside the
# board edge; tune PITCH_PX after printing and measuring your actual output.
PITCH_PX: float = 17.5          # pixels per 2.54 mm hole pitch

_COL_START_X: float = 108.0     # pixel-x of column A, row 1
_ROW_START_Y: float = 42.0      # pixel-y of row 1
_DIP_GAP_PX: float = 30.0       # extra gap between column E and F

_RAIL_LM_X: float = 16.0        # L- rail x
_RAIL_LP_X: float = 38.0        # L+ rail x
_RAIL_RP_X: float = WARP_W - 38.0
_RAIL_RM_X: float = WARP_W - 16.0
_RAIL_Y0: float = 10.0          # rail row 1 y

SAMPLE_R: int = 5               # half-size of patch sampled around each hole center (px)
OCCUPANCY_THRESHOLD: float = 28.0  # colour distance from background to call a hole occupied


# ── Hole pixel map ────────────────────────────────────────────────────────────

def _build_hole_map(board: BreadboardTemplate = BB830) -> dict[str, tuple[int, int]]:
    px: dict[str, tuple[int, int]] = {}

    # Terminal columns A-J (gap between E and F)
    x = _COL_START_X
    for i, col in enumerate(LEFT_COLUMNS + RIGHT_COLUMNS):
        if i == len(LEFT_COLUMNS):
            x += _DIP_GAP_PX
        for row in range(1, board.rows + 1):
            y = _ROW_START_Y + (row - 1) * PITCH_PX
            px[f"{col}{row}"] = (round(x), round(y))
        x += PITCH_PX

    # Rails
    for rail, rx in (("L-", _RAIL_LM_X), ("L+", _RAIL_LP_X),
                     ("R+", _RAIL_RP_X), ("R-", _RAIL_RM_X)):
        for idx in range(1, board.rail_length + 1):
            y = _RAIL_Y0 + (idx - 1) * PITCH_PX
            px[f"{rail}{idx}"] = (round(rx), round(y))

    return px


_HOLE_MAP: dict[str, tuple[int, int]] = _build_hole_map()


# ── BoardFrame ────────────────────────────────────────────────────────────────

@dataclass
class BoardFrame:
    """Perspective-corrected view of the breadboard."""
    warped: np.ndarray
    homography: np.ndarray

    def sample(self, hole: str) -> np.ndarray:
        """Mean BGR of the patch around a hole center."""
        x, y = _HOLE_MAP[hole]
        h, w = self.warped.shape[:2]
        patch = self.warped[
            max(0, y - SAMPLE_R): min(h, y + SAMPLE_R),
            max(0, x - SAMPLE_R): min(w, x + SAMPLE_R),
        ]
        if patch.size == 0:
            return np.zeros(3, dtype=np.float32)
        return patch.mean(axis=(0, 1)).astype(np.float32)

    def debug_image(self, occupancy: dict[str, bool] | None = None) -> np.ndarray:
        """Return annotated warp image: green dot = occupied, grey = empty."""
        out = self.warped.copy()
        for hole, (x, y) in _HOLE_MAP.items():
            if occupancy is None:
                color = (160, 160, 160)
            elif occupancy.get(hole):
                color = (0, 220, 0)
            else:
                color = (60, 60, 60)
            cv2.circle(out, (x, y), 3, color, -1)
        return out


# ── Calibration ───────────────────────────────────────────────────────────────
# Primary path: contour-based (no markers needed).
# Backup path:  ArUco markers for sub-pixel precision (print & tape to corners).

# BB830 aspect ratio width:height ≈ 3.15:1. Boards within this tolerance are accepted.
_BOARD_ASPECT_MIN = 2.5
_BOARD_ASPECT_MAX = 4.2


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Sort 4 points into [top-left, top-right, bottom-right, bottom-left]."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([
        pts[np.argmin(s)],   # top-left: smallest x+y
        pts[np.argmin(d)],   # top-right: smallest x-y
        pts[np.argmax(s)],   # bottom-right: largest x+y
        pts[np.argmax(d)],   # bottom-left: largest x-y
    ], dtype=np.float32)


def _warp_from_corners(frame: np.ndarray, corners: np.ndarray) -> BoardFrame:
    dst = np.float32([[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]])
    H, _ = cv2.findHomography(corners, dst)
    warped = cv2.warpPerspective(frame, H, (WARP_W, WARP_H))
    return BoardFrame(warped=warped, homography=H)


def calibrate_from_image(frame: np.ndarray) -> BoardFrame | None:
    """Primary calibration: find the breadboard rectangle by contour detection.

    Works with any camera angle — no printed markers needed. The board's white/
    beige plastic body is detected against the background. Returns None only if
    no plausible rectangle is found.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (7, 7), 0)

    # Try both Canny edges and an inverse threshold (white board on dark desk).
    candidates: list[np.ndarray] = []
    for method in ("canny", "thresh"):
        if method == "canny":
            edges = cv2.Canny(blur, 30, 100)
            edges = cv2.dilate(edges, None, iterations=2)
        else:
            _, edges = cv2.threshold(blur, 180, 255, cv2.THRESH_BINARY)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) != 4:
                continue
            x, y, w, h = cv2.boundingRect(approx)
            if w < 100 or h < 30:
                continue
            aspect = w / max(h, 1)
            if _BOARD_ASPECT_MIN <= aspect <= _BOARD_ASPECT_MAX:
                candidates.append(approx)
                break
        if candidates:
            break

    if not candidates:
        return None

    corners = _order_corners(candidates[0])
    return _warp_from_corners(frame, corners)


def detect_markers(frame: np.ndarray) -> dict[int, np.ndarray]:
    """Return {marker_id: 4x2 corner array} for every detected ArUco marker."""
    d = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)
    params = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(d, params)
    corners, ids, _ = detector.detectMarkers(frame)
    if ids is None:
        return {}
    return {int(ids[i]): corners[i][0] for i in range(len(ids))}


def calibrate_from_aruco(frame: np.ndarray) -> BoardFrame | None:
    """ArUco backup: sub-pixel precise calibration when all 4 markers are visible.

    Marker IDs: 0=top-left, 1=top-right, 2=bottom-right, 3=bottom-left.
    Place markers just outside each board corner, inner corner touching the edge.
    """
    markers = detect_markers(frame)
    if not all(i in markers for i in range(4)):
        return None
    src = np.float32([
        markers[0][2],  # inner corner of each marker faces the board
        markers[1][3],
        markers[2][0],
        markers[3][1],
    ])
    return _warp_from_corners(frame, src)


def calibrate(frame: np.ndarray) -> BoardFrame | None:
    """Calibrate from a single camera frame.

    Tries contour detection first (no markers needed). Falls back to ArUco if
    contours fail and markers are present. Returns None only if both fail.
    """
    bf = calibrate_from_image(frame)
    if bf is not None:
        return bf
    return calibrate_from_aruco(frame)


# ── Background learning ───────────────────────────────────────────────────────

def learn_background(board_frame: BoardFrame, empty_holes: list[str] | None = None) -> np.ndarray:
    """Sample known-empty holes to get the board's background colour (mean BGR).

    Pass holes you're sure are empty, or None to default to the four outermost corners.
    """
    if empty_holes is None:
        empty_holes = ["A1", "J1", "A63", "J63"]
    samples = np.stack([board_frame.sample(h) for h in empty_holes])
    return samples.mean(axis=0).astype(np.float32)


# ── Occupancy scan ────────────────────────────────────────────────────────────

def scan_occupancy(
    board_frame: BoardFrame,
    background: np.ndarray,
    board: BreadboardTemplate = BB830,
    threshold: float = OCCUPANCY_THRESHOLD,
) -> dict[str, bool]:
    """Return {hole: is_occupied} for every hole on the board."""
    result: dict[str, bool] = {}
    for hole in _HOLE_MAP:
        bgr = board_frame.sample(hole)
        dist = float(np.linalg.norm(bgr.astype(float) - background.astype(float)))
        result[hole] = dist > threshold
    return result


# ── Component type heuristic (stretch goal) ───────────────────────────────────

# Rough HSV hue ranges for common component colours.
_COMPONENT_COLOUR_HINTS: dict[str, tuple[int, int]] = {
    "wire_red":    (0,   10),
    "wire_orange": (10,  20),
    "wire_yellow": (20,  35),
    "wire_green":  (35,  85),
    "wire_blue":   (85, 130),
    "wire_purple": (130,160),
    "led_red":     (0,   10),
    "led_green":   (35,  85),
    "led_yellow":  (20,  35),
    "led_blue":    (100,130),
}

def guess_component_colour(board_frame: BoardFrame, hole: str) -> str | None:
    """Return a colour-hint string for an occupied hole, or None if ambiguous."""
    x, y = _HOLE_MAP[hole]
    h, w = board_frame.warped.shape[:2]
    patch = board_frame.warped[
        max(0, y - SAMPLE_R): min(h, y + SAMPLE_R),
        max(0, x - SAMPLE_R): min(w, x + SAMPLE_R),
    ]
    if patch.size == 0:
        return None
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    mean_h = float(hsv[:, :, 0].mean())
    mean_s = float(hsv[:, :, 1].mean())
    if mean_s < 40:
        return "wire_black_or_white"
    for label, (lo, hi) in _COMPONENT_COLOUR_HINTS.items():
        if lo <= mean_h <= hi:
            return label
    return None


# ── Diff → Observations ───────────────────────────────────────────────────────

def occupancy_diff(
    prev: dict[str, bool],
    curr: dict[str, bool],
    confidence: float = 0.7,
) -> list[Observation]:
    """Diff two occupancy grids. Returns vision-only Observations (status=PENDING).

    These are raw proposals — the Reconciler assigns final confidence and status.
    """
    obs: list[Observation] = []
    i = 0
    for hole, now in curr.items():
        was = prev.get(hole, False)
        if now == was:
            continue
        kind = ObservationKind.ADDED if now else ObservationKind.REMOVED
        obs.append(Observation(
            id=f"vis_{i}",
            kind=kind,
            object_type="wire",
            object_id=f"hole_{hole}",
            before=None if kind == ObservationKind.ADDED else {"hole": hole},
            after={"hole": hole} if kind == ObservationKind.ADDED else None,
            confidence=confidence,
            status=ObservationStatus.PENDING,
        ))
        i += 1
    return obs


# ── Camera helpers ────────────────────────────────────────────────────────────

def open_camera(index: int = 0) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera {index}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    return cap


def capture_frame(cap: cv2.VideoCapture) -> np.ndarray:
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError("Failed to read frame from camera")
    return frame


def capture_board(cap: cv2.VideoCapture, retries: int = 20) -> BoardFrame:
    """Read frames until all 4 ArUco markers are visible. Returns calibrated BoardFrame."""
    for _ in range(retries):
        frame = capture_frame(cap)
        bf = calibrate(frame)
        if bf is not None:
            return bf
    raise RuntimeError(f"Could not detect all 4 ArUco markers after {retries} frames")
