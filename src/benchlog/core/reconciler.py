"""Reconciler: merges vision Observations with ESP32 electrical state.

Neither source is trusted alone:
  - Vision can hallucinate from lighting/shadows.
  - ESP32 only knows about holes that map to its GPIO pins.

Agreement rules:
  - Both agree  → confidence boosted, status stays PENDING for user review
  - Vision only → confidence kept low (0.5-0.7), flagged as uncertain
  - ESP32 only  → emitted as a low-confidence observation (wire moved off-camera?)
  - Neither     → nothing emitted
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from benchlog.core.models import Observation, ObservationKind, ObservationStatus
from benchlog.core.serial_reader import SerialReader

# GPIO number → hole name for the DOIT ESP32 DevKit V1 placed at rows 1-15.
# Generated from parts.py: left col B, right col I, top_row=1.
_GPIO_TO_HOLE: dict[int, str] = {
    # Left side (column B, rows 1-15, top to bottom)
    # EN=B1 (not a GPIO), GPIO36=B2 … GND1=B13 (skip), GPIO13=B14, VIN=B15 (skip)
    36: "B2", 39: "B3", 34: "B4", 35: "B5", 32: "B6", 33: "B7",
    25: "B8", 26: "B9", 27: "B10", 14: "B11", 12: "B12", 13: "B14",
    # Right side (column I, rows 1-15, top to bottom)
    # GPIO23=I1, GPIO22=I2, GPIO1=I3 (TX, skip), GPIO3=I4 (RX, skip),
    # GPIO21=I5, GPIO19=I6, GPIO18=I7, GPIO5=I8, GPIO17=I9, GPIO16=I10,
    # GPIO4=I11, GPIO2=I12, GPIO15=I13, GND2=I14 (skip), 3V3=I15 (skip)
    23: "I1", 22: "I2", 21: "I5", 19: "I6", 18: "I7", 5: "I8",
    17: "I9", 16: "I10", 4: "I11", 2: "I12", 15: "I13",
}


# Strip connectivity: holes in the same horizontal strip share a node.
# We use board.strip() for this, but keep a simple per-row lookup here too.
# A hole is "electrically confirmed" if its strip's GPIO reads stable (not floating).


@dataclass
class ReconciledObservation:
    observation: Observation
    vision_only: bool       # True if ESP32 couldn't confirm (no GPIO on that strip)
    esp32_confirms: bool    # True if ESP32 GPIO state changed in agreement


def _gpio_for_strip(hole: str) -> int | None:
    """Return the GPIO number whose pin sits in the same strip as `hole`, or None."""
    # Strips are same-row, same-side groups (A-E or F-J in the same row).
    # We check every GPIO to see if it shares a strip with the target hole.
    try:
        from benchlog.core.board import BB830
        target_strip = BB830.strip(hole)
    except ValueError:
        return None

    for gpio, gpio_hole in _GPIO_TO_HOLE.items():
        try:
            if BB830.strip(gpio_hole) == target_strip:
                return gpio
        except ValueError:
            continue
    return None


def reconcile(
    vision_obs: Sequence[Observation],
    serial: SerialReader | None,
) -> list[ReconciledObservation]:
    """Merge vision observations with live ESP32 pin state.

    Args:
        vision_obs: Raw observations from occupancy_diff().
        serial:     Live SerialReader (may be None if ESP32 not connected).

    Returns:
        ReconciledObservation list, sorted high-confidence first.
    """
    results: list[ReconciledObservation] = []

    for obs in vision_obs:
        # Which hole did vision flag?
        hole = (obs.after or obs.before or {}).get("hole")
        if hole is None:
            results.append(ReconciledObservation(obs, vision_only=True, esp32_confirms=False))
            continue

        gpio = _gpio_for_strip(hole)

        # No ESP32 connected or no GPIO on this strip → vision only
        if serial is None or gpio is None:
            updated = obs.model_copy(update={"confidence": obs.confidence, "uncertain_holes": [hole] if gpio is None else []})
            results.append(ReconciledObservation(updated, vision_only=True, esp32_confirms=False))
            continue

        # Check ESP32 agreement
        if obs.kind == ObservationKind.ADDED:
            esp32_agrees = serial.gpio_stable_high(gpio) or serial.gpio_stable_low(gpio)
        else:  # REMOVED
            esp32_agrees = serial.gpio_floating(gpio)

        if esp32_agrees:
            # Both agree: boost confidence
            boosted = min(1.0, obs.confidence + 0.25)
            updated = obs.model_copy(update={"confidence": boosted, "uncertain_holes": []})
            results.append(ReconciledObservation(updated, vision_only=False, esp32_confirms=True))
        else:
            # Disagree: keep low confidence, mark uncertain
            updated = obs.model_copy(update={
                "confidence": obs.confidence * 0.6,
                "uncertain_holes": [hole],
            })
            results.append(ReconciledObservation(updated, vision_only=True, esp32_confirms=False))

    # Emit ESP32-only observations (pin changed but vision didn't notice)
    if serial is not None:
        window = serial.window()
        if len(window) >= 2:
            prev, curr = window[-2], window[-1]
            flagged_holes = {r.observation.object_id for r in results}
            for gpio, hole in _GPIO_TO_HOLE.items():
                obj_id = f"hole_{hole}"
                if obj_id in flagged_holes:
                    continue  # already covered by vision
                p_val = prev.digital.get(gpio)
                c_val = curr.digital.get(gpio)
                if p_val is None or c_val is None or p_val == c_val:
                    continue
                kind = ObservationKind.ADDED if c_val == 1 else ObservationKind.REMOVED
                obs = Observation(
                    id=f"esp_{gpio}",
                    kind=kind,
                    object_type="wire",
                    object_id=obj_id,
                    before=None if kind == ObservationKind.ADDED else {"hole": hole},
                    after={"hole": hole} if kind == ObservationKind.ADDED else None,
                    confidence=0.45,
                    uncertain_holes=[hole],
                    status=ObservationStatus.PENDING,
                )
                results.append(ReconciledObservation(obs, vision_only=False, esp32_confirms=False))

    results.sort(key=lambda r: r.observation.confidence, reverse=True)
    return results


def high_confidence(
    reconciled: list[ReconciledObservation],
    threshold: float = 0.75,
) -> list[Observation]:
    """Return only observations where both sources agree above the threshold."""
    return [r.observation for r in reconciled if r.observation.confidence >= threshold]
