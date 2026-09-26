"""Calibrate once, then lock: map every hole on the live image, save it, and track small board moves.

With the camera fixed, the hole map shouldn't be rebuilt from scratch on every scan (that's what
made scans fragile). Instead:

1. Calibrate (`benchlog camera calibrate`): every hole is drawn on the live image. The grid is
   placed from the board's corners (auto-detected, or dragged/nudged by hand) and then snapped
   onto the holes actually visible. Enter saves the hole positions and the empty board's image
   as the reference.
2. Track: each scan measures how far the board slid/rotated since calibration (a rough estimate
   from the board outline, refined to sub-pixel by aligning the image with the reference) and
   moves the saved holes with it. The camera height is fixed, so the size never changes.
3. Classify: a hole changed if it looks different from its own appearance at calibration. Usually
   the board is empty then; calibrating with a circuit built records which holes were occupied,
   and a hole is occupied now if it changed XOR it was occupied at calibration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from benchlog.core.board import RAILS
from benchlog.vision.bb830_layout import BOARD_H_MM, BOARD_W_MM, hole_positions_mm
from benchlog.vision.capture import (
    MAX_RELIABLE_RESIDUAL_PX,
    PX_PER_MM,
    WARP_H,
    WARP_W,
    detect_board_downscaled,
    register_holes,
    warp_from_corners,
)

CALIBRATION_FILE = "calibration.json"
REFERENCE_FILE = "reference.png"

_HOLES_MM = hole_positions_mm()
_HOLE_NAMES = list(_HOLES_MM)
_BOARD_MM = np.float32([[0, 0], [BOARD_W_MM, 0], [BOARD_W_MM, BOARD_H_MM], [0, BOARD_H_MM]])

# Tracking
MIN_TRACKING_CORRELATION = 0.6  # ECC correlation below this means the lock is lost
GOOD_TRACKING_CORRELATION = 0.9  # good enough to stop trying other starting points
MAX_TRACKED_MOVE_FRACTION = 0.25  # of the board's width; beyond this, recalibrate
_ECC_SCALES = (0.125, 0.25, 0.5)  # coarse to fine; the coarsest blurs out the (periodic) hole grid

# Classification
BLUR_SIGMA = 1.0
PATCH_RADIUS_PITCH = 0.3  # patch half-size as a fraction of the hole pitch
MIN_OCCUPIED_DIFF = 18.0  # Lab distance a hole must change by, however quiet the scan
NOISE_SIGMAS = 8.0  # ... and this many robust standard deviations above the typical hole


class CalibrationError(RuntimeError):
    pass


# ── Hole positions ────────────────────────────────────────────────────────────


def holes_from_corners(corners: np.ndarray) -> dict[str, tuple[float, float]]:
    """Image position of every hole, given the board body's corners [tl, tr, br, bl] in the image."""
    matrix = cv2.getPerspectiveTransform(_BOARD_MM, np.asarray(corners, dtype=np.float32))
    mm = np.float32([_HOLES_MM[n] for n in _HOLE_NAMES]).reshape(-1, 1, 2)
    pts = cv2.perspectiveTransform(mm, matrix).reshape(-1, 2)
    return {n: (float(x), float(y)) for n, (x, y) in zip(_HOLE_NAMES, pts)}


def fit_holes(frame: np.ndarray, corners: np.ndarray) -> tuple[dict[str, tuple[float, float]], float]:
    """Snap the grid onto the holes actually visible in `frame`. Returns positions and residual (px)."""
    corners = np.asarray(corners, dtype=np.float32)
    warped = warp_from_corners(frame, corners)
    warped_holes, residual = register_holes(warped)
    to_warped = cv2.getPerspectiveTransform(corners, np.float32([[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]]))
    pts = np.float32([warped_holes[n] for n in _HOLE_NAMES]).reshape(-1, 1, 2)
    image_pts = cv2.perspectiveTransform(pts, np.linalg.inv(to_warped)).reshape(-1, 2)
    # Residual is in warped pixels (PX_PER_MM px/mm); report it in image pixels.
    image_scale = np.linalg.norm(corners[1] - corners[0]) / (BOARD_W_MM * PX_PER_MM)
    return {n: (float(x), float(y)) for n, (x, y) in zip(_HOLE_NAMES, image_pts)}, residual * image_scale


def hole_pitch_px(holes: dict[str, tuple[float, float]]) -> float:
    return float(np.linalg.norm(np.subtract(holes["A2"], holes["A1"])))


# ── Saved calibration ─────────────────────────────────────────────────────────


@dataclass
class Calibration:
    corners: np.ndarray  # 4x2, [tl, tr, br, bl] of the board body in the reference image
    holes: dict[str, tuple[float, float]]
    reference: np.ndarray  # BGR image of the board at calibration time
    camera: int | None = None
    # Holes that were occupied in the reference image (empty unless calibrated with a circuit built).
    occupied: list[str] = field(default_factory=list)

    @property
    def frame_size(self) -> tuple[int, int]:
        h, w = self.reference.shape[:2]
        return w, h

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(directory / REFERENCE_FILE), self.reference)
        data = {
            "board": "bb830",
            "camera": self.camera,
            "frame_size": list(self.frame_size),
            "corners": np.asarray(self.corners).tolist(),
            "holes": {n: [round(x, 2), round(y, 2)] for n, (x, y) in self.holes.items()},
            "occupied": sorted(self.occupied),
        }
        (directory / CALIBRATION_FILE).write_text(json.dumps(data, indent=1) + "\n")

    @classmethod
    def load(cls, directory: Path) -> Calibration | None:
        path = directory / CALIBRATION_FILE
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        reference = cv2.imread(str(directory / REFERENCE_FILE))
        if reference is None:
            raise CalibrationError(f"{directory / REFERENCE_FILE} is missing; run `benchlog camera calibrate`")
        return cls(
            corners=np.float32(data["corners"]),
            holes={n: (float(x), float(y)) for n, (x, y) in data["holes"].items()},
            reference=reference,
            camera=data.get("camera"),
            occupied=list(data.get("occupied", [])),
        )


# ── Tracking ──────────────────────────────────────────────────────────────────


@dataclass
class Tracking:
    matrix: np.ndarray  # 2x3, reference image coords -> current frame coords
    correlation: float

    def apply(self, points: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
        names = list(points)
        pts = np.float32([points[n] for n in names]).reshape(-1, 1, 2)
        moved = cv2.transform(pts, self.matrix).reshape(-1, 2)
        return {n: (float(x), float(y)) for n, (x, y) in zip(names, moved)}

    @property
    def shift_px(self) -> float:
        return float(np.linalg.norm(self.matrix[:, 2]))

    @property
    def rotation_deg(self) -> float:
        return float(np.degrees(np.arctan2(self.matrix[1, 0], self.matrix[0, 0])))


def _board_mask(shape: tuple[int, ...], corners: np.ndarray) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.asarray(corners, dtype=np.int32), 255)
    return mask


def _gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _ecc(cal: Calibration, ref_gray: np.ndarray, cur_gray: np.ndarray, start: np.ndarray) -> tuple[np.ndarray, float]:
    """Refine a starting rotation+shift by aligning the images, coarse to fine."""
    mask = cv2.dilate(_board_mask(ref_gray.shape, cal.corners), np.ones((15, 15), np.uint8))
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)
    matrix, correlation = start.astype(np.float32).copy(), 0.0
    for scale in _ECC_SCALES:
        size = (max(8, round(ref_gray.shape[1] * scale)), max(8, round(ref_gray.shape[0] * scale)))
        ref_s = cv2.resize(ref_gray, size, interpolation=cv2.INTER_AREA)
        cur_s = cv2.resize(cur_gray, size, interpolation=cv2.INTER_AREA)
        mask_s = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
        scaled = matrix.copy()
        scaled[:, 2] *= scale
        correlation, scaled = cv2.findTransformECC(ref_s, cur_s, scaled, cv2.MOTION_EUCLIDEAN, criteria, mask_s, 5)
        matrix = scaled.copy()
        matrix[:, 2] /= scale
    return matrix, float(correlation)


def _outline_estimate(cal: Calibration, frame: np.ndarray) -> np.ndarray | None:
    """Rotation+shift from the calibrated corners to the board outline detected in `frame`."""
    detected = detect_board_downscaled(frame)
    if detected is None:
        return None
    estimate, _ = cv2.estimateAffinePartial2D(cal.corners, detected)
    if estimate is None:
        return None
    angle = np.arctan2(estimate[1, 0], estimate[0, 0])
    c, s = np.cos(angle), np.sin(angle)
    return np.float32([[c, -s, estimate[0, 2]], [s, c, estimate[1, 2]]])


def track(cal: Calibration, frame: np.ndarray, start: np.ndarray | None = None) -> Tracking:
    """How the board moved since calibration (a rotation + shift; the camera height is fixed).

    Tries, in order: `start` (e.g. the last tracked position), "hasn't moved", and only then the
    board outline detected in this frame, which can wobble on faint board edges.
    """
    if frame.shape[:2] != cal.reference.shape[:2]:
        raise CalibrationError(
            f"camera resolution changed ({frame.shape[1]}x{frame.shape[0]}, calibrated at "
            f"{cal.frame_size[0]}x{cal.frame_size[1]}); run `benchlog camera calibrate`"
        )
    ref_gray, cur_gray = _gray(cal.reference), _gray(frame)
    starts = ([start] if start is not None else []) + [np.eye(2, 3, dtype=np.float32), None]
    best: Tracking | None = None
    for candidate in starts:
        if candidate is None:
            candidate = _outline_estimate(cal, frame)  # only computed if the others didn't lock
            if candidate is None:
                continue
        try:
            matrix, correlation = _ecc(cal, ref_gray, cur_gray, candidate)
        except cv2.error:
            continue
        if best is None or correlation > best.correlation:
            best = Tracking(matrix=matrix, correlation=correlation)
        if correlation >= GOOD_TRACKING_CORRELATION:
            break

    if best is None or best.correlation < MIN_TRACKING_CORRELATION:
        match = f" (match {best.correlation:.2f})" if best else ""
        raise CalibrationError(
            f"can't lock onto the board{match}; check nothing is covering it, or run `benchlog camera calibrate`"
        )
    board_w = float(np.linalg.norm(cal.corners[1] - cal.corners[0]))
    if best.shift_px > MAX_TRACKED_MOVE_FRACTION * board_w:
        raise CalibrationError("the board moved too far since calibration; run `benchlog camera calibrate`")
    return best


# ── Classification ────────────────────────────────────────────────────────────


@dataclass
class Occupancy:
    changed: list[str]  # holes that look different from the reference image
    occupied: list[str]  # holes occupied now: changed XOR occupied at calibration
    diffs: dict[str, float] = field(repr=False)
    threshold: float


def _lab(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)


def classify(cal: Calibration, frame: np.ndarray, tracking: Tracking) -> Occupancy:
    """Holes occupied now, judged by whether each looks different from its calibration appearance."""
    h, w = cal.reference.shape[:2]
    # Bring the current frame into the reference's coordinates so every hole lines up exactly.
    aligned = cv2.warpAffine(frame, tracking.matrix, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    # A light blur on both sides evens out the softening that resampling adds at sharp edges.
    ref_lab = _lab(cv2.GaussianBlur(cal.reference, (0, 0), BLUR_SIGMA))
    cur_lab = _lab(cv2.GaussianBlur(aligned, (0, 0), BLUR_SIGMA))

    # Compensate for overall lighting changes on the board (brightness and contrast).
    board = _board_mask(ref_lab.shape, cal.corners) > 0
    ref_l, cur_l = ref_lab[..., 0][board], cur_lab[..., 0][board]
    ref_spread = np.percentile(ref_l, 75) - np.percentile(ref_l, 25)
    cur_spread = np.percentile(cur_l, 75) - np.percentile(cur_l, 25)
    gain = ref_spread / cur_spread if cur_spread > 1e-3 else 1.0
    cur_lab[..., 0] = (cur_lab[..., 0] - np.median(cur_l)) * gain + np.median(ref_l)

    distance = np.linalg.norm(cur_lab - ref_lab, axis=2)
    r = max(2, round(PATCH_RADIUS_PITCH * hole_pitch_px(cal.holes)))
    diffs = {}
    for name, (x, y) in cal.holes.items():
        xi, yi = round(x), round(y)
        patch = distance[max(0, yi - r) : yi + r + 1, max(0, xi - r) : xi + r + 1]
        diffs[name] = float(patch.mean()) if patch.size else 0.0

    values = np.array(list(diffs.values()))
    median = float(np.median(values))
    spread = 1.4826 * float(np.median(np.abs(values - median)))
    threshold = max(MIN_OCCUPIED_DIFF, median + NOISE_SIGMAS * spread)
    changed = [n for n, d in diffs.items() if d > threshold]
    occupied = sorted(set(changed) ^ set(cal.occupied))
    return Occupancy(changed=changed, occupied=occupied, diffs=diffs, threshold=threshold)


# ── Interactive calibration ───────────────────────────────────────────────────

# Arrow key codes from cv2.waitKeyEx differ per platform; i/j/k/l work everywhere.
_NUDGE = {
    ord("i"): (0, -1), ord("k"): (0, 1), ord("j"): (-1, 0), ord("l"): (1, 0),
    63232: (0, -1), 63233: (0, 1), 63234: (-1, 0), 63235: (1, 0),  # macOS
    65362: (0, -1), 65364: (0, 1), 65361: (-1, 0), 65363: (1, 0),  # Linux
    2490368: (0, -1), 2621440: (0, 1), 2424832: (-1, 0), 2555904: (1, 0),  # Windows
}  # fmt: skip
_GRAB_RADIUS_PX = 40
SNAP_INTERVAL_S = 0.5  # how often auto mode re-fits the grid onto the holes (until locked)
SMOOTHING = 0.2  # how far the corners follow each new detection (1 = no smoothing)
SMOOTH_JUMP_PX = 15  # bigger jumps are real moves: take them immediately


@dataclass
class CalibrationEditor:
    """State of the calibration window, separate from the GUI so it can be tested.

    The corners are handles for placing the grid roughly; the grid then snaps onto the holes
    actually visible in the image (the measured layout alone can be off by about half a hole).
    """

    corners: np.ndarray | None = None
    holes: dict[str, tuple[float, float]] | None = None
    auto: bool = True  # follow the detected board every frame
    locked: bool = False  # holes snapped onto the real holes
    residual: float | None = None
    selected: int = 0  # corner that the nudge keys move
    dragging: bool = False
    needs_snap: bool = False
    last_snap: float = float("-inf")
    message: str = ""

    def update(self, frame: np.ndarray, now: float) -> None:
        if self.auto and not self.locked:  # once locked, stop following the (wobbly) detector
            corners = detect_board_downscaled(frame)
            if corners is None:
                self.message = "no board detected: drag the corner handles onto it"
                return
            corners = np.asarray(corners, dtype=np.float32)
            if self.corners is not None and np.abs(corners - self.corners).max() < SMOOTH_JUMP_PX:
                corners = self.corners + SMOOTHING * (corners - self.corners)  # damp frame-to-frame wobble
            self.corners = corners
            if self.holes is None or now - self.last_snap >= SNAP_INTERVAL_S:
                self.snap(frame, now)
        elif self.needs_snap and not self.dragging:
            self.snap(frame, now)

    def snap(self, frame: np.ndarray, now: float) -> None:
        """Fit the grid onto the visible holes, falling back to the layout if they can't be found."""
        self.needs_snap, self.last_snap = False, now
        holes, residual = fit_holes(frame, self.corners)
        if residual <= MAX_RELIABLE_RESIDUAL_PX:
            self.holes, self.locked, self.residual, self.message = holes, True, residual, ""
        else:
            # Faint holes (e.g. a translucent board): the user places the grid by hand instead.
            self.holes, self.locked, self.residual = holes_from_corners(self.corners), False, None
            self.message = "can't see the holes clearly: move the corner handles until the dots sit in the holes"

    def _move_corners(self, corners: np.ndarray) -> None:
        """Manual adjustment: show the layout grid right away, snap once the user lets go."""
        self.auto = False
        self.corners = np.asarray(corners, dtype=np.float32)
        self.holes, self.locked, self.residual = holes_from_corners(self.corners), False, None
        self.needs_snap = True

    def on_mouse(self, event: int, x: int, y: int, frame_size: tuple[int, int]) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            if self.corners is None:
                w, h = frame_size
                self._move_corners(np.float32([[w * 0.1, h * 0.3], [w * 0.9, h * 0.3], [w * 0.9, h * 0.7], [w * 0.1, h * 0.7]]))
            dist = np.linalg.norm(self.corners - np.float32([x, y]), axis=1)
            if dist.min() <= _GRAB_RADIUS_PX:
                self.selected, self.dragging, self.auto = int(dist.argmin()), True, False
        elif event == cv2.EVENT_MOUSEMOVE and self.dragging:
            corners = self.corners.copy()
            corners[self.selected] = (x, y)
            self._move_corners(corners)
        elif event == cv2.EVENT_LBUTTONUP:
            self.dragging = False

    def on_key(self, key: int, frame: np.ndarray, now: float) -> str | None:
        """Returns "save" or "quit" when the window should close."""
        if key in (27, ord("q")):
            return "quit"
        if key in (13, 10, 32):
            if self.holes is None:
                self.message = "no hole map yet: wait for the board or drag the corner handles"
                return None
            return "save"
        if key == ord("a"):  # follow the detected board again
            self.auto, self.locked, self.message = True, False, ""
        elif key in (ord("1"), ord("2"), ord("3"), ord("4")):
            self.selected = key - ord("1")
        elif key in _NUDGE and self.corners is not None:
            corners = self.corners.copy()
            corners[self.selected] += np.float32(_NUDGE[key])
            self._move_corners(corners)
        elif key == ord("f") and self.corners is not None:
            self.snap(frame, now)
        return None


def draw_holes(img: np.ndarray, holes: dict[str, tuple[float, float]], pitch: float | None = None) -> None:
    """Dots on every hole: rails + red / - blue, terminal holes green, row labels every 5."""
    pitch = pitch or hole_pitch_px(holes)
    radius = max(2, round(pitch * 0.18))
    for name, (x, y) in holes.items():
        rail = name[:2] if name[:2] in RAILS else None
        color = (60, 60, 230) if rail and "+" in rail else (230, 120, 40) if rail else (60, 200, 60)
        cv2.circle(img, (round(x), round(y)), radius, color, -1, cv2.LINE_AA)
    for row in (1, 5, 10, 20, 30, 40, 50, 60):
        x, y = holes[f"A{row}"]
        cv2.putText(img, str(row), (round(x - pitch / 2), round(y - pitch * 0.8)), cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.4, pitch / 40), (0, 220, 255), 1, cv2.LINE_AA)  # fmt: skip


def render(editor: CalibrationEditor, frame: np.ndarray) -> np.ndarray:
    out = frame.copy()
    if editor.holes is not None:
        draw_holes(out, editor.holes)
    if editor.corners is not None:
        cv2.polylines(out, [editor.corners.astype(np.int32)], True, (0, 220, 255), 2, cv2.LINE_AA)
        for i, (x, y) in enumerate(editor.corners):
            color = (0, 255, 255) if i == editor.selected else (255, 255, 255)
            cv2.circle(out, (round(x), round(y)), 12, color, 2, cv2.LINE_AA)
            cv2.putText(out, str(i + 1), (round(x) + 14, round(y) - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    mode = "AUTO" if editor.auto else "MANUAL"
    lock = f"LOCKED on holes ({editor.residual:.1f} px)" if editor.locked else "placed by hand" if not editor.auto else "not locked"
    lines = [
        f"{mode} - {lock}. Check every dot sits in a hole, then press Enter",
        "drag corner handles, or 1-4 + arrows/ijkl to nudge   f: snap now   a: re-detect board   Enter: save   Esc: cancel",
    ]
    if editor.message:
        lines.append(editor.message)
    for i, line in enumerate(lines):
        y = 40 + i * 36
        cv2.putText(out, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(out, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def run_calibration(
    cap: cv2.VideoCapture, camera: int | None = None, occupied: list[str] = (), window: str = "benchlog calibrate"
) -> Calibration | None:
    """Interactive window. Returns the calibration on Enter, None on Esc."""
    import time

    from benchlog.vision.camera import read_frame  # camera imports capture, like this module

    editor = CalibrationEditor()
    frame = read_frame(cap)
    h, w = frame.shape[:2]
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, min(w, 1400), round(h * min(w, 1400) / w))
    cv2.setMouseCallback(window, lambda event, x, y, *_: editor.on_mouse(event, x, y, (w, h)))
    try:
        while True:
            frame = read_frame(cap)
            now = time.monotonic()
            editor.update(frame, now)
            cv2.imshow(window, render(editor, frame))
            action = editor.on_key(cv2.waitKeyEx(15), frame, now)
            if action == "quit":
                return None
            if action == "save":
                return Calibration(
                    corners=editor.corners, holes=editor.holes, reference=frame.copy(), camera=camera, occupied=list(occupied)
                )
    finally:
        cv2.destroyAllWindows()
