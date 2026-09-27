"""Outline and measure every new object on the board: the step before telling parts apart.

The calibration gives a top-down view lined up with the empty board to under a pixel, and the hole
pitch (2.54 mm) gives the scale. So each new object's outline can be measured in millimetres,
which is what tells parts apart: a jumper is ~1-2 mm wide, a resistor body ~6 x 2.3 mm, an LED a
3-5 mm circle, a TO-92 transistor ~4.5 x 3.5 mm. Those sizes come from datasheets, so they
barely depend on lighting or camera.

    find_objects(cal, frame, tracking) -> [BoardObject]   outlines + measurements
    draw_objects(image, objects)                           debug overlay
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from benchlog.vision.calibration import Calibration, Tracking, _board_mask, change_map, hole_pitch_px
from benchlog.vision.colors import color_name

PITCH_MM = 2.54
MIN_PIXEL_DIFF = 20.0  # a pixel must change at least this much (Lab distance), however quiet the scan
PIXEL_NOISE_SIGMAS = 6.0  # ... and this many robust standard deviations above the board's typical pixel
MIN_WEAK_PIXEL_DIFF = 18.0  # weaker changes (thin bare legs) count where they connect to a strong one
WEAK_NOISE_SIGMAS = 5.0  # lower picks up shadows; legs are found along the axis instead
MIN_OBJECT_AREA_PITCH2 = 0.1  # smaller blobs (in hole-pitch squared) are noise: a wire end is ~0.15
EDGE_BAND_PITCH = 1.2  # width (in hole pitches) of the board's outer edge band that's ignored
NEAR_HOLE_PITCH = 0.5  # an object must reach within this of a hole centre: parts plug into holes
BODY_FRACTION = 0.6  # the body is where the object is at least this fraction of its thickest


@dataclass
class BoardObject:
    """One new thing on the board, measured in millimetres (in the calibration image's coordinates)."""

    id: int
    contour: np.ndarray = field(repr=False)
    length_mm: float  # along its long axis (the minimum-area rectangle around it)
    width_mm: float
    area_mm2: float
    angle_deg: float  # of the long axis; 0 = along the rows, 90 = across them
    roundness: float  # 1 for a circle, towards 0 for a thin line
    fill: float  # how much of its rectangle it fills: ~1 for a bar, ~0.79 for a circle
    color: str
    saturation: float  # median, 0-255: jumper insulation is high, resistor bodies and metal lower
    holes: list[str]  # holes whose centre it covers
    ends: tuple[str, str] | None  # the hole nearest each end of its long axis (legs of an axial part)
    # Its thickest part: a resistor or diode body on thin legs, an LED dome. A wire is the same all along.
    body_width_mm: float = 0.0
    body_length_mm: float = 0.0
    body_color: str = "grey"
    body_saturation: float = 0.0  # median, 0-255
    body_value: float = 0.0  # median brightness, 0-255: black parts are low
    endpoints: list[tuple[float, float]] = field(default_factory=list, repr=False)  # tips of its centre line (px)

    @property
    def aspect(self) -> float:
        return self.length_mm / max(self.width_mm, 1e-6)

    def summary(self) -> str:
        ends = f", ends {self.ends[0]}-{self.ends[1]}" if self.ends else ""
        body = f"; body {self.body_length_mm:.1f} x {self.body_width_mm:.1f} mm {self.body_color}"
        holes = f"{len(self.holes)} holes" if len(self.holes) > 4 else ", ".join(self.holes) or "no holes"
        return (
            f"#{self.id}: {self.length_mm:.1f} x {self.width_mm:.1f} mm, {self.color} (sat {self.saturation:.0f}), "
            f"round {self.roundness:.2f}, fill {self.fill:.2f}, {self.angle_deg:.0f}°; {holes}{ends}{body}"
        )


def _changed_pixels(cal: Calibration, distance: np.ndarray, pitch: float) -> tuple[np.ndarray, float]:
    """Binary mask of pixels that changed, and the (strong) threshold used.

    Two levels, like an edge detector: pixels that changed strongly start an object, and weaker
    changes are kept only where they connect to one. A part's thin bare legs are faint from above
    but attached to its body, so they come along; isolated specks and glints don't.

    The board's outer edge band is left out: when the board moves, its edges and sides (it has
    height) never line up exactly with the calibration image, and nothing plugs in there anyway.
    """
    edge = max(3, round(pitch * EDGE_BAND_PITCH)) | 1
    board = cv2.erode(_board_mask(distance.shape, cal.corners), cv2.getStructuringElement(cv2.MORPH_RECT, (edge, edge))) > 0
    values = distance[board]
    median = float(np.median(values))
    spread = 1.4826 * float(np.median(np.abs(values - median)))
    strong = max(MIN_PIXEL_DIFF, median + PIXEL_NOISE_SIGMAS * spread)
    weak = max(MIN_WEAK_PIXEL_DIFF, median + WEAK_NOISE_SIGMAS * spread)
    weak_mask = ((distance > weak) & board).astype(np.uint8) * 255
    opened = max(3, round(pitch * 0.12)) | 1
    strong_mask = ((distance > strong) & board).astype(np.uint8) * 255
    strong_mask = cv2.morphologyEx(strong_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (opened, opened)))
    # Keep the weak regions that contain a strong seed.
    count, labels = cv2.connectedComponents(weak_mask, connectivity=8)
    seeded = np.unique(labels[strong_mask > 0])
    mask = np.isin(labels, seeded[seeded > 0]).astype(np.uint8) * 255
    # Close small gaps inside one object (bands on a resistor, a glint on insulation).
    close = max(3, round(pitch * 0.3)) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close, close)))
    return mask, strong


def _near_a_hole(contour: np.ndarray, centres: np.ndarray, reach: float) -> bool:
    """Does the outline cover a hole centre, or come within `reach` of one?"""
    x, y, w, h = cv2.boundingRect(contour)
    nearby = centres[(centres[:, 0] > x - reach) & (centres[:, 0] < x + w + reach) & (centres[:, 1] > y - reach) & (centres[:, 1] < y + h + reach)]
    return any(cv2.pointPolygonTest(contour, (float(cx), float(cy)), True) > -reach for cx, cy in nearby)


def _body(
    blob: np.ndarray, aligned: np.ndarray, axis_angle: float, mm_per_px: float
) -> tuple[float, float, str, float, float]:
    """(width, length, color, saturation, brightness) of the object's thickest part (sizes in mm)."""
    thickness = cv2.distanceTransform(blob, cv2.DIST_L2, 5)
    widest = float(thickness.max())
    body = thickness >= BODY_FRACTION * widest
    # The body is the thick core plus the rim around it (the distance transform thins it).
    body = cv2.dilate(body.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=max(1, round(widest * BODY_FRACTION))) & (blob > 0)
    ys, xs = np.nonzero(body)
    theta = np.deg2rad(axis_angle)
    along = xs * np.cos(theta) + ys * np.sin(theta)
    length = float(along.max() - along.min() + 1) if len(along) else 0.0
    if not len(xs):
        return 2 * widest * mm_per_px, length * mm_per_px, "grey", 0.0, 0.0
    hsv = cv2.cvtColor(aligned[body > 0].reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    return (
        2 * widest * mm_per_px, length * mm_per_px, color_name(aligned[body > 0]),
        float(np.median(hsv[:, 1])), float(np.median(hsv[:, 2])),
    )  # fmt: skip


def _endpoints(blob: np.ndarray) -> list[tuple[float, float]]:
    """The two tips of the object's centre line: where a wire or a part's legs end, even when curved."""
    skeleton = cv2.ximgproc.thinning(blob)
    ys, xs = np.nonzero(skeleton)
    if len(xs) < 2:
        return []
    # A skeleton pixel with a single neighbour is a tip; branches (a shadow, a glint) give extra tips.
    neighbours = cv2.filter2D((skeleton > 0).astype(np.uint8), -1, np.ones((3, 3), np.uint8)) - 1
    tips = np.array([(x, y) for x, y in zip(xs, ys) if neighbours[y, x] == 1], dtype=float)
    if len(tips) < 2:
        tips = np.column_stack([xs, ys]).astype(float)
    # The two tips farthest apart are the real ends; shorter branches are side twigs.
    d = np.linalg.norm(tips[:, None] - tips[None], axis=2)
    i, j = np.unravel_index(d.argmax(), d.shape)
    return [tuple(tips[i]), tuple(tips[j])]


def _ends(cal: Calibration, contour: np.ndarray, rect: tuple, pitch: float) -> tuple[str, str] | None:
    """The hole nearest each end of the object's long axis, if both are within half a hole of it."""
    (cx, cy), (w, h), angle = rect
    length = max(w, h)
    theta = np.deg2rad(angle if w >= h else angle + 90)
    axis = np.array([np.cos(theta), np.sin(theta)])
    tips = [np.array([cx, cy]) + axis * length / 2 * s for s in (-1, 1)]
    names = list(cal.holes)
    centres = np.array([cal.holes[n] for n in names])
    ends = []
    for tip in tips:
        # Legs sit a little inside the outline's tip (the lead bends down into the hole).
        d = np.linalg.norm(centres - tip, axis=1)
        i = int(d.argmin())
        if d[i] > pitch * 0.75:
            return None
        ends.append(names[i])
    if ends[0] == ends[1]:
        return None
    return tuple(sorted(ends, key=lambda n: (n[0], int(n[1:]) if n[1:].isdigit() else 0)))  # type: ignore[return-value]


def find_objects(cal: Calibration, frame: np.ndarray, tracking: Tracking) -> list[BoardObject]:
    """Outline every object that isn't on the calibration image, largest first, and measure it."""
    distance, aligned = change_map(cal, frame, tracking)
    pitch = hole_pitch_px(cal.holes)
    mm_per_px = PITCH_MM / pitch
    mask, _ = _changed_pixels(cal, distance, pitch)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    min_area = MIN_OBJECT_AREA_PITCH2 * pitch * pitch
    centres = np.array(list(cal.holes.values()))
    hsv = cv2.cvtColor(aligned, cv2.COLOR_BGR2HSV)
    objects = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        if not _near_a_hole(contour, centres, NEAR_HOLE_PITCH * pitch):
            continue
        rect = cv2.minAreaRect(contour)
        (w, h), angle = rect[1], rect[2]
        length, width = max(w, h), min(w, h)
        perimeter = cv2.arcLength(contour, True)
        blob = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(blob, [contour], -1, 255, -1)
        inside = blob > 0
        pixels = aligned[inside]
        holes = [n for n, (x, y) in cal.holes.items() if blob[min(blob.shape[0] - 1, round(y)), min(blob.shape[1] - 1, round(x))]]
        long_angle = (angle if w >= h else angle + 90) % 180
        body_width, body_length, body_color, body_saturation, body_value = _body(blob, aligned, long_angle, mm_per_px)
        objects.append(
            BoardObject(
                id=len(objects) + 1,
                contour=contour,
                length_mm=length * mm_per_px,
                width_mm=width * mm_per_px,
                area_mm2=area * mm_per_px**2,
                angle_deg=float(long_angle),
                roundness=float(4 * np.pi * area / perimeter**2) if perimeter else 0.0,
                fill=float(area / (w * h)) if w * h else 0.0,
                color=color_name(pixels) if len(pixels) else "grey",
                saturation=float(np.median(hsv[..., 1][inside])) if len(pixels) else 0.0,
                holes=sorted(holes, key=lambda n: (n[0], int(n[1:]) if n[1:].isdigit() else 0)),
                ends=_ends(cal, contour, rect, pitch),
                body_width_mm=body_width,
                body_length_mm=body_length,
                body_color=body_color,
                body_saturation=body_saturation,
                body_value=body_value,
                endpoints=_endpoints(blob),
            )
        )
    return objects


def draw_objects(image: np.ndarray, objects: list[BoardObject]) -> np.ndarray:
    """`image` (in calibration coordinates) with each object outlined and labeled."""
    out = image.copy()
    for obj in objects:
        cv2.drawContours(out, [obj.contour], -1, (255, 0, 255), 2, cv2.LINE_AA)
        x, y, _, _ = cv2.boundingRect(obj.contour)
        label = f"#{obj.id} {obj.length_mm:.1f}x{obj.width_mm:.1f}mm {obj.color}"
        for thickness, colour in ((4, (0, 0, 0)), (1, (255, 255, 255))):
            cv2.putText(out, label, (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, thickness, cv2.LINE_AA)
    return out
