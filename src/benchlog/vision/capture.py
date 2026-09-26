"""BB830 baseline capture: "mobile check deposit" style scan of an empty board.

Point a camera (iPhone, over Camo/iVCam/DroidCam, or a still photo) at a BB830,
line it up inside the grey guide outline, and this module confirms a BB830 is
present, locates all 830 holes precisely, checks it is empty, and saves that as
the baseline future scans get diffed against. Owner: Person 4.

Pipeline (pure functions, testable without a camera):
  detect_board(frame)        -> 4 corners | None
  warp_from_corners(...)     -> canonical 1651x546 image (10 px/mm)
  register_holes(warped)     -> {hole: (x, y) px}, residual_px  (fits detected
                                 holes; corrects for imperfect corner detection)
  is_upside_down(warped)     -> bool          (rail +/- line order check)
  classify_occupancy(...)    -> occupied ids, warnings
  analyse_image(frame)       -> ScanResult    (runs the whole pipeline once)
  save_baseline(result, ...) -> writes .benchlog/baseline/*

The live loop (`_run_live`) just calls `detect_board` every frame for guide
overlay + alignment feedback, then `analyse_image` once on capture.

Getting an iPhone into OpenCV on Windows: install Camo, iVCam or DroidCam on
both the phone and this laptop; the phone then shows up as a normal webcam, so
`cv2.VideoCapture(0)` (or `--camera 1` / `--camera 2` if 0 is the laptop's own
webcam) picks it up. Tips: put the board on a dark, matte, uncluttered surface,
light it evenly (avoid glare/shadows), and hold the phone parallel to the board
(square to it, not tilted) -- perspective is corrected, but a steep angle hides
holes near the far edge.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from benchlog.vision.bb830_layout import (
    BOARD_H_MM,
    BOARD_W_MM,
    PITCH_MM,
    guide_outline_mm,
    hole_positions_mm,
)

# ── Canonical warp: 10 px per mm ────────────────────────────────────────────
PX_PER_MM = 10
WARP_W = round(BOARD_W_MM * PX_PER_MM)  # 1651
WARP_H = round(BOARD_H_MM * PX_PER_MM)  # 546
PITCH_PX = PITCH_MM * PX_PER_MM  # 25.4

_HOLE_MM = hole_positions_mm()
_HOLE_PX_TEMPLATE: dict[str, tuple[float, float]] = {
    h: (x * PX_PER_MM, y * PX_PER_MM) for h, (x, y) in _HOLE_MM.items()
}

# Board aspect (~3.02); perspective widens this range a bit for live detection.
BOARD_ASPECT = BOARD_W_MM / BOARD_H_MM
ASPECT_MIN, ASPECT_MAX = 2.6, 3.5

DEFAULT_OUT_DIR = Path(".benchlog/baseline")

SAMPLE_R = max(3, round(PITCH_PX * 0.22))  # half-size of the patch read at a hole centre
_BG_BLUR_KSIZE = round(PITCH_PX * 0.8) | 1  # odd kernel; smooths over thin rail lines
CONTRAST_RATIO_THRESH = 0.35  # empty hole must be this much darker than its surroundings
SATURATION_THRESH = 70.0  # 0-255; coloured wire insulation saturates well above this
RELIABLE_CONTRAST_MEDIAN = 0.08  # below this, contrast-based emptiness is not trustworthy
MAX_RELIABLE_RESIDUAL_PX = 5.0  # above this, per-hole sampling may land off-hole


# ── Result + baseline ────────────────────────────────────────────────────────


@dataclass
class ScanResult:
    present: bool
    corners: np.ndarray | None
    residual_px: float
    upside_down: bool
    empty: bool
    occupied: list[str]
    warnings: list[str]
    warped: np.ndarray | None
    hole_px: dict[str, tuple[float, float]] = field(default_factory=dict)
    hole_stats: dict[str, tuple[float, float, float]] = field(default_factory=dict)


def annotate_warped(result: ScanResult) -> np.ndarray:
    """Warped image with every hole dotted: grey = empty, red = occupied."""
    assert result.warped is not None
    out = result.warped.copy()
    occupied = set(result.occupied)
    for hole, (x, y) in result.hole_px.items():
        color = (0, 0, 220) if hole in occupied else (160, 160, 160)
        cv2.circle(out, (round(x), round(y)), 2, color, -1)
    return out


def save_baseline(result: ScanResult, raw_frame: np.ndarray, out_dir: Path = DEFAULT_OUT_DIR) -> Path:
    """Write baseline_raw.jpg, baseline_warped.png, baseline_annotated.png, baseline.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / "baseline_raw.jpg"), raw_frame)
    if result.warped is not None:
        cv2.imwrite(str(out_dir / "baseline_warped.png"), result.warped)
        cv2.imwrite(str(out_dir / "baseline_annotated.png"), annotate_warped(result))
    data = {
        "board": "bb830",
        "px_per_mm": PX_PER_MM,
        "present": result.present,
        "corners": result.corners.tolist() if result.corners is not None else None,
        "residual_px": result.residual_px,
        "upside_down": result.upside_down,
        "empty": result.empty,
        "occupied": result.occupied,
        "warnings": result.warnings,
        "holes": {
            h: {"px": list(result.hole_px[h]), "bgr_mean": list(result.hole_stats[h])}
            for h in result.hole_px
        },
    }
    (out_dir / "baseline.json").write_text(json.dumps(data, indent=2))
    return out_dir


# ── Board detection ──────────────────────────────────────────────────────────


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Sort 4 points into [top-left, top-right, bottom-right, bottom-left]."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        dtype=np.float32,
    )


BORDER_TOUCH_TOL_FRAC = 0.01  # fraction of the relevant frame dimension counted as "touching"
BORDER_TOUCH_SIDES_REJECT = 3  # a real board photographed with margin never fills the frame
CONTRAST_BAND_FRAC = 0.015  # width of the ring sampled just outside a candidate quad
CANDIDATE_CONTRAST_MARGIN = 10.0  # min gray-level gap between a candidate and its surroundings


def _border_touch_count(box: np.ndarray, frame_w: int, frame_h: int) -> int:
    """How many of the 4 frame edges a candidate's bounding box touches/nearly touches.

    A board photographed with the intended margin around it never fills the
    frame; a spurious contour that swallowed the whole background does (often
    on 3 or all 4 sides at once). tol scales with frame size so this behaves
    the same at any resolution.
    """
    x, y, w, h = cv2.boundingRect(box.astype(np.int32))
    tol_x = max(2.0, frame_w * BORDER_TOUCH_TOL_FRAC)
    tol_y = max(2.0, frame_h * BORDER_TOUCH_TOL_FRAC)
    touches = 0
    touches += x <= tol_x
    touches += y <= tol_y
    touches += (frame_w - (x + w)) <= tol_x
    touches += (frame_h - (y + h)) <= tol_y
    return touches


def _candidate_contrasts(gray: np.ndarray, box: np.ndarray, method: str) -> bool:
    """Does this candidate contrast with its surroundings the way `method` assumes?

    `bright`/`canny` assume a board that's lighter than what's just outside it
    (the "dark matte surface" setup); `white_on_white` assumes the opposite
    (board slightly darker than an even brighter studio background). A
    candidate that fails this is very likely a background blob that happened
    to have a plausible aspect ratio, not the board.
    """
    if method == "canny":
        return True  # edge-based; no brightness direction assumed
    h, w = gray.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillConvexPoly(mask, box.astype(np.int32), 255)
    band = max(3, round(CONTRAST_BAND_FRAC * max(w, h)))
    kernel = np.ones((band * 2 + 1, band * 2 + 1), np.uint8)
    ring = cv2.bitwise_and(cv2.dilate(mask, kernel), cv2.bitwise_not(mask))
    if mask.sum() == 0 or ring.sum() == 0:
        return False
    inside = float(gray[mask > 0].mean())
    outside = float(gray[ring > 0].mean())
    if method == "bright":
        return outside < inside - CANDIDATE_CONTRAST_MARGIN
    return outside > inside + CANDIDATE_CONTRAST_MARGIN  # white_on_white


def detect_board(frame: np.ndarray, prefer_near: np.ndarray | None = None) -> np.ndarray | None:
    """Find the breadboard body: a bright, large quadrilateral of plausible aspect.

    Searches the whole frame; if `prefer_near` (e.g. the guide corners) is given,
    picks the candidate whose centre is closest to it, otherwise the largest.
    Returns 4 ordered corners [TL, TR, BR, BL], or None if nothing plausible.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    frame_h, frame_w = frame.shape[:2]
    frame_area = frame_h * frame_w

    candidates: list[np.ndarray] = []
    for method in ("bright", "white_on_white", "canny"):
        if method == "bright":
            # Board is a bright quad against a darker background (the intended,
            # "dark matte surface" setup).
            thresh, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            _, edges = cv2.threshold(blur, max(thresh, 150), 255, cv2.THRESH_BINARY)
        elif method == "white_on_white":
            # White board photographed against an even-brighter white/studio
            # background: the board is *slightly* darker, not lighter.
            _, edges = cv2.threshold(blur, 250, 255, cv2.THRESH_BINARY_INV)
        else:
            edges = cv2.dilate(cv2.Canny(blur, 30, 100), None, iterations=2)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
            if cv2.contourArea(cnt) < 0.15 * frame_area:
                continue
            # minAreaRect fits all contour points at once, so small notches (the
            # board's interlocking tabs) don't throw a single corner off the way
            # a 4-point approxPolyDP pick can.
            (_, (rw, rh), _angle) = cv2.minAreaRect(cnt)
            if rw < 1 or rh < 1:
                continue
            aspect = max(rw, rh) / min(rw, rh)
            if not (ASPECT_MIN <= aspect <= ASPECT_MAX):
                continue
            box = cv2.boxPoints(cv2.minAreaRect(cnt))
            # A board photographed with margin never fills the frame; a blob
            # that merged the board with the background (e.g. white_on_white's
            # fixed 250 threshold catching a dark background too) typically
            # does, on most/all sides.
            if _border_touch_count(box, frame_w, frame_h) >= BORDER_TOUCH_SIDES_REJECT:
                continue
            if not _candidate_contrasts(blur, box, method):
                continue
            candidates.append(_order_corners(box))

    if not candidates:
        return None
    if prefer_near is not None:
        target = prefer_near.reshape(4, 2).mean(axis=0)
        candidates.sort(key=lambda c: float(np.linalg.norm(c.mean(axis=0) - target)))
    else:
        candidates.sort(key=lambda c: -cv2.contourArea(c))
    return candidates[0]


# ── Guide overlay (the "check deposit" frame) ────────────────────────────────


def guide_corners(frame_w: int, frame_h: int, fraction: float = 0.85) -> np.ndarray:
    """Guide rectangle [TL, TR, BR, BL] centred in a frame of the given size."""
    w = frame_w * fraction
    h = w / BOARD_ASPECT
    if h > frame_h * fraction:
        h = frame_h * fraction
        w = h * BOARD_ASPECT
    cx, cy = frame_w / 2, frame_h / 2
    hw, hh = w / 2, h / 2
    return np.float32(
        [[cx - hw, cy - hh], [cx + hw, cy - hh], [cx + hw, cy + hh], [cx - hw, cy + hh]]
    )


def draw_guide_overlay(frame: np.ndarray, status: str) -> np.ndarray:
    """Dim outside the guide, draw the board outline/holes/labels and a status line."""
    out = frame.copy()
    h, w = out.shape[:2]
    guide = guide_corners(w, h)

    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [guide.astype(np.int32)], 255)
    dim = (out.astype(np.float32) * 0.35).astype(np.uint8)
    out = np.where(mask[:, :, None] > 0, out, dim)

    x0, y0 = guide[0]
    sx = (guide[1][0] - guide[0][0]) / BOARD_W_MM
    sy = (guide[3][1] - guide[0][1]) / BOARD_H_MM

    def to_px(pt: tuple[float, float]) -> tuple[int, int]:
        return (round(x0 + pt[0] * sx), round(y0 + pt[1] * sy))

    cv2.polylines(out, [guide.astype(np.int32)], True, (180, 180, 180), 2)
    for x, y in _HOLE_MM.values():
        cv2.circle(out, to_px((x, y)), 1, (170, 170, 170), -1)

    outline = guide_outline_mm()
    for y in outline.rail_line_ys:
        cv2.line(out, to_px((0, y)), to_px((BOARD_W_MM, y)), (170, 170, 170), 1)
    for y in outline.centre_channel:
        cv2.line(out, to_px((0, y)), to_px((BOARD_W_MM, y)), (170, 170, 170), 1)

    row_mid_y = (outline.centre_channel[0] + PITCH_MM * 2)  # roughly row-C height
    cv2.putText(out, "63", to_px((-14, 26)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
    cv2.putText(out, "1", to_px((BOARD_W_MM + 4, 26)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
    cv2.putText(out, "A", to_px((-12, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
    cv2.putText(out, "J", to_px((-12, row_mid_y + 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

    cv2.putText(out, status, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return out


@dataclass
class Alignment:
    status: str
    aligned: bool


def check_alignment(corners: np.ndarray | None, guide: np.ndarray) -> Alignment:
    """Compare detected corners to the guide and describe what the user should do."""
    if corners is None:
        return Alignment("No breadboard found — place a BB830 on a dark, matte surface", False)

    diag = float(np.linalg.norm(guide[2] - guide[0]))
    deviation = max(float(np.linalg.norm(c - g)) for c, g in zip(corners, guide))
    if deviation < 0.04 * diag:
        return Alignment("Hold steady…", True)

    area_c = cv2.contourArea(corners.astype(np.float32))
    area_g = cv2.contourArea(guide.astype(np.float32))
    ratio = area_c / area_g if area_g else 0.0
    if ratio < 0.85:
        return Alignment("Move closer", False)
    if ratio > 1.15:
        return Alignment("Move back", False)

    if float(np.linalg.norm(corners.mean(axis=0) - guide.mean(axis=0))) > 0.08 * diag:
        return Alignment("Centre the board in the outline", False)

    v_c = corners[1] - corners[0]
    v_g = guide[1] - guide[0]
    angle = abs(np.degrees(np.arctan2(v_c[1], v_c[0]) - np.arctan2(v_g[1], v_g[0])))
    if angle > 6:
        return Alignment("Rotate to match the outline", False)

    return Alignment("Almost there… hold steady", False)


# ── Registration ──────────────────────────────────────────────────────────────


def warp_from_corners(frame: np.ndarray, corners: np.ndarray) -> np.ndarray:
    dst = np.float32([[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]])
    homography = cv2.getPerspectiveTransform(corners.astype(np.float32), dst)
    return cv2.warpPerspective(frame, homography, (WARP_W, WARP_H))


def detect_hole_blobs(warped: np.ndarray) -> np.ndarray:
    """Dark, roughly-square blob centroids in the warped image (candidate holes).

    Otsu (not a fixed 130): a fixed cutoff assumes the fixture's own
    brightness. Any blur that softens hole edges (a few degrees of camera
    tilt, a lower-resolution capture, dimmer lighting) lifts the holes'
    darkest pixels above a fixed threshold and silently drops most of the
    board's blobs -- which starves the registration fit below of real
    correspondences and lets it lock onto a self-consistent-but-wrong
    lattice offset (see register_holes). Otsu tracks the image's own
    dark/light split instead.
    """
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    otsu_t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, dark = cv2.threshold(gray, min(otsu_t, 130) + 20, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(dark, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    amin, amax = (PITCH_PX * 0.25) ** 2, (PITCH_PX * 0.75) ** 2
    pts = []
    for c in contours:
        area = cv2.contourArea(c)
        if not (amin <= area <= amax):
            continue
        x, y, w, h = cv2.boundingRect(c)
        if not (0.4 < w / max(h, 1) < 2.5):
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        pts.append((m["m10"] / m["m00"], m["m01"] / m["m00"]))
    return np.array(pts, dtype=np.float32)


def register_holes(
    warped: np.ndarray, max_iterations: int = 6, max_match_dist: float = PITCH_PX * 0.9
) -> tuple[dict[str, tuple[float, float]], float]:
    """Fit the template hole grid onto real detected holes (a few ICP-style iterations).

    Corrects for imperfect corner detection in the warp. Coarse-to-fine: the
    nearest-neighbour match distance starts generous (most of a pitch, since an
    imprecise perspective warp can leave a systematic error that size at the far
    edge of the board) and tightens each iteration as the fit improves, so
    every hole gets a chance to be matched before mismatches are excluded.
    Returns the corrected per-hole pixel positions and the median residual
    distance to the nearest real hole blob, in warped-image pixels.
    """
    names = list(_HOLE_PX_TEMPLATE)
    template = np.array([_HOLE_PX_TEMPLATE[n] for n in names], dtype=np.float32)
    blobs = detect_hole_blobs(warped)
    if len(blobs) < 10:
        fallback = {n: (float(p[0]), float(p[1])) for n, p in zip(names, template)}
        return fallback, float("inf")

    current = template.copy()
    match_dist = max_match_dist
    for _ in range(max_iterations):
        dist = np.linalg.norm(current[:, None, :] - blobs[None, :, :], axis=2)
        nn_idx = dist.argmin(axis=1)
        nn_dist = dist[np.arange(len(current)), nn_idx]
        good = nn_dist < match_dist
        if good.sum() < 10:
            break
        # A homography (not just affine): the residual error left by an
        # imprecise perspective warp of imprecise detected corners is itself a
        # small projective distortion, which an affine fit can only
        # approximate but a homography can absorb directly.
        #
        # RANSAC (not LMEDS): the template's own mm measurements carry a
        # systematic offset from the real board (see bb830_layout module
        # docstring -- "measurement method") that's already a meaningful
        # fraction of a pitch, so even a couple of extra px of corner-detection
        # error is enough to push some holes into the wrong nearest-neighbour
        # bin. LMEDS has no explicit inlier threshold and would occasionally
        # settle on a self-consistent-but-wrong fit (all holes matched one
        # pitch off) instead of the true one; RANSAC with a tight pitch-scaled
        # reprojection threshold reliably rejects those mismatches instead.
        matrix, _ = cv2.findHomography(
            template[good], blobs[nn_idx[good]], method=cv2.RANSAC, ransacReprojThreshold=PITCH_PX * 0.2
        )
        if matrix is None:
            break
        current = cv2.perspectiveTransform(template.reshape(-1, 1, 2), matrix).reshape(-1, 2)
        match_dist = max(PITCH_PX * 0.4, match_dist * 0.7)

    dist = np.linalg.norm(current[:, None, :] - blobs[None, :, :], axis=2)
    nn_idx = dist.argmin(axis=1)
    nn_dist = dist[np.arange(len(current)), nn_idx]
    residual = float(np.median(nn_dist))

    # Final per-hole snap to the nearest real blob, but only within a tight
    # radius -- this mops up the last few px of error the global fit leaves at
    # the far edges of the board. A hole with no blob nearby (no matching dark
    # square -- most likely occupied) keeps its global-fit position instead of
    # snapping to some unrelated neighbour.
    snap = nn_dist < PITCH_PX * 0.3
    current[snap] = blobs[nn_idx[snap]]

    hole_px = {n: (float(p[0]), float(p[1])) for n, p in zip(names, current)}
    return hole_px, residual


def _redness(patch: np.ndarray) -> float:
    """Mean LAB 'a' channel: higher = more red, ~128 = neutral grey/black/white."""
    return float(cv2.cvtColor(patch, cv2.COLOR_BGR2LAB)[:, :, 1].mean())


def is_upside_down(warped: np.ndarray) -> bool:
    """True if the top rail's black line sits above its red line (board rotated 180deg)."""
    top_plus_y, top_minus_y, _, _ = guide_outline_mm().rail_line_ys
    h = warped.shape[0]
    above = round((top_plus_y - 1.5) * PX_PER_MM)
    below = round((top_minus_y + 1.5) * PX_PER_MM)
    band_above = warped[max(0, above - 2) : max(0, above) + 2, :]
    band_below = warped[min(h, below) - 2 : min(h, below + 2), :]
    if band_above.size == 0 or band_below.size == 0:
        return False
    return _redness(band_below) > _redness(band_above) + 3.0


# ── Empty check ───────────────────────────────────────────────────────────────


def _patch(img: np.ndarray, x: float, y: float, r: int) -> np.ndarray:
    h, w = img.shape[:2]
    xi, yi = round(x), round(y)
    patch = img[max(0, yi - r) : min(h, yi + r + 1), max(0, xi - r) : min(w, xi + r + 1)]
    if patch.size == 0:
        return np.zeros((1,) if img.ndim == 2 else (1, img.shape[2]), dtype=np.float32)
    return patch.reshape(-1, *patch.shape[2:]).astype(np.float32)


def _patch_mean(img: np.ndarray, x: float, y: float, r: int) -> np.ndarray:
    return _patch(img, x, y, r).mean(axis=0)


def _patch_percentile(img: np.ndarray, x: float, y: float, r: int, q: float) -> float:
    return float(np.percentile(_patch(img, x, y, r), q))


def sample_hole_stats(
    warped: np.ndarray, hole_px: dict[str, tuple[float, float]]
) -> dict[str, tuple[float, float, float]]:
    """Mean BGR of the patch at each hole centre -- stored in the baseline for later diffing."""
    return {h: tuple(float(v) for v in _patch_mean(warped, x, y, SAMPLE_R)) for h, (x, y) in hole_px.items()}


def classify_occupancy(
    warped: np.ndarray, hole_px: dict[str, tuple[float, float]]
) -> tuple[list[str], list[str]]:
    """Classify each hole empty/occupied from local contrast + saturation.

    An empty hole is a small dark, low-saturation square surrounded by bright
    plastic. Comparing against a median-blurred background (rather than one
    global threshold) makes this robust to lighting gradients, and the blur
    kernel (~0.8x pitch) averages away the thin printed rail lines while still
    picking up a wire's colour, since a jumper is much wider than the lines.
    Hole darkness/saturation are read as a percentile rather than a plain mean
    over the sample patch, so a hole that isn't perfectly registered (its real
    centre a few px off) is still judged from its darkest/most-coloured pixels
    instead of being diluted by the surrounding plastic the patch also covers.
    """
    ksize = _BG_BLUR_KSIZE if _BG_BLUR_KSIZE % 2 else _BG_BLUR_KSIZE + 1
    bg = cv2.medianBlur(warped, ksize)
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    sat = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)[:, :, 1]

    occupied: list[str] = []
    ratios: list[float] = []
    for hole, (x, y) in hole_px.items():
        v_hole = _patch_percentile(gray, x, y, SAMPLE_R, 20)
        v_bg = float(_patch_mean(bg_gray, x, y, SAMPLE_R))
        s_hole = _patch_percentile(sat, x, y, SAMPLE_R, 80)
        ratio = (v_bg - v_hole) / max(v_bg, 1.0)
        ratios.append(ratio)
        if ratio < CONTRAST_RATIO_THRESH or s_hole > SATURATION_THRESH:
            occupied.append(hole)

    warnings: list[str] = []
    if float(np.median(ratios)) < RELIABLE_CONTRAST_MEDIAN:
        warnings.append(
            "low hole/background contrast across the whole board (translucent board?) "
            "— empty/occupied result may be unreliable"
        )
    return occupied, warnings


# ── Whole pipeline ────────────────────────────────────────────────────────────


def ensure_landscape(frame: np.ndarray) -> np.ndarray:
    """Rotate a portrait frame 90 deg so the board's long axis is horizontal.

    A phone mounted pointing straight down at the board can hand OpenCV a
    portrait frame (e.g. 1080x1920); guide_corners() fits the landscape
    3:1 guide into the *narrow* dimension in that case, so the board only
    gets a fraction of the frame's actual resolution and registration
    residual balloons. Rotating one way may leave the board upside down;
    the existing is_upside_down() check catches that and asks the user to
    flip the board, same as it would for a landscape capture.
    """
    h, w = frame.shape[:2]
    if h > w:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    return frame


def analyse_image(frame: np.ndarray) -> ScanResult:
    """Run detection -> registration -> orientation -> emptiness on one frame."""
    frame = ensure_landscape(frame)
    corners = detect_board(frame)
    if corners is None:
        return ScanResult(
            present=False,
            corners=None,
            residual_px=float("inf"),
            upside_down=False,
            empty=False,
            occupied=[],
            warnings=["No breadboard found — place a BB830 on a dark, matte surface"],
            warped=None,
        )

    warped = warp_from_corners(frame, corners)
    hole_px, residual = register_holes(warped)
    upside_down = is_upside_down(warped)
    occupied, warnings = classify_occupancy(warped, hole_px)
    if residual > MAX_RELIABLE_RESIDUAL_PX:
        warnings.append(
            f"registration error is high ({residual:.1f} px) — empty/occupied result may be "
            "unreliable (seen on translucent boards, where holes are harder to detect precisely)"
        )
    if upside_down:
        warnings = ["Board is upside down — row 1 should be on the right"] + warnings
    stats = sample_hole_stats(warped, hole_px)

    return ScanResult(
        present=True,
        corners=corners,
        residual_px=residual,
        upside_down=upside_down,
        empty=len(occupied) == 0,
        occupied=occupied,
        warnings=warnings,
        warped=warped,
        hole_px=hole_px,
        hole_stats=stats,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def _verdict(result: ScanResult, out_dir: Path) -> str:
    if not result.present:
        return "BB830 detected ✗ — no breadboard found"
    present = "✓"
    aligned = "✓"  # a still image is analysed as-is; live mode only gets here once aligned
    empty = "✓" if result.empty else "✗"
    n_empty = len(result.hole_px) - len(result.occupied)
    line = (
        f"BB830 detected {present}  aligned {aligned}  empty {empty}  "
        f"({n_empty}/{len(result.hole_px)} holes empty, registration error {result.residual_px:.1f} px)"
        f" → saved {out_dir}"
    )
    if result.upside_down:
        line += "  [UPSIDE DOWN]"
    return line


MAX_WINDOW_W = 1400  # a 1920x1080+ preview shouldn't overflow a laptop screen


def _show_fit(window_name: str, img: np.ndarray) -> None:
    """cv2.imshow that fits the window to a laptop screen instead of the raw frame size."""
    h, w = img.shape[:2]
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    if w > MAX_WINDOW_W:
        cv2.resizeWindow(window_name, MAX_WINDOW_W, round(h * MAX_WINDOW_W / w))
    cv2.imshow(window_name, img)


def _finish(result: ScanResult, raw_frame: np.ndarray, out_dir: Path, show: bool) -> int:
    if result.present:
        save_baseline(result, raw_frame, out_dir)
    print(_verdict(result, out_dir))
    for w in result.warnings:
        print(f"  warning: {w}")
    if show and result.warped is not None:
        _show_fit("benchlog baseline", annotate_warped(result))
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    return 0 if (result.present and result.empty) else 1


MAX_CONSECUTIVE_READ_FAILURES = 60  # ~2s at 30fps; Continuity Camera can hiccup transiently

# Live preview only needs to be accurate enough to draw a guide box and judge
# alignment; running detect_board on a downscaled copy and scaling the
# corners back up keeps every frame cheap so the loop stays responsive at
# camera framerate. The one-shot analyse_image() on the captured frame
# (full pipeline: registration + emptiness) still runs at full resolution.
LIVE_DETECT_WIDTH = 960


def _detect_board_downscaled(frame: np.ndarray, prefer_near: np.ndarray | None) -> np.ndarray | None:
    h, w = frame.shape[:2]
    if w <= LIVE_DETECT_WIDTH:
        return detect_board(frame, prefer_near=prefer_near)
    scale = LIVE_DETECT_WIDTH / w
    small = cv2.resize(frame, (LIVE_DETECT_WIDTH, round(h * scale)), interpolation=cv2.INTER_AREA)
    small_prefer = prefer_near * scale if prefer_near is not None else None
    corners = detect_board(small, prefer_near=small_prefer)
    return None if corners is None else corners / scale


def _run_live(camera_index: int, out_dir: Path) -> int:
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(
            f"Cannot open camera {camera_index}. Things to try:\n"
            "  - a different --camera index (0, 1, 2, ...) -- other apps or a "
            "built-in webcam can take index 0\n"
            "  - on macOS: System Settings -> Privacy & Security -> Camera -- "
            "grant access to Terminal (or whichever app runs this)\n"
            "  - make sure no other application is already using the camera"
        )
        return 2
    # Best effort; harmless if the camera/driver doesn't support it.
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

    stable_since: float | None = None
    last_corners: np.ndarray | None = None
    consecutive_failures = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                consecutive_failures += 1
                if consecutive_failures > MAX_CONSECUTIVE_READ_FAILURES:
                    print(
                        "Camera stopped delivering frames. Things to try:\n"
                        "  - a different --camera index\n"
                        "  - on macOS: check System Settings -> Privacy & Security -> Camera\n"
                        "  - reconnect/relaunch Continuity Camera on the iPhone"
                    )
                    return 2
                continue  # skip a transient hiccup instead of giving up on frame 1
            consecutive_failures = 0

            frame = ensure_landscape(frame)
            h, w = frame.shape[:2]
            guide = guide_corners(w, h)
            corners = _detect_board_downscaled(frame, prefer_near=guide)
            align = check_alignment(corners, guide)
            now = time.time()

            if align.aligned and corners is not None:
                jitter = (
                    float(np.max(np.linalg.norm(corners - last_corners, axis=1)))
                    if last_corners is not None
                    else None
                )
                if jitter is not None and jitter < 2.0:
                    stable_since = stable_since if stable_since is not None else now
                else:
                    stable_since = now
            else:
                stable_since = None
            last_corners = corners

            display = draw_guide_overlay(frame, align.status)
            if corners is not None:
                color = (0, 220, 0) if align.aligned else (0, 200, 255)
                cv2.polylines(display, [corners.astype(np.int32)], True, color, 2)
            _show_fit("benchlog - baseline capture", display)

            key = cv2.waitKey(1) & 0xFF
            auto_capture = stable_since is not None and (now - stable_since) > 1.0
            if key in (27, ord("q")):
                return 1
            if key == 32 or auto_capture:
                result = analyse_image(frame)
                return _finish(result, frame, out_dir, show=True)
    finally:
        cap.release()
        cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    # Some terminals (notably Windows' legacy codepage) can't encode the
    # checkmarks/arrows in the verdict line; fall back instead of crashing.
    # `reconfigure` only touches the error handler, not encoding, so this is
    # a no-op-ish safety net on platforms (macOS/Linux) that already do fine.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Capture a BB830 baseline scan (see module docstring).")
    parser.add_argument("--camera", type=int, default=0, help="camera index for live mode")
    parser.add_argument("--image", type=Path, default=None, help="analyse a still photo instead of the camera")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="baseline output directory")
    parser.add_argument("--no-show", action="store_true", help="don't open a result window")
    args = parser.parse_args(argv)

    if args.image is not None:
        frame = cv2.imread(str(args.image))
        if frame is None:
            print(f"Could not read image: {args.image}")
            return 2
        frame = ensure_landscape(frame)  # keep baseline_raw.jpg consistent with corners/warped below
        result = analyse_image(frame)
        return _finish(result, frame, args.out, show=not args.no_show)

    return _run_live(args.camera, args.out)


if __name__ == "__main__":
    sys.exit(main())
