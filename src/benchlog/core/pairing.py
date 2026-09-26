"""Turn per-hole changes from a scan into per-object Observations. Owner: Person 1.

The camera only reports which holes filled or emptied. This module explains those changes using
the last accepted circuit: an emptied hole that held `w5.b` plus a newly filled hole means w5's
end moved there. Anything it can't explain confidently is marked uncertain for the user to confirm.
"""

from itertools import count
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from benchlog.core.board import LEFT_COLUMNS, RIGHT_COLUMNS, TEMPLATES, BreadboardTemplate
from benchlog.core.models import Circuit, Component, Hole, Observation, ObservationKind, ObservationStatus, Wire
from benchlog.core.netlist import natural_key

COLUMNS = LEFT_COLUMNS + RIGHT_COLUMNS
# Confidence multipliers for guesses that need the user's eye.
AMBIGUOUS = 0.6
UNRESOLVED = 0.4


class HoleChange(BaseModel):
    """What the camera reports for one hole between the last accepted state and now."""

    model_config = ConfigDict(extra="forbid")

    hole: Hole
    change: Literal["filled", "emptied"]
    color: str | None = Field(default=None, description="Wire color seen at a filled hole, if known.")
    confidence: float = Field(default=1.0, ge=0, le=1)


def occupied_holes(circuit: Circuit) -> dict[Hole, tuple[str, str, str]]:
    """Hole -> (object type, object id, end or pin name) for everything plugged in."""
    owners = {}
    for c in circuit.components:
        for pin, hole in c.pins.items():
            owners[hole] = ("component", c.id, pin)
    for w in circuit.wires:
        owners[w.a] = ("wire", w.id, "a")
        owners[w.b] = ("wire", w.id, "b")
    return owners


def _position(hole: Hole) -> tuple[float, float]:
    """Approximate (x, y) on the board in 0.1" steps, for "nearest hole" decisions."""
    if hole[0] in COLUMNS and hole[1] not in "+-":
        col = COLUMNS.index(hole[0])
        return (col + (2 if col >= len(LEFT_COLUMNS) else 0), float(hole[1:]))
    rail_x = {"L-": -3, "L+": -2, "R+": 14, "R-": 15}[hole[:2]]
    return (rail_x, int(hole[2:]) * 63 / 50)


def _distance(a: Hole, b: Hole) -> float:
    (ax, ay), (bx, by) = _position(a), _position(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _same_color(a: str | None, b: str | None) -> bool | None:
    """True/False when both colors are known, None when either is unknown."""
    if a is None or b is None:
        return None
    return a.strip().lower() == b.strip().lower()


def _shift(hole: Hole, rows: int, cols: int, template: BreadboardTemplate) -> Hole | None:
    if hole[0] not in COLUMNS or hole[1] in "+-":
        return None  # parts on rails aren't matched by shape
    col = COLUMNS.index(hole[0]) + cols
    if not 0 <= col < len(COLUMNS):
        return None
    shifted = f"{COLUMNS[col]}{int(hole[1:]) + rows}"
    return shifted if template.is_valid(shifted) else None


MIN_FLAT_WIRE_HOLES = 3


def _line_key(hole: Hole) -> list[tuple[str, int]]:
    """The straight lines a hole lies on, each as (line id, position along it)."""
    if hole[:2] in ("L+", "L-", "R+", "R-"):
        return [(hole[:2], int(hole[2:]))]
    col, row = hole[0], int(hole[1:])
    half = "L" if col in LEFT_COLUMNS else "R"
    # Along a column letter (rows change), or across a row within one half (letters change).
    return [(f"col {col}", row), (f"row {row}{half}", COLUMNS.index(col))]


def _straight_runs(holes: list[Hole]) -> list[list[Hole]]:
    """Longest runs of 3+ consecutive holes along one line, each hole used at most once."""
    lines: dict[str, dict[int, Hole]] = {}
    for hole in holes:
        for line, pos in _line_key(hole):
            lines.setdefault(line, {})[pos] = hole
    runs = []
    for line, by_pos in lines.items():
        positions = sorted(by_pos)
        start = 0
        for i in range(1, len(positions) + 1):
            if i == len(positions) or positions[i] != positions[i - 1] + 1:
                if i - start >= MIN_FLAT_WIRE_HOLES:
                    runs.append([by_pos[p] for p in positions[start:i]])
                start = i
    runs.sort(key=len, reverse=True)
    used: set[Hole] = set()
    result = []
    for run in runs:
        if not used.intersection(run):
            result.append(run)
            used.update(run)
    return result


class _Pairer:
    def __init__(self, circuit: Circuit, changes: list[HoleChange]) -> None:
        self.circuit = circuit
        self.template = TEMPLATES[circuit.board]
        owners = occupied_holes(circuit)
        # Changes that contradict the circuit (a known-empty hole emptied, a known-occupied hole
        # filled) are camera noise and ignored.
        self.emptied = {c.hole: c for c in changes if c.change == "emptied" and c.hole in owners}
        self.free = {c.hole: c for c in changes if c.change == "filled" and c.hole not in owners}
        self.lost: dict[tuple[str, str], list[str]] = {}
        for hole in self.emptied:
            obj_type, obj_id, end = owners[hole]
            self.lost.setdefault((obj_type, obj_id), []).append(end)
        self.observations: list[Observation] = []
        self._obs_ids = count(1)
        used_ids = {w.id for w in circuit.wires} | {c.id for c in circuit.components}
        self._wire_ids = (f"w{n}" for n in count(1) if f"w{n}" not in used_ids)

    def run(self) -> list[Observation]:
        wires = {w.id: w for w in self.circuit.wires}
        components = {c.id: c for c in self.circuit.components}
        lost = sorted(self.lost.items(), key=lambda item: natural_key(item[0][1]))
        # Single-end wire moves are the most common change, so they claim filled holes first.
        for (obj_type, obj_id), ends in lost:
            if obj_type == "wire" and len(ends) == 1:
                self._wire_end_moved(wires[obj_id], ends[0])
        for (obj_type, obj_id), ends in lost:
            if obj_type == "wire" and len(ends) == 2:
                self._wire_both_ends(wires[obj_id])
            elif obj_type == "component":
                self._component(components[obj_id], ends)
        self._new_wires()
        return self.observations

    def _add(self, kind: ObservationKind, obj_type: str, obj_id: str, confidence: float, **fields) -> None:
        self.observations.append(
            Observation(
                id=f"obs{next(self._obs_ids)}", kind=kind, object_type=obj_type, object_id=obj_id,
                confidence=round(confidence, 3), status=ObservationStatus.PENDING, **fields,
            )
        )  # fmt: skip

    def _candidates(self, near: Hole, color: str | None, exclude: set[Hole] = frozenset()) -> list[Hole]:
        """Free holes that could be where a wire end went: same (or unknown) color, nearest first."""
        options = [h for h in self.free if h not in exclude and _same_color(color, self.free[h].color) is not False]
        return sorted(options, key=lambda h: (_same_color(color, self.free[h].color) is not True, _distance(near, h)))

    def _is_certain(self, chosen: Hole, candidates: list[Hole], color: str | None) -> bool:
        if len(candidates) == 1:
            return True
        color_matches = [h for h in candidates if _same_color(color, self.free[h].color)]
        return color_matches == [chosen]

    def _wire_end_moved(self, wire: Wire, end: str) -> None:
        before = {"a": wire.a, "b": wire.b}
        old = before[end]
        confidence = self.emptied[old].confidence
        candidates = self._candidates(old, wire.color)
        if not candidates:
            # The end was pulled out and we can't see where it went (or it's hidden).
            self._add(ObservationKind.REMOVED, "wire", wire.id, confidence * UNRESOLVED, before=before, uncertain_holes=[old])
            return
        new = candidates[0]
        certain = self._is_certain(new, candidates, wire.color)
        change = self.free.pop(new)
        self._add(
            ObservationKind.MOVED, "wire", wire.id,
            min(confidence, change.confidence) * (1 if certain else AMBIGUOUS),
            before=before, after={**before, end: new}, uncertain_holes=[] if certain else [new],
        )  # fmt: skip

    def _wire_both_ends(self, wire: Wire) -> None:
        before = {"a": wire.a, "b": wire.b}
        confidence = min(self.emptied[wire.a].confidence, self.emptied[wire.b].confidence)
        new_a = next(iter(self._candidates(wire.a, wire.color)), None)
        new_b = next(iter(self._candidates(wire.b, wire.color, exclude={new_a})), None) if new_a else None
        # Only call it a move when the color confirms it; otherwise it's a removal plus a new wire.
        if new_a and new_b and _same_color(wire.color, self.free[new_a].color) and _same_color(wire.color, self.free[new_b].color):
            conf = min(confidence, self.free.pop(new_a).confidence, self.free.pop(new_b).confidence) * AMBIGUOUS
            self._add(ObservationKind.MOVED, "wire", wire.id, conf, before=before, after={"a": new_a, "b": new_b}, uncertain_holes=[new_a, new_b])
        else:
            self._add(ObservationKind.REMOVED, "wire", wire.id, confidence, before=before)

    def _component(self, component: Component, lost_pins: list[str]) -> None:
        before = dict(component.pins)
        confidence = min(self.emptied[before[p]].confidence for p in lost_pins)
        if len(lost_pins) < len(before):
            # Only some pins disappeared: likely occlusion or a half-pulled part. Ask the user.
            holes = sorted((before[p] for p in lost_pins), key=natural_key)
            self._add(ObservationKind.REMOVED, "component", component.id, confidence * UNRESOLVED, before=before, uncertain_holes=holes)
            return
        # Look for the same pin shape shifted somewhere in the newly filled holes.
        anchor = before[lost_pins[0]]
        for target in sorted(self.free, key=lambda h: _distance(anchor, h)):
            if anchor[0] not in COLUMNS or target[0] not in COLUMNS or "+" in anchor + target or "-" in anchor + target:
                continue
            rows = int(target[1:]) - int(anchor[1:])
            cols = COLUMNS.index(target[0]) - COLUMNS.index(anchor[0])
            after = {pin: _shift(hole, rows, cols, self.template) for pin, hole in before.items()}
            if all(h in self.free for h in after.values()):
                conf = min([confidence] + [self.free.pop(h).confidence for h in after.values()])
                self._add(ObservationKind.MOVED, "component", component.id, conf, before=before, after=after)
                return
        self._add(ObservationKind.REMOVED, "component", component.id, confidence, before=before)

    def _flat_wires(self) -> None:
        """A straight run of 3+ neighbouring filled holes is one wire lying flat across them.

        Pre-cut jumpers lie on the board, so from above they cover every hole between their ends.
        The run's two ends are taken as the wire's ends, marked for the user to check.
        """
        for run in _straight_runs(list(self.free)):
            a, b = run[0], run[-1]
            conf = min(self.free[h].confidence for h in run) * AMBIGUOUS
            for hole in run:
                del self.free[hole]
            self._add(ObservationKind.ADDED, "wire", next(self._wire_ids), conf, after={"a": a, "b": b}, uncertain_holes=[a, b])

    def _new_wires(self) -> None:
        """Pair leftover filled holes into new wires, by color when the camera saw one."""
        self._flat_wires()
        by_color: dict[str | None, list[Hole]] = {}
        for hole in sorted(self.free, key=natural_key):
            color = self.free[hole].color
            by_color.setdefault(color.strip().lower() if color else None, []).append(hole)
        total = len(self.free)
        for color, holes in sorted(by_color.items(), key=lambda item: item[0] or ""):
            # Two holes in total (or two of one known color) is a confident new wire; anything else is a guess.
            certain = len(holes) == 2 and (color is not None or total == 2)
            while len(holes) >= 2:
                a = holes.pop(0)
                b = min(holes, key=lambda h: _distance(a, h)) if not certain else holes.pop(0)
                if b in holes:
                    holes.remove(b)
                conf = min(self.free[a].confidence, self.free[b].confidence) * (1 if certain else AMBIGUOUS)
                self._add(
                    ObservationKind.ADDED, "wire", next(self._wire_ids), conf,
                    after={"a": a, "b": b}, uncertain_holes=[] if certain else [a, b],
                )  # fmt: skip
            for hole in holes:
                # One end we can see, the other hidden or not yet plugged in.
                self._add(
                    ObservationKind.ADDED, "wire", next(self._wire_ids), self.free[hole].confidence * UNRESOLVED,
                    after={"a": hole}, uncertain_holes=[hole],
                )  # fmt: skip


def observations_from_occupancy(circuit: Circuit, changes: list[HoleChange]) -> list[Observation]:
    """Explain a scan's hole changes relative to the last accepted `circuit`."""
    return _Pairer(circuit, changes).run()


def hole_changes_between(old: Circuit, new: Circuit) -> list[HoleChange]:
    """The hole changes a perfect camera would report. Used for tests and the demo fallback."""
    old_holes, new_holes = occupied_holes(old), occupied_holes(new)
    colors = {w.a: w.color for w in new.wires} | {w.b: w.color for w in new.wires}
    changes = [HoleChange(hole=h, change="emptied") for h in old_holes.keys() - new_holes.keys()]
    changes += [HoleChange(hole=h, change="filled", color=colors.get(h)) for h in new_holes.keys() - old_holes.keys()]
    return sorted(changes, key=lambda c: natural_key(c.hole))


def apply_observations(circuit: Circuit, observations: list[Observation]) -> Circuit:
    """The circuit after accepting `observations`. Raises ValueError for ones that can't be applied yet."""
    data = circuit.model_dump()
    for obs in observations:
        key = "wires" if obs.object_type == "wire" else "components"
        objs = data[key]
        index = next((i for i, o in enumerate(objs) if o["id"] == obs.object_id), None)
        if obs.kind == ObservationKind.REMOVED:
            if index is None:
                raise ValueError(f"{obs.id}: {obs.object_id} is not in the circuit")
            objs.pop(index)
        elif obs.kind == ObservationKind.MOVED:
            if index is None:
                raise ValueError(f"{obs.id}: {obs.object_id} is not in the circuit")
            if obs.object_type == "wire":
                objs[index].update(obs.after)
            else:
                objs[index]["pins"] = dict(obs.after)
        elif obs.object_type == "component":
            raise ValueError(f"{obs.id}: register the new component's type and value before accepting it")
        elif set(obs.after or {}) != {"a", "b"}:
            raise ValueError(f"{obs.id}: the new wire's other end hasn't been confirmed")
        else:
            objs.append({"id": obs.object_id, **obs.after})
    return Circuit.model_validate(data)
