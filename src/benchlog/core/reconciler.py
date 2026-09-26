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

from benchlog.core.board import BB830
from benchlog.core.esp32_source import Esp32Source, NullEsp32Source
from benchlog.core.models import Observation, ObservationKind, ObservationStatus
from benchlog.core.parts import esp32_devkit_v1_30_pins

_GPIO_TO_HOLE: dict[int, str] = {
    int(name.replace("GPIO", "")): hole
    for name, hole in esp32_devkit_v1_30_pins(top_row=1).items()
    if name.startswith("GPIO")
}
_STRIP_TO_GPIO: dict[str, int] = {
    BB830.strip(hole): gpio
    for gpio, hole in _GPIO_TO_HOLE.items()
}


@dataclass
class ReconciledObservation:
    observation: Observation
    vision_only: bool       # True if ESP32 couldn't confirm (no GPIO on that strip)
    esp32_confirms: bool    # True if ESP32 GPIO state changed in agreement


def _gpio_for_strip(hole: str) -> int | None:
    """Return the GPIO number whose pin sits in the same strip as `hole`, or None."""
    try:
        return _STRIP_TO_GPIO.get(BB830.strip(hole))
    except ValueError:
        return None


def reconcile(
    vision_obs: Sequence[Observation],
    serial: Esp32Source | None = None,
) -> list[ReconciledObservation]:
    """Merge vision observations with live ESP32 pin state.

    Args:
        vision_obs: Raw observations from occupancy_diff().
        serial:     Any Esp32Source implementation, or None to run vision-only.
                    Pass NullEsp32Source() to keep call sites uniform without a device.

    Returns:
        ReconciledObservation list, sorted high-confidence first.
    """
    src: Esp32Source = serial if serial is not None else NullEsp32Source()
    results: list[ReconciledObservation] = []

    for obs in vision_obs:
        # Which hole did vision flag?
        hole = (obs.after or obs.before or {}).get("hole")
        if hole is None:
            results.append(ReconciledObservation(obs, vision_only=True, esp32_confirms=False))
            continue

        gpio = _gpio_for_strip(hole)

        # No GPIO on this strip or source not alive → vision only
        if not src.is_alive() or gpio is None:
            updated = obs.model_copy(update={"confidence": obs.confidence, "uncertain_holes": [hole]})
            results.append(ReconciledObservation(updated, vision_only=True, esp32_confirms=False))
            continue

        # Check ESP32 agreement
        if obs.kind == ObservationKind.ADDED:
            esp32_agrees = src.gpio_stable_high(gpio)
        else:  # REMOVED
            esp32_agrees = src.gpio_stable_low(gpio)

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
    if src.is_alive():
        window = src.window()
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
