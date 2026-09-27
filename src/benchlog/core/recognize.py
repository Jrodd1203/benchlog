"""Which part an object on the board is, from its measurements. No OpenCV: plain rules.

The vision step (`benchlog.vision.objects`) outlines each new object and measures it in
millimetres. Parts differ mostly in physical size and shape, which datasheets fix, so a few rules
tell them apart. The thresholds come from real scans of each part (see the table in the PR):

    ESP32         huge dark rectangle          ~50 x 28 mm board (+ shadow)
    pot           big body                     ~20 x 17 mm
    wire          long, uniformly thin         body ~ as long as the whole thing, 2-4 mm wide
    resistor      colored body, legs showing   whole thing ~2-4x the body's length, body ~7 x 2.6
    diode         dark body, legs showing      body ~7 x 3.5, saturation ~15
    LED           vivid color, roundish        saturation ~140, body ~6-8 mm
    ceramic cap   thin colored bar, no legs    body ~6 x 2.4
    electrolytic  grey can, no legs            body ~6.6 x 4.3
    transistor    small black block            body ~5.6 x 3.9

Anything that fits none of them is left unrecognized (None): the user names it in review.
"""

from pydantic import BaseModel, Field

from benchlog.core.models import ComponentType, Hole

WIRE = "wire"

MIN_AREA_MM2 = 3.0  # smaller: a speck, not a part
LEGS_RATIO = 1.8  # whole length / body length at or above this: legs stick out (axial part)


class PartFeatures(BaseModel):
    length_mm: float
    width_mm: float
    area_mm2: float
    body_length_mm: float
    body_width_mm: float
    body_color: str
    body_saturation: float
    body_value: float
    saturation: float = 0.0  # of the whole object: jumper insulation is vivid all along


class PartGuess(BaseModel):
    """A recognized object: what it is, where its pins go, and every hole it accounts for."""

    kind: str = Field(description="'wire', or a component type ('resistor', 'led', ...).")
    confidence: float = Field(ge=0, le=1)
    pins: dict[str, Hole] = Field(description="Wire ends {'a','b'}, or the component's pins.")
    claims: list[Hole] = Field(default=[], description="Holes it covers (pins included), so they aren't paired as wires.")
    color: str | None = None
    uncertain: list[Hole] = Field(default=[], description="Pins the user should check (hidden legs, unknown polarity).")


def classify(f: PartFeatures) -> tuple[str | None, float]:
    """(kind, confidence): 'wire', a ComponentType value, or None when it fits nothing well."""
    if f.area_mm2 < MIN_AREA_MM2:
        return None, 0.0
    dark = f.body_value < 90 or f.body_color == "black"
    colored = f.body_saturation >= 40
    legs = f.length_mm / max(f.body_length_mm, 0.1) >= LEGS_RATIO

    if f.body_length_mm >= 35 and f.body_width_mm >= 18:
        return ComponentType.ESP32_DEVKIT_V1_30.value, 0.8
    if f.body_length_mm >= 14 and f.body_width_mm >= 10:
        return ComponentType.POTENTIOMETER.value, 0.6
    # A wire is long and the same thickness all along (no body bulge), flat or looping.
    if f.length_mm >= 12 and f.body_width_mm <= 4.5 and not legs:
        return WIRE, 0.8
    # ...or long and vividly colored all along, even if its plug housing makes one end look thick.
    if f.length_mm >= 12 and f.saturation >= 90 and f.body_width_mm <= 5.5:
        return WIRE, 0.75
    if legs and 4 <= f.body_length_mm <= 12 and f.body_width_mm <= 4.5:
        if colored and not dark:
            return ComponentType.RESISTOR.value, 0.7
        return ComponentType.DIODE.value, 0.6
    if legs:
        return None, 0.0
    # No legs showing: the part stands up or its legs are underneath.
    if f.body_saturation >= 110 and f.body_width_mm >= 3.5:
        return ComponentType.LED.value, 0.7
    if f.body_width_mm <= 3.2 and colored and 3.5 <= f.body_length_mm <= 9:
        return ComponentType.CAPACITOR_CERAMIC.value, 0.6
    if dark and f.body_length_mm <= 6.2 and f.body_width_mm >= 2.8:
        return ComponentType.TRANSISTOR_NPN.value, 0.5
    if 5 <= f.body_length_mm <= 10 and f.body_width_mm >= 3.5 and f.body_value < 190:  # bright white: glare
        return ComponentType.CAPACITOR_ELECTROLYTIC.value, 0.5
    return None, 0.0
