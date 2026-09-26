"""BB830 hole geometry in board-millimetre space. Owner: Person 4.

Pure geometry: numpy only, no OpenCV. Everything downstream (`capture.py`) scales
these millimetre coordinates by a px-per-mm factor to get pixel positions in a
canonical warped image.

Coordinate frame: origin = top-left corner of the board *body* outline, x to the
right, y down, board size BOARD_W_MM x BOARD_H_MM (165.1 x 54.6 mm, landscape).
Viewed this way (top-down, readable): numbered rows 1..63 run horizontally with
row 1 at the RIGHT edge and row 63 at the LEFT edge (labels are printed every 5:
1, 5, 10, ..., 60; 61-63 are unlabeled at the far left). Columns A-E are the top
half (nearest the top rail pair), F-J the bottom half, split by a centre channel.
There are two rail pairs (top and bottom edge); in both, the + row is above the
- row. L = the rail pair next to column A (top), R = the rail pair next to
column J (bottom) -- matching `benchlog.core.board`'s hole naming. Within every
rail, index 1 is the end nearest row 1 (the right side), increasing leftward.

Measurement method (see tests/fixtures/bb830_white_topdown.jpg, 1200x528,
studio-lit, perfectly top-down white BB830): a one-off scratch script (not
checked in) thresholded the image to find the board body's bounding box
(bright plastic vs white background, x=[68, 1112] y=[80, 431] px) and the dark
hole blobs (threshold on grayscale < 130), clustered their centroids into rows
and columns, and fit pitch/offsets from the cluster spacing. The photo isn't
perfectly isotropic (~15.94 px per 2.54 mm pitch horizontally vs ~15.67 px
vertically -- lens/scan artefact, not a real board property), so horizontal and
vertical pixel measurements were normalised through separate px/mm scales
before being written here as millimetres. PITCH_MM itself is the exact
datasheet value (2.54 mm); only the offsets below are measurements. Registration
in `capture.py` fits a homography correction against real detected holes at
runtime, so residual measurement error of a fraction of a mm here is expected
and corrected for -- it does not need to be perfect.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchlog.core.board import BB830, LEFT_COLUMNS, RIGHT_COLUMNS

Point = tuple[float, float]

# ── Datasheet constants ────────────────────────────────────────────────────
BOARD_W_MM = 165.1
BOARD_H_MM = 54.6
PITCH_MM = 2.54

# ── Measured offsets (see module docstring for method) ─────────────────────
# Terminal grid: x of the row-63 column (leftmost), y of row A (topmost).
_TERM_X0_MM = 5.02
_TERM_Y0_MM = 14.06
# Gap between row E (last of the top block) and row F (first of the bottom
# block), i.e. the centre DIP channel. Measured ~7.60 mm, close to 3x pitch.
_CHANNEL_GAP_MM = 7.60

# Rail rows: y of the top pair's "+" row and the bottom pair's "+" row. The
# "-" row of each pair is one pitch below (measured ~2.55 mm, i.e. one pitch).
_RAIL_TOP_PLUS_Y_MM = 4.54
_RAIL_BOTTOM_PLUS_Y_MM = 49.22

# Rail holes: x of rail index 1 (rightmost, nearest row 1), measured separately
# for the top and bottom rail pair -- they are not quite at the same x. Rails
# are 10 groups of 5 holes; consecutive holes in a group are one pitch apart,
# and there's a two-pitch gap (one hole-width skipped) between groups -- both
# confirmed by the measured spacing.
_RAIL_TOP_X1_MM = 156.68
_RAIL_BOTTOM_X1_MM = 157.66
_RAIL_GROUP_GAP_MM = 2 * PITCH_MM


def _terminal_x_mm(row: int) -> float:
    """x of numbered row `row` (1..63); row 1 is at the right."""
    return _TERM_X0_MM + (63 - row) * PITCH_MM


def _terminal_y_mm(col: str) -> float:
    """y of column letter `col` (A..J)."""
    if col in LEFT_COLUMNS:
        return _TERM_Y0_MM + LEFT_COLUMNS.index(col) * PITCH_MM
    i = RIGHT_COLUMNS.index(col)
    return _TERM_Y0_MM + 4 * PITCH_MM + _CHANNEL_GAP_MM + i * PITCH_MM


def _rail_x_mm(idx: int, x1_mm: float) -> float:
    """x of rail hole `idx` (1..50); idx 1 is at the right, increasing leftward."""
    group, pos = divmod(idx - 1, 5)
    return x1_mm - (group * (4 * PITCH_MM + _RAIL_GROUP_GAP_MM) + pos * PITCH_MM)


def hole_positions_mm() -> dict[str, Point]:
    """Every BB830 hole's (x, y) centre in board mm. 830 entries, matches BB830.is_valid."""
    pts: dict[str, Point] = {}

    for row in range(1, BB830.rows + 1):
        x = _terminal_x_mm(row)
        for col in LEFT_COLUMNS + RIGHT_COLUMNS:
            pts[f"{col}{row}"] = (x, _terminal_y_mm(col))

    for idx in range(1, BB830.rail_length + 1):
        top_x = _rail_x_mm(idx, _RAIL_TOP_X1_MM)
        bottom_x = _rail_x_mm(idx, _RAIL_BOTTOM_X1_MM)
        pts[f"L+{idx}"] = (top_x, _RAIL_TOP_PLUS_Y_MM)
        pts[f"L-{idx}"] = (top_x, _RAIL_TOP_PLUS_Y_MM + PITCH_MM)
        pts[f"R+{idx}"] = (bottom_x, _RAIL_BOTTOM_PLUS_Y_MM)
        pts[f"R-{idx}"] = (bottom_x, _RAIL_BOTTOM_PLUS_Y_MM + PITCH_MM)

    return pts


@dataclass(frozen=True)
class GuideOutline:
    """Minimal geometry for drawing the check-deposit guide overlay, in board mm."""

    rail_line_ys: tuple[float, float, float, float]  # top+, top-, bottom+, bottom-
    centre_channel: tuple[float, float]  # y0, y1 spanning the DIP channel


def guide_outline_mm() -> GuideOutline:
    """Board rectangle, rail line ys and centre-channel span, for overlay drawing."""
    return GuideOutline(
        rail_line_ys=(
            _RAIL_TOP_PLUS_Y_MM,
            _RAIL_TOP_PLUS_Y_MM + PITCH_MM,
            _RAIL_BOTTOM_PLUS_Y_MM,
            _RAIL_BOTTOM_PLUS_Y_MM + PITCH_MM,
        ),
        centre_channel=(
            _TERM_Y0_MM + 4 * PITCH_MM,
            _TERM_Y0_MM + 4 * PITCH_MM + _CHANNEL_GAP_MM,
        ),
    )
