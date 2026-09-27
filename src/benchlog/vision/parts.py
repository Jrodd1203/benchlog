"""From measured objects to recognized parts with their pins in holes.

`benchlog.core.recognize.classify` says what each object is; this works out which holes its pins
are in, which needs the geometry (outline, centre-line tips, hole positions):

    wire, resistor, diode    the two tips of the centre line, snapped to the nearest hole
                             (works for curved loose wires too)
    ESP32                    the 30-pin footprint that best fits inside the outline; which end is
                             which from the shiny metal module (the antenna / EN end)
    LED, capacitors          two neighbouring holes under the body (polarity: the user checks)
    transistor, pot          three holes in a line under the body (pin order: the user checks)
"""

from __future__ import annotations

import cv2
import numpy as np

from benchlog.core.models import ComponentType
from benchlog.core.parts import ESP32_DEVKIT_V1_30_LEFT, ESP32_DEVKIT_V1_30_RIGHT
from benchlog.core.recognize import WIRE, PartFeatures, PartGuess, classify
from benchlog.vision.calibration import Calibration, hole_pitch_px
from benchlog.vision.objects import BoardObject

_TERMINAL_COLUMNS = "ABCDEFGHIJ"
_ESP32_COLUMNS = (("A", "H"), ("B", "I"), ("C", "J"))
TIP_REACH_PITCH = 0.9  # a tip farther than this from any hole isn't plugged in
AMBIGUOUS_TIP_PITCH = 0.3  # a tip this far from its nearest hole...
AMBIGUOUS_RATIO = 1.6  # ...with the next hole less than this much farther away: a close call, to check
HOUSING_SEARCH_PITCH = 1.5  # look this far from a tip for a loose jumper's plug housing
HOUSING_RATIO = 1.4  # thicker than this times the wire's typical thickness: a housing

_TWO_LEGS = {
    ComponentType.LED.value: ("anode", "cathode"),
    ComponentType.CAPACITOR_CERAMIC.value: ("1", "2"),
    ComponentType.CAPACITOR_ELECTROLYTIC.value: ("+", "-"),
}
_AXIAL = {WIRE: ("a", "b"), ComponentType.RESISTOR.value: ("1", "2"), ComponentType.DIODE.value: ("anode", "cathode")}
_THREE_LEGS = {
    ComponentType.TRANSISTOR_NPN.value: ("E", "B", "C"),
    ComponentType.POTENTIOMETER.value: ("1", "wiper", "3"),
}


def features(obj: BoardObject) -> PartFeatures:
    return PartFeatures(
        length_mm=obj.length_mm, width_mm=obj.width_mm, area_mm2=obj.area_mm2,
        body_length_mm=obj.body_length_mm, body_width_mm=obj.body_width_mm, body_color=obj.body_color,
        body_saturation=obj.body_saturation, body_value=obj.body_value, saturation=obj.saturation,
    )  # fmt: skip


class _Holes:
    def __init__(self, cal: Calibration) -> None:
        self.names = [n for n in cal.holes if n[0] in _TERMINAL_COLUMNS and n[1] not in "+-"] + [
            n for n in cal.holes if n[:2] in ("L+", "L-", "R+", "R-")
        ]
        self.xy = np.array([cal.holes[n] for n in self.names])
        self.pitch = hole_pitch_px(cal.holes)

    def ambiguous(self, point) -> bool:
        """Is `point` nearly as close to a second hole as to the nearest one (a wire curling into
        a hole, say)? Then the camera can't tell which it went into."""
        d = np.sort(np.linalg.norm(self.xy - np.asarray(point, float), axis=1))[:2]
        return d[0] > AMBIGUOUS_TIP_PITCH * self.pitch and d[1] < AMBIGUOUS_RATIO * d[0]

    def nearest(self, point, exclude: set[str] = frozenset(), within: float | None = None) -> str | None:
        d = np.linalg.norm(self.xy - np.asarray(point, float), axis=1)
        for i in np.argsort(d):
            if within is not None and d[i] > within:
                return None
            if self.names[i] not in exclude:
                return self.names[i]
        return None


def _centre(obj: BoardObject) -> np.ndarray:
    m = cv2.moments(obj.contour)
    return np.array([m["m10"] / m["m00"], m["m01"] / m["m00"]]) if m["m00"] else obj.contour.reshape(-1, 2).mean(axis=0)


def _axis(obj: BoardObject) -> np.ndarray:
    theta = np.deg2rad(obj.angle_deg)
    return np.array([np.cos(theta), np.sin(theta)])


def _has_housing(obj: BoardObject, tip, pitch: float) -> bool:
    """Is there a loose jumper's plug housing at this end (a blob clearly thicker than the wire)?

    From above, the housing, its shadow and the wire's bend blur together, so the exact hole under
    it can be one off: such ends are marked for the user to check rather than guessed harder.
    """
    x, y, w, h = cv2.boundingRect(obj.contour)
    pad = 2
    mask = np.zeros((h + 2 * pad, w + 2 * pad), np.uint8)
    cv2.drawContours(mask, [obj.contour - [x - pad, y - pad]], -1, 255, -1)
    thickness = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    skeleton = cv2.ximgproc.thinning(mask) > 0
    if not skeleton.any():
        return False
    typical = float(np.median(thickness[skeleton]))
    local = np.asarray(tip, float) - [x - pad, y - pad]
    ys, xs = np.nonzero(thickness)
    near = np.hypot(xs - local[0], ys - local[1]) < HOUSING_SEARCH_PITCH * pitch
    return bool(near.any()) and float(thickness[ys[near], xs[near]].max()) >= HOUSING_RATIO * typical


def _axial_pins(obj: BoardObject, holes: _Holes, names: tuple[str, str]) -> tuple[dict[str, str], list[str]] | None:
    """The two ends' holes, and which pins are a close call between two holes (to check)."""
    if len(obj.endpoints) != 2:
        return None
    reach = TIP_REACH_PITCH * holes.pitch
    first = holes.nearest(obj.endpoints[0], within=reach)
    second = holes.nearest(obj.endpoints[1], exclude={first} if first else set(), within=reach)
    if not first or not second:
        return None
    close_calls = [name for name, tip in zip(names, obj.endpoints) if holes.ambiguous(tip)]
    return {names[0]: first, names[1]: second}, close_calls


def _step(hole: str, along_rows: bool, steps: int, cal: Calibration) -> str | None:
    """The terminal hole `steps` away from `hole`, along the numbered rows or across the letters."""
    if hole[0] not in _TERMINAL_COLUMNS or hole[1] in "+-":
        return None
    col, row = hole[0], int(hole[1:])
    target = f"{col}{row + steps}" if along_rows else (
        f"{_TERMINAL_COLUMNS[_TERMINAL_COLUMNS.index(col) + steps]}{row}"
        if 0 <= _TERMINAL_COLUMNS.index(col) + steps < len(_TERMINAL_COLUMNS) else ""
    )  # fmt: skip
    return target if target in cal.holes else None


def _runs_along_rows(axis: np.ndarray, cal: Calibration) -> bool:
    """Does `axis` point along the numbered rows (A1 -> A2) rather than across the letters (A1 -> B1)?"""
    along = np.subtract(cal.holes["A2"], cal.holes["A1"])
    across = np.subtract(cal.holes["B1"], cal.holes["A1"])
    return abs(axis @ along) / np.linalg.norm(along) >= abs(axis @ across) / np.linalg.norm(across)


def _legs_in_line(obj: BoardObject, cal: Calibration, holes: _Holes, count: int, spacing: int) -> list[str]:
    """`count` holes in one line under the body, `spacing` holes apart, centred on it.

    The body is snapped to its nearest hole, then legs step along whichever grid direction the part
    lies along, so they stay in one row (a body straddling two rows can't split its legs across them).
    """
    centre_xy = _centre(obj)
    centre = holes.nearest(centre_xy)
    if centre is None:
        return []
    axis = _axis(obj)
    along_rows = _runs_along_rows(axis, cal)
    if count == 2:
        # Two legs: the centre hole and its neighbour on the side the body extends towards.
        towards = float(np.subtract(centre_xy, cal.holes[centre]) @ axis)
        step_dir = np.subtract(cal.holes[_step(centre, along_rows, 1, cal) or centre], cal.holes[centre])
        forward = (towards >= 0) == (float(step_dir @ axis) >= 0)
        offsets = [0, spacing if forward else -spacing]
    else:
        offsets = [round((i - (count - 1) / 2) * spacing) for i in range(count)]
    legs = [_step(centre, along_rows, off, cal) for off in offsets]
    return sorted(legs, key=lambda h: (h[0], int(h[1:]))) if all(legs) else []


def _straight_ends(obj: BoardObject, pins: dict[str, str], cal: Calibration) -> dict[str, str]:
    """Keep an axial part's two legs in the line its body lies on (resistors and diodes are straight)."""
    names = list(pins)
    a, b = pins[names[0]], pins[names[1]]
    if any(h[0] not in _TERMINAL_COLUMNS or h[1] in "+-" for h in (a, b)):
        return pins
    centre = _Holes(cal).nearest(_centre(obj))
    if centre is None or centre[0] not in _TERMINAL_COLUMNS:
        return pins
    if _runs_along_rows(_axis(obj), cal):
        fixed = {names[0]: f"{centre[0]}{a[1:]}", names[1]: f"{centre[0]}{b[1:]}"}  # same letter
    else:
        fixed = {names[0]: f"{a[0]}{centre[1:]}", names[1]: f"{b[0]}{centre[1:]}"}  # same number
    return fixed if all(h in cal.holes for h in fixed.values()) and len(set(fixed.values())) == 2 else pins


def _esp32_pins(obj: BoardObject, aligned: np.ndarray, cal: Calibration, holes: _Holes) -> dict[str, str] | None:
    """The DevKit's 30 pins: the footprint position whose pins best fall inside the outline."""
    reach = -0.3 * holes.pitch
    inside = {n for n, xy in zip(holes.names, holes.xy) if cv2.pointPolygonTest(obj.contour, tuple(map(float, xy)), True) >= reach}
    rows = sorted({int(n[1:]) for n in inside if n[0] in _TERMINAL_COLUMNS})
    best = None
    for left, right in _ESP32_COLUMNS:
        for top in rows:
            names = [f"{left}{top + i}" for i in range(15)] + [f"{right}{top + i}" for i in range(15)]
            if any(n not in cal.holes for n in names):
                continue
            hits = sum(n in inside for n in names)
            if best is None or hits > best[0]:
                best = (hits, left, right, top)
    if best is None or best[0] < 26:
        return None
    _, left, right, top = best
    # Which end is the antenna/EN end? The ESP32 module's metal can is the shiny (bright, colourless) end.
    mask = np.zeros(aligned.shape[:2], np.uint8)
    cv2.drawContours(mask, [obj.contour], -1, 255, -1)
    hsv = cv2.cvtColor(aligned, cv2.COLOR_BGR2HSV)
    first_end, last_end = np.array(cal.holes[f"{left}{top}"]), np.array(cal.holes[f"{left}{top + 14}"])
    ys, xs = np.nonzero(mask)
    t = ((np.column_stack([xs, ys]) - first_end) @ (last_end - first_end)) / max(1.0, np.linalg.norm(last_end - first_end)) ** 2
    shiny = hsv[ys, xs, 2].astype(float) - hsv[ys, xs, 1].astype(float)
    en_at_top = shiny[t < 0.5].mean() >= shiny[t >= 0.5].mean()
    order = range(15) if en_at_top else range(14, -1, -1)
    pins = {}
    for i, row in zip(order, range(top, top + 15)):
        pins[ESP32_DEVKIT_V1_30_LEFT[i]] = f"{left}{row}"
        pins[ESP32_DEVKIT_V1_30_RIGHT[i]] = f"{right}{row}"
    return pins


def recognize(cal: Calibration, objects: list[BoardObject], aligned: np.ndarray) -> list[PartGuess]:
    """What each object is and where its pins are. Unrecognized objects are left out."""
    holes = _Holes(cal)
    guesses = []
    halves: list[tuple[BoardObject, str]] = []
    for obj in objects:
        kind, confidence = classify(features(obj))
        if kind is None:
            continue
        uncertain: list[str] = []
        if kind in _AXIAL:
            found = _axial_pins(obj, holes, _AXIAL[kind])
            pins, close_calls = found if found else (None, [])
            if pins is None:
                if kind == WIRE:
                    half = _half_wire(obj, holes)
                    if half:
                        halves.append(half)
                continue
            if kind != WIRE:  # resistors and diodes are straight; wires can loop anywhere
                pins = _straight_ends(obj, pins, cal)
            else:
                uncertain = [hole for hole, tip in zip(pins.values(), obj.endpoints) if _has_housing(obj, tip, holes.pitch)]
            uncertain = sorted(set(uncertain) | {pins[name] for name in close_calls})  # after straightening
            if kind == ComponentType.DIODE.value:
                uncertain = list(pins.values())  # which end is the cathode (band): check
        elif kind == ComponentType.ESP32_DEVKIT_V1_30.value:
            pins = _esp32_pins(obj, aligned, cal, holes)
            if pins is None:
                continue
            # It sits ~1 cm up on its headers, so it looks bigger and shifted: the camera can't see which
            # holes the pins are in. The user anchors it with one or two pins in review.
            uncertain = [pins["EN"], pins["VIN"]]
            confidence = 0.5
        elif kind in _TWO_LEGS:
            legs = _legs_in_line(obj, cal, holes, 2, 1)
            if len(legs) < 2:
                continue
            pins = dict(zip(_TWO_LEGS[kind], legs))
            uncertain = legs  # legs are hidden under the body; polarity isn't visible
        elif kind in _THREE_LEGS:
            legs = _legs_in_line(obj, cal, holes, 3, 1 if kind != ComponentType.POTENTIOMETER.value else 2)
            if len(legs) < 3:
                continue
            pins = dict(zip(_THREE_LEGS[kind], legs))
            uncertain = legs
        else:
            continue
        claims = sorted(set(obj.holes) | set(pins.values()))
        color = obj.color if kind == WIRE else None
        guesses.append(PartGuess(kind=kind, confidence=confidence, pins=pins, claims=claims, color=color, uncertain=uncertain))
    return guesses + _join_halves(halves)


def _half_wire(obj: BoardObject, holes: _Holes) -> tuple[BoardObject, str] | None:
    """A wire piece with one end in a hole and the other running off the board (a loop outside
    the board's edge is cut out of the outline). Returns (piece, the hole it's plugged into)."""
    if len(obj.endpoints) != 2:
        return None
    reach = TIP_REACH_PITCH * holes.pitch
    plugged = [holes.nearest(tip, within=reach) for tip in obj.endpoints]
    if sum(p is not None for p in plugged) != 1:
        return None
    return obj, next(p for p in plugged if p)


def _join_halves(halves: list[tuple[BoardObject, str]]) -> list[PartGuess]:
    """Pair wire pieces of the same color into one wire between their plugged ends."""
    guesses, left = [], list(halves)
    while left:
        obj, hole = left.pop(0)
        mate = next((h for h in left if h[0].color == obj.color), None)
        if mate is None:
            continue  # a lone piece: its other end is hidden; leave it to the per-hole pairing
        left.remove(mate)
        claims = sorted(set(obj.holes) | set(mate[0].holes) | {hole, mate[1]})
        guesses.append(PartGuess(
            kind=WIRE, confidence=0.6, pins={"a": hole, "b": mate[1]}, claims=claims, color=obj.color,
            uncertain=[hole, mate[1]],  # joined across the board's edge, with plug housings: check both ends
        ))  # fmt: skip
    return guesses
