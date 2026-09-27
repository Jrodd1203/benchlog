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
    PITCH_PX,
    PX_PER_MM,
    WARP_H,
    WARP_W,
    detect_board_downscaled,
    detect_hole_blobs,
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
PATCH_GRID = (8, 4)  # patches along and across the board for the perspective correction
PATCH_HALF_PITCHES = 4  # patch half-size in hole pitches: big enough to include non-repeating features
PATCH_ROUNDS = 3
MIN_PATCHES = 8
MIN_PATCH_RESPONSE = 0.05  # phase-correlation peak strength below this means the patch can't be trusted
MAX_PATCH_RESIDUAL_PX = 1.5  # median leftover misalignment allowed after correction

# Classification
BLUR_SIGMA = 1.0
LIGHTING_SIGMA_PITCH = 2.5  # lighting changes smoother than this (in hole pitches) are ignored
PATCH_RADIUS_PITCH = 0.3  # patch half-size as a fraction of the hole pitch
MIN_OCCUPIED_DIFF = 18.0  # Lab distance a hole must change by, however quiet the scan
NOISE_SIGMAS = 8.0  # ... and this many robust standard deviations above the typical hole
MIN_COLOR_DIFF = 12.0  # chroma difference that makes a hole look colored (empty-board check)


class CalibrationError(RuntimeError):
    pass


# ── Hole positions ────────────────────────────────────────────────────────────


def holes_from_corners(corners: np.ndarray) -> dict[str, tuple[float, float]]:
    """Image position of every hole, given the board body's corners [tl, tr, br, bl] in the image."""
    matrix = cv2.getPerspectiveTransform(_BOARD_MM, np.asarray(corners, dtype=np.float32))
    mm = np.float32([_HOLES_MM[n] for n in _HOLE_NAMES]).reshape(-1, 1, 2)
    pts = cv2.perspectiveTransform(mm, matrix).reshape(-1, 2)
    return {n: (float(x), float(y)) for n, (x, y) in zip(_HOLE_NAMES, pts)}


_TEMPLATE_PX = {n: (x * PX_PER_MM, y * PX_PER_MM) for n, (x, y) in _HOLES_MM.items()}  # in the warped image
_RAIL_PAIRS = (("L+", "L-"), ("R+", "R-"))
MIN_SNAP_BLOBS = 40  # detected holes needed before trusting a snap
MIN_MATCHED_TERMINALS = 60
SNAP_CANDIDATES = 6


def _hit_map(blobs: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    hit = np.zeros(shape, np.uint8)
    for x, y in blobs:
        cv2.circle(hit, (round(x), round(y)), max(2, round(PITCH_PX * 0.25)), 1, -1)
    return hit


def _best_shift(hit: np.ndarray, pts: np.ndarray, span: float, step: float) -> tuple[np.ndarray, int]:
    """The (dx, dy) within +-span that lands the most points on detected holes."""
    h, w = hit.shape
    shifts = np.arange(-span, span + step / 2, step)
    xs = np.rint(pts[None, None, :, 0] + shifts[None, :, None]).astype(int)  # (1, ndx, n)
    ys = np.rint(pts[None, None, :, 1] + shifts[:, None, None]).astype(int)  # (ndy, 1, n)
    xs, ys = np.broadcast_arrays(xs, ys)
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    counts = np.where(ok, hit[np.clip(ys, 0, h - 1), np.clip(xs, 0, w - 1)], 0).sum(axis=2)
    iy, ix = np.unravel_index(counts.argmax(), counts.shape)
    return np.float32([shifts[ix], shifts[iy]]), int(counts[iy, ix])


def _darkness_map(warped: np.ndarray) -> np.ndarray:
    """How much darker each spot is than the plastic around it (high at holes, even faint ones)."""
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY).astype(np.float32)
    inner = max(3, round(PITCH_PX * 0.3)) | 1
    outer = max(inner + 2, round(PITCH_PX * 0.9)) | 1
    return cv2.blur(gray, (outer, outer)) - cv2.blur(gray, (inner, inner))


def _darkest_shift(darkness: np.ndarray, pts: np.ndarray, span: float, step: float) -> np.ndarray:
    """The (dx, dy) within +-span where the points sit on the darkest spots on average."""
    h, w = darkness.shape
    shifts = np.arange(-span, span + step / 2, step)
    xs = np.clip(np.rint(pts[None, None, :, 0] + shifts[None, :, None]).astype(int), 0, w - 1)
    ys = np.clip(np.rint(pts[None, None, :, 1] + shifts[:, None, None]).astype(int), 0, h - 1)
    xs, ys = np.broadcast_arrays(xs, ys)
    scores = darkness[ys, xs].mean(axis=2)
    iy, ix = np.unravel_index(scores.argmax(), scores.shape)
    return np.float32([shifts[ix], shifts[iy]])


def _nearest(pts: np.ndarray, blobs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    d = np.linalg.norm(pts[:, None, :] - blobs[None, :, :], axis=2)
    idx = d.argmin(axis=1)
    return idx, d[np.arange(len(pts)), idx]


def _refine(t: np.ndarray, placed: np.ndarray, blobs: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """A few rounds of matching template holes to nearby detected holes and fitting a homography."""
    matrix = None
    for tol in (0.45, 0.35, 0.3):
        idx, dist = _nearest(placed, blobs)
        good = dist < tol * PITCH_PX
        if good.sum() < 8:
            break
        found, _ = cv2.findHomography(t[good], blobs[idx[good]], cv2.RANSAC, PITCH_PX * 0.2)
        if found is None:
            break
        matrix = found
        placed = cv2.perspectiveTransform(t.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    return placed, matrix


def snap_warped(warped: np.ndarray) -> tuple[dict[str, tuple[float, float]], float, int]:
    """Fit the layout onto the holes detected in a straightened board image.

    The measured layout can be off by more than half a hole (corner detection, a different board
    model), so the terminal grid is first found by a global search over shift and stretch, then
    refined with a homography; each rail pair then gets its own small shift, since rail spacing
    differs between board models. Returns positions (warped px), median residual of matched
    holes (warped px) and how many terminal holes matched a detected hole.
    """
    blobs = detect_hole_blobs(warped)
    template = {n: np.float32(p) for n, p in _TEMPLATE_PX.items()}
    if len(blobs) < MIN_SNAP_BLOBS:
        return {n: (float(x), float(y)) for n, (x, y) in template.items()}, float("inf"), 0
    blobs = np.float32(blobs)
    hit = _hit_map(blobs, warped.shape[:2])

    terms = [n for n in _HOLE_NAMES if n[:2] not in RAILS]
    t = np.float32([template[n] for n in terms])
    centre = t.mean(axis=0)
    coarse = []
    for sx in np.linspace(0.94, 1.06, 9):
        for sy in np.linspace(0.92, 1.08, 9):
            scaled = (t - centre) * np.float32([sx, sy]) + centre
            shift, count = _best_shift(hit, scaled, 1.3 * PITCH_PX, 2.0)
            coarse.append((count, scaled + shift))
    # The hole grid repeats, so a placement one hole off also scores well at coarse resolution.
    # Refine several distinct candidates and keep the one that matches the most holes.
    coarse.sort(key=lambda c: -c[0])
    candidates: list[np.ndarray] = []
    for _, placed in coarse:
        if all(np.abs(placed - other).max() > 0.5 * PITCH_PX for other in candidates):
            candidates.append(placed)
        if len(candidates) == SNAP_CANDIDATES:
            break
    best = None
    for placed in candidates:
        placed, matrix = _refine(t, placed, blobs)
        _, dist = _nearest(placed, blobs)
        close = dist < 0.3 * PITCH_PX
        score = (int(close.sum()), -float(np.median(dist[close])) if close.any() else 0.0)
        if best is None or score > best[0]:
            best = (score, placed, matrix)
    _, placed, matrix = best

    positions = dict(zip(terms, placed))
    darkness = None
    for pair in _RAIL_PAIRS:
        names = [n for n in _HOLE_NAMES if n[:2] in pair]
        r = np.float32([template[n] for n in names])
        if matrix is not None:
            r = cv2.perspectiveTransform(r.reshape(-1, 1, 2), matrix).reshape(-1, 2)
        shift, count = _best_shift(hit, r, 1.2 * PITCH_PX, 1.0)
        if count >= 10:
            r = r + shift
            idx, dist = _nearest(r, blobs)
            close = dist < 0.3 * PITCH_PX
            if close.sum() >= 5:
                r = r + np.median(blobs[idx[close]] - r[close], axis=0)
        else:
            # Rail holes often read lighter (reflective clips) than the blob detector's cutoff.
            # Their groups of five make the darkest placement unique, so use darkness instead.
            if darkness is None:
                darkness = _darkness_map(warped)
            r = r + _darkest_shift(darkness, r, 1.2 * PITCH_PX, 1.0)
        positions.update(zip(names, r))

    t_now = np.float32([positions[n] for n in terms])
    _, dist = _nearest(t_now, blobs)
    matched = dist < 0.3 * PITCH_PX
    residual = float(np.median(dist[matched])) if matched.any() else float("inf")
    return {n: (float(x), float(y)) for n, (x, y) in positions.items()}, residual, int(matched.sum())


def fit_holes(frame: np.ndarray, corners: np.ndarray) -> tuple[dict[str, tuple[float, float]], float]:
    """Snap the grid onto the holes actually visible in `frame`. Returns positions and residual (image px).

    The residual is infinite when too few holes could be matched to trust the fit.
    """
    corners = np.asarray(corners, dtype=np.float32)
    warped_holes, residual, matched = snap_warped(warp_from_corners(frame, corners))
    to_warped = cv2.getPerspectiveTransform(corners, np.float32([[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]]))
    pts = np.float32([warped_holes[n] for n in _HOLE_NAMES]).reshape(-1, 1, 2)
    image_pts = cv2.perspectiveTransform(pts, np.linalg.inv(to_warped)).reshape(-1, 2)
    if matched < MIN_MATCHED_TERMINALS:
        residual = float("inf")
    # Residual is in warped pixels (PX_PER_MM px/mm); report it in image pixels.
    image_scale = np.linalg.norm(corners[1] - corners[0]) / (BOARD_W_MM * PX_PER_MM)
    return {n: (float(x), float(y)) for n, (x, y) in zip(_HOLE_NAMES, image_pts)}, residual * image_scale


def flip_row_names(holes: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """Rename holes for a board numbered from the other end (row 1 on the left)."""
    flipped = {}
    for name, pos in holes.items():
        if name[:2] in RAILS:
            flipped[f"{name[:2]}{51 - int(name[2:])}"] = pos
        else:
            flipped[f"{name[0]}{64 - int(name[1:])}"] = pos
    return flipped


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
    matrix: np.ndarray  # 3x3 homography (a 2x3 rotation+shift is accepted too): reference -> current frame
    correlation: float

    def __post_init__(self) -> None:
        m = np.asarray(self.matrix, dtype=np.float64)
        self.matrix = np.vstack([m, [0, 0, 1]]) if m.shape == (2, 3) else m

    def apply(self, points: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
        names = list(points)
        pts = np.float64([points[n] for n in names]).reshape(-1, 1, 2)
        moved = cv2.perspectiveTransform(pts, self.matrix).reshape(-1, 2)
        return {n: (float(x), float(y)) for n, (x, y) in zip(names, moved)}

    def apply_array(self, pts: np.ndarray) -> np.ndarray:
        return cv2.perspectiveTransform(np.float64(pts).reshape(-1, 1, 2), self.matrix).reshape(-1, 2)

    @property
    def shift_px(self) -> float:
        return float(np.linalg.norm(self.matrix[:2, 2]))

    @property
    def rotation_deg(self) -> float:
        return float(np.degrees(np.arctan2(self.matrix[1, 0], self.matrix[0, 0])))


def _board_mask(shape: tuple[int, ...], corners: np.ndarray) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.asarray(corners, dtype=np.int32), 255)
    return mask


def _gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _ecc(
    cal: Calibration, ref_gray: np.ndarray, cur_gray: np.ndarray, start: np.ndarray, motion: int, scales: tuple[float, ...]
) -> tuple[np.ndarray, float]:
    """Refine a starting 3x3 transform by aligning the images, coarse to fine."""
    mask = cv2.dilate(_board_mask(ref_gray.shape, cal.corners), np.ones((15, 15), np.uint8))
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6)
    matrix, correlation = np.float64(start), 0.0
    for scale in scales:
        size = (max(8, round(ref_gray.shape[1] * scale)), max(8, round(ref_gray.shape[0] * scale)))
        ref_s = cv2.resize(ref_gray, size, interpolation=cv2.INTER_AREA)
        cur_s = cv2.resize(cur_gray, size, interpolation=cv2.INTER_AREA)
        mask_s = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
        to_s = np.diag([scale, scale, 1.0])
        scaled = (to_s @ matrix @ np.linalg.inv(to_s)).astype(np.float32)
        if motion != cv2.MOTION_HOMOGRAPHY:
            scaled = scaled[:2]
        correlation, scaled = cv2.findTransformECC(ref_s, cur_s, scaled, motion, criteria, mask_s, 5)
        if scaled.shape == (2, 3):
            scaled = np.vstack([scaled, [0, 0, 1]])
        matrix = np.linalg.inv(to_s) @ np.float64(scaled) @ to_s
    return matrix, float(correlation)


def _patch_centres(cal: Calibration) -> np.ndarray:
    """Points spread over the board (reference coords) where local alignment is measured."""
    c = np.float64(cal.corners)
    pts = []
    for u in np.linspace(0.06, 0.94, PATCH_GRID[0]):
        top, bottom = c[0] + (c[1] - c[0]) * u, c[3] + (c[2] - c[3]) * u
        pts.extend(top + (bottom - top) * v for v in np.linspace(0.12, 0.88, PATCH_GRID[1]))
    return np.float64(pts)


def _patch_shifts(
    cal: Calibration, ref_gray: np.ndarray, cur_gray: np.ndarray, matrix: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Leftover misalignment at each patch after aligning with `matrix`: (patch centres, shifts).

    Patches span several holes, so they include things that don't repeat (printed numbers, board
    edges, the centre channel, rail lines). That lets them see a slip of a whole hole, which the
    repeating hole grid hides from whole-image alignment.
    """
    h, w = ref_gray.shape
    size = round(PATCH_HALF_PITCHES * hole_pitch_px(cal.holes))
    window = cv2.createHanningWindow((2 * size, 2 * size), cv2.CV_32F)
    aligned = cv2.warpPerspective(cur_gray, matrix, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    centres, shifts = [], []
    for x, y in _patch_centres(cal):
        xi, yi = round(x), round(y)
        if xi - size < 0 or yi - size < 0 or xi + size > w or yi + size > h:
            continue
        ref_patch = ref_gray[yi - size : yi + size, xi - size : xi + size]
        cur_patch = aligned[yi - size : yi + size, xi - size : xi + size]
        (dx, dy), response = cv2.phaseCorrelate(ref_patch, cur_patch, window)
        if response >= MIN_PATCH_RESPONSE:
            centres.append((x, y))
            shifts.append((dx, dy))
    return np.float64(centres).reshape(-1, 2), np.float64(shifts).reshape(-1, 2)


def _patch_refine(
    cal: Calibration, ref_gray: np.ndarray, cur_gray: np.ndarray, start: np.ndarray
) -> tuple[np.ndarray, float] | None:
    """Correct `start` (a rotation + shift) for perspective, from the patches' leftover shifts.

    Returns the refined homography and the median leftover shift (px), or None if too few patches
    could be measured.
    """
    pitch = hole_pitch_px(cal.holes)
    matrix = np.float64(start)
    for _ in range(PATCH_ROUNDS):
        centres, shifts = _patch_shifts(cal, ref_gray, cur_gray, matrix)
        keep = np.hypot(*shifts.T) < 1.5 * pitch if len(shifts) else np.zeros(0, bool)
        if keep.sum() < MIN_PATCHES:
            return None
        # Where each patch centre actually is in the current frame.
        seen = cv2.perspectiveTransform((centres[keep] + shifts[keep]).reshape(-1, 1, 2), matrix).reshape(-1, 2)
        found, _ = cv2.findHomography(centres[keep], seen, cv2.RANSAC, 2.0)
        if found is None:
            return None
        matrix = found
    _, shifts = _patch_shifts(cal, ref_gray, cur_gray, matrix)
    if len(shifts) < MIN_PATCHES:
        return None
    return matrix, float(np.median(np.hypot(*shifts.T)))


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
    """How the board moved since calibration.

    First a rotation + shift, found robustly from coarse to fine. Tries, in order: `start` (e.g.
    the last tracked position), "hasn't moved", and only then the board outline detected in this
    frame, which can wobble on faint board edges. Then a perspective correction from local patch
    alignment: when the board moves under a camera that isn't perfectly overhead, its image also
    changes shape, and the repeating hole grid can let whole-image alignment slip by a hole in
    places. Raises CalibrationError rather than returning an alignment that isn't precise.
    """
    if frame.shape[:2] != cal.reference.shape[:2]:
        raise CalibrationError(
            f"camera resolution changed ({frame.shape[1]}x{frame.shape[0]}, calibrated at "
            f"{cal.frame_size[0]}x{cal.frame_size[1]}); run `benchlog camera calibrate`"
        )
    ref_gray, cur_gray = _gray(cal.reference), _gray(frame)
    identity = np.eye(3)
    starts = ([Tracking(start, 0).matrix] if start is not None else []) + [identity, None]
    best: Tracking | None = None
    for candidate in starts:
        if candidate is None:
            outline = _outline_estimate(cal, frame)  # only computed if the others didn't lock
            if outline is None:
                continue
            candidate = np.vstack([outline, [0, 0, 1]])
        # A starting homography is reduced to its rotation + shift for the robust stage.
        angle = np.arctan2(candidate[1, 0], candidate[0, 0])
        c, si = np.cos(angle), np.sin(angle)
        rigid = np.array([[c, -si, candidate[0, 2]], [si, c, candidate[1, 2]], [0, 0, 1]])
        try:
            matrix, correlation = _ecc(cal, ref_gray, cur_gray, rigid, cv2.MOTION_EUCLIDEAN, _ECC_SCALES)
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

    refined = _patch_refine(cal, ref_gray, cur_gray, best.matrix)
    if refined is None or refined[1] > MAX_PATCH_RESIDUAL_PX:
        raise CalibrationError(
            "can't line the board up with the calibration precisely (something covering it, or the camera "
            "moved?); scan again, or run `benchlog camera calibrate`"
        )
    return Tracking(matrix=refined[0], correlation=best.correlation)


# ── Classification ────────────────────────────────────────────────────────────


@dataclass
class Occupancy:
    changed: list[str]  # holes that look different from the reference image
    occupied: list[str]  # holes occupied now: changed XOR occupied at calibration
    diffs: dict[str, float] = field(repr=False)
    threshold: float


def _lab(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)


def change_map(cal: Calibration, frame: np.ndarray, tracking: Tracking) -> tuple[np.ndarray, np.ndarray]:
    """How much each pixel differs from the calibration image, and the frame lined up with it.

    Both are in the calibration image's coordinates (top-down, holes where `cal.holes` says).
    """
    h, w = cal.reference.shape[:2]
    # Bring the current frame into the reference's coordinates so every hole lines up exactly.
    aligned = cv2.warpPerspective(frame, tracking.matrix, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    # A light blur on both sides evens out the softening that resampling adds at sharp edges.
    ref_lab = _lab(cv2.GaussianBlur(cal.reference, (0, 0), BLUR_SIGMA))
    cur_lab = _lab(cv2.GaussianBlur(aligned, (0, 0), BLUR_SIGMA))

    # Compare each spot with its surroundings, not raw colors: lighting that varies across the board
    # (the board moved under the lamp, a shadow) is smooth and cancels; a wire is sharp and doesn't.
    pitch = hole_pitch_px(cal.holes)
    ref_lab -= cv2.GaussianBlur(ref_lab, (0, 0), LIGHTING_SIGMA_PITCH * pitch)
    cur_lab -= cv2.GaussianBlur(cur_lab, (0, 0), LIGHTING_SIGMA_PITCH * pitch)

    return np.linalg.norm(cur_lab - ref_lab, axis=2), aligned


def classify(cal: Calibration, frame: np.ndarray, tracking: Tracking) -> Occupancy:
    """Holes occupied now, judged by whether each looks different from its calibration appearance."""
    distance, _ = change_map(cal, frame, tracking)
    pitch = hole_pitch_px(cal.holes)
    r = max(2, round(PATCH_RADIUS_PITCH * pitch))
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


def debug_image(cal: Calibration, frame: np.ndarray, occupancy: Occupancy, top: int = 10) -> np.ndarray:
    """The frame lined up with the calibration, every hole colored by how much it changed.

    Green = unchanged, yellow = halfway to the threshold, red = at or over it (called changed).
    The `top` most-changed holes are labeled with their score.
    """
    tracking = track(cal, frame)
    h, w = cal.reference.shape[:2]
    out = cv2.warpPerspective(frame, tracking.matrix, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    pitch = hole_pitch_px(cal.holes)
    radius = max(2, round(pitch * 0.2))
    for name, (x, y) in cal.holes.items():
        t = min(1.0, occupancy.diffs[name] / occupancy.threshold)
        color = (0, round(255 * min(1.0, 2 * (1 - t))), round(255 * min(1.0, 2 * t)))  # BGR green -> red
        cv2.circle(out, (round(x), round(y)), radius, color, -1, cv2.LINE_AA)
        if name in occupancy.changed:
            cv2.circle(out, (round(x), round(y)), round(pitch * 0.55), (0, 0, 255), 2, cv2.LINE_AA)
    for name in sorted(occupancy.diffs, key=occupancy.diffs.get, reverse=True)[:top]:
        x, y = cal.holes[name]
        label = f"{name} {occupancy.diffs[name]:.0f}"
        org = (round(x + pitch * 0.6), round(y - pitch * 0.6))
        cv2.putText(out, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def colored_holes(frame: np.ndarray, holes: dict[str, tuple[float, float]]) -> list[str]:
    """Terminal holes that look colored compared with the rest: likely a wire on a board meant to be empty.

    Judged on color only (not brightness), so shadows and the dark surface around the board don't
    count; jumper insulation is colored, board plastic isn't. Rails are skipped, since their printed
    red and blue lines sit right next to the holes.
    """
    lab = cv2.cvtColor(cv2.GaussianBlur(frame, (0, 0), BLUR_SIGMA), cv2.COLOR_BGR2LAB).astype(np.float32)
    r = max(2, round(PATCH_RADIUS_PITCH * hole_pitch_px(holes)))
    names = [n for n in holes if n[:2] not in RAILS]
    h, w = lab.shape[:2]
    chroma = []
    for n in names:
        x, y = round(holes[n][0]), round(holes[n][1])
        patch = lab[max(0, y - r) : min(h, y + r + 1), max(0, x - r) : min(w, x + r + 1), 1:]
        chroma.append(patch.reshape(-1, 2).mean(axis=0) if patch.size else np.zeros(2))
    chroma = np.array(chroma)
    distance = np.linalg.norm(chroma - np.median(chroma, axis=0), axis=1)
    median = float(np.median(distance))
    threshold = max(MIN_COLOR_DIFF, median + NOISE_SIGMAS * 1.4826 * float(np.median(np.abs(distance - median))))
    return sorted((n for n, d in zip(names, distance) if d > threshold), key=lambda n: (n[0], int(n[1:])))


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
    row1_left: bool = False  # board numbered from the other end (the layout has row 1 on the right)
    expect_empty: bool = True  # False when calibrating with the circuit built
    confirm_save: bool = False  # the user was warned the board doesn't look empty; Enter again saves

    def named_holes(self) -> dict[str, tuple[float, float]] | None:
        """The hole map with names matching the board's printed numbering."""
        if self.holes is None:
            return None
        return flip_row_names(self.holes) if self.row1_left else self.holes

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
        if key == -1:  # no key pressed this frame
            return None
        if key in (27, ord("q")):
            return "quit"
        if key in (13, 10, 32):
            if self.holes is None:
                self.message = "no hole map yet: wait for the board or drag the corner handles"
                return None
            if self.expect_empty and not self.confirm_save:
                found = colored_holes(frame, self.named_holes())
                if found:
                    shown = ", ".join(found[:6]) + (" ..." if len(found) > 6 else "")
                    self.message = f"board doesn't look empty ({shown}): remove it and press Enter, or Enter again to save anyway"
                    self.confirm_save = True
                    return None
            return "save"
        self.confirm_save = False  # any other key: check again on the next Enter
        if key == ord("a"):  # follow the detected board again
            self.auto, self.locked, self.message = True, False, ""
        elif key in (ord("1"), ord("2"), ord("3"), ord("4")):
            self.selected = key - ord("1")
        elif key == ord("r"):
            self.row1_left = not self.row1_left
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
        draw_holes(out, editor.named_holes())
    if editor.corners is not None:
        cv2.polylines(out, [editor.corners.astype(np.int32)], True, (0, 220, 255), 2, cv2.LINE_AA)
        for i, (x, y) in enumerate(editor.corners):
            color = (0, 255, 255) if i == editor.selected else (255, 255, 255)
            cv2.circle(out, (round(x), round(y)), 12, color, 2, cv2.LINE_AA)
            cv2.putText(out, str(i + 1), (round(x) + 14, round(y) - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    mode = "AUTO" if editor.auto else "MANUAL"
    lock = f"LOCKED on holes ({editor.residual:.1f} px)" if editor.locked else "placed by hand" if not editor.auto else "not locked"
    lines = [
        f"{mode} - {lock}. Check every dot sits in a hole and the yellow row numbers match the board's, then Enter",
        "drag corners, or 1-4 + arrows/ijkl to nudge   f: snap now   a: re-detect   r: flip row numbering   Enter: save   Esc: cancel",
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

    editor = CalibrationEditor(expect_empty=not occupied)
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
                    corners=editor.corners, holes=editor.named_holes(), reference=frame.copy(), camera=camera, occupied=list(occupied)
                )
    finally:
        cv2.destroyAllWindows()
