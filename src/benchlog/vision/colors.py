"""Name the color at a hole, e.g. a wire's insulation. Coarse on purpose: a few names that survive
lighting changes beat precise ones that flip between scans.

Pairing uses these to tell which wire ends belong together, and later to tell parts apart
(a beige resistor body, a red LED dome). It treats them as hints, never as proof.
"""

from __future__ import annotations

import cv2
import numpy as np

# OpenCV hue runs 0-180. Upper bounds of each named hue band.
_HUES = ((8, "red"), (22, "orange"), (35, "yellow"), (85, "green"), (130, "blue"), (165, "purple"), (180, "red"))
MIN_SATURATION = 60  # below this it's black, grey or white rather than a hue
DARK = 60  # value (brightness) below this is black
LIGHT = 190  # value above this (with low saturation) is white


def color_name(patch: np.ndarray) -> str:
    """The color of a BGR image patch, as one of: red, orange, yellow, green, blue, purple, black, grey, white."""
    hsv = cv2.cvtColor(patch.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(float)
    h, s, v = np.median(hsv[:, 0]), np.median(hsv[:, 1]), np.median(hsv[:, 2])
    if v < DARK:
        return "black"
    if s < MIN_SATURATION:
        return "white" if v > LIGHT else "grey"
    # Hue is circular (red wraps around 180), so take the hue of saturated pixels by vector mean.
    saturated = hsv[hsv[:, 1] >= MIN_SATURATION]
    angles = np.deg2rad((saturated[:, 0] if len(saturated) else np.array([h])) * 2)
    hue = (np.rad2deg(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())) / 2) % 180
    return next(name for bound, name in _HUES if hue < bound)


def hole_colors(frame: np.ndarray, holes: dict[str, tuple[float, float]], names: list[str], radius: int) -> dict[str, str]:
    """Color name at each of `names`, sampled around its position in `frame`."""
    h, w = frame.shape[:2]
    colors = {}
    for name in names:
        x, y = round(holes[name][0]), round(holes[name][1])
        patch = frame[max(0, y - radius) : min(h, y + radius + 1), max(0, x - radius) : min(w, x + radius + 1)]
        if patch.size:
            colors[name] = color_name(patch)
    return colors
