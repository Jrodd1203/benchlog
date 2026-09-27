"""Turn a reading of the physical board into pending observations. Owner: Person 1.

A `BoardReading` is which holes look occupied right now (from the camera, or simulated from a
circuit file). Each scan is compared with a *reference* reading rather than with the circuit, so
holes that always look occupied (under the ESP32, under a resistor body) cancel out.

The reference is the last reading whose observations were all reviewed; before any scan it is the
empty-board baseline from `benchlog.vision.capture`, or failing that, what the circuit implies.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from benchlog.core.board import TEMPLATES
from benchlog.core.models import Circuit, Hole, Observation
from benchlog.core.netlist import natural_key
from benchlog.core.recognize import PartGuess
from benchlog.core.pairing import HoleChange, explained_by_footprints, observations_from_occupancy, occupied_holes


# Nobody rewires this much between two scans; more changed holes than this means the camera misread
# the board. (Holes, not observations: a flat wire covers several holes, and a misread board can
# form long runs that pair into only a few observations.)
MAX_PLAUSIBLE_HOLE_CHANGES = 60


class MisreadError(ValueError):
    def __init__(self, count: int) -> None:
        super().__init__(
            f"{count} holes changed at once, which looks like a misread board. Check the lighting and "
            "alignment and scan again, or scan with sync if the board really matches the circuit"
        )
        self.count = count


class BoardReading(BaseModel):
    occupied: list[Hole]
    colors: dict[Hole, str] = Field(default={}, description="Wire color seen at a hole, if known.")
    confidence: float = Field(default=1.0, ge=0, le=1)
    warnings: list[str] = []
    source: str = Field(description="Where the reading came from, e.g. 'camera 0', 'image photo.jpg'.")
    parts: list[PartGuess] = Field(default=[], description="Objects the camera recognized (wires, resistors, ...).")
    taken_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


def reading_from_circuit(circuit: Circuit, source: str) -> BoardReading:
    """What a perfect camera would see for `circuit`."""
    colors = {h: w.color for w in circuit.wires if w.color for h in (w.a, w.b)}
    return BoardReading(occupied=sorted(occupied_holes(circuit), key=natural_key), colors=colors, source=source)


def changes_between(reference: BoardReading, current: BoardReading) -> list[HoleChange]:
    before, after = set(reference.occupied), set(current.occupied)
    changes = [HoleChange(hole=h, change="emptied", confidence=current.confidence) for h in before - after]
    changes += [
        HoleChange(hole=h, change="filled", color=current.colors.get(h), confidence=current.confidence)
        for h in after - before
    ]
    return sorted(changes, key=lambda c: natural_key(c.hole))


class ScanStore:
    """The readings kept in `.benchlog/scans/` (reference and latest)."""

    def __init__(self, state_dir: Path) -> None:
        self.dir = state_dir / "scans"
        self.reference_path = self.dir / "reference.json"
        self.latest_path = self.dir / "latest.json"
        self.baseline_path = state_dir / "baseline" / "baseline.json"

    def _load(self, path: Path) -> BoardReading | None:
        return BoardReading.model_validate_json(path.read_text()) if path.exists() else None

    def _save(self, path: Path, reading: BoardReading) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_text(reading.model_dump_json(indent=2) + "\n")

    def latest(self) -> BoardReading | None:
        return self._load(self.latest_path)

    def save_latest(self, reading: BoardReading) -> None:
        self._save(self.latest_path, reading)

    def promote_latest(self) -> None:
        """Make the latest reading the reference: its changes have all been reviewed."""
        latest = self.latest()
        if latest is not None:
            self._save(self.reference_path, latest)

    def reset(self, reference: BoardReading) -> None:
        """Start over from `reference` (e.g. after recalibrating), forgetting the latest reading."""
        self._save(self.reference_path, reference)
        self.latest_path.unlink(missing_ok=True)

    def reference(self, circuit: Circuit) -> BoardReading:
        reading = self._load(self.reference_path)
        if reading is not None:
            return reading
        if self.baseline_path.exists():
            data = json.loads(self.baseline_path.read_text())
            return BoardReading(occupied=data.get("occupied", []), source="empty-board baseline")
        return reading_from_circuit(circuit, source="accepted circuit (no baseline captured)")


def scan(circuit: Circuit, store: ScanStore, reading: BoardReading, pending: list[Observation]) -> list[Observation]:
    """Compare `reading` with the reference and return the new pending observations.

    If the previous scan's observations have all been reviewed, that scan becomes the new
    reference first. Otherwise this scan replaces the unreviewed one, measured from the same
    reference, so nothing seen before is lost. Raises MisreadError (saving nothing) when an
    implausible number of holes changed.
    """
    if not pending:
        store.promote_latest()
    reference = store.reference(circuit)
    changes = changes_between(reference, reading)
    # A recognized part (an ESP32 covers ~120 holes) is one change, not a flood of them.
    explained = explained_by_footprints({c.hole for c in changes if c.change == "filled"}, TEMPLATES[circuit.board])
    explained |= {h for part in reading.parts for h in part.claims}
    unexplained = [c for c in changes if c.hole not in explained]
    if len(unexplained) > MAX_PLAUSIBLE_HOLE_CHANGES:
        raise MisreadError(len(unexplained))  # before saving, so a misread never becomes the reference
    observations = observations_from_occupancy(circuit, changes, reading.parts)
    store.save_latest(reading)
    return observations
