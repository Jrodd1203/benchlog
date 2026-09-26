"""Which commit the physical board matches, and how to rewire it to match another.

    .benchlog/board_state.json   gitignored: {"matches_commit": sha | null, "updated_at": ...}

Switching branches changes circuit.json but not the wires on the bench, so benchlog remembers
the last commit the board was confirmed to match: a clean scan (nothing to review), a commit
made with nothing left to review, or a merge. `rewire_guide` turns the difference between two
circuits into steps a person can follow.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from benchlog.core.board import TEMPLATES
from benchlog.core.diff import PlacementChange, diff
from benchlog.core.models import Circuit, Hole
from benchlog.core.netlist import natural_key


class BoardState(BaseModel):
    matches_commit: str | None = Field(default=None, description="Commit the board matches; null if unknown or uncommitted.")
    updated_at: str | None = None
    circuit_fingerprint: str | None = Field(
        default=None, description="Fingerprint of the circuit the board was last confirmed to match (committed or not)."
    )


def fingerprint(circuit: Circuit) -> str:
    """Stable hash of a circuit's wiring (parts and wires), independent of object order.

    Saved serial results are left out: they describe the wiring, they aren't part of it.
    """
    data = circuit.model_dump(mode="json", exclude_none=True, exclude={"serial"})
    data["components"] = sorted(data["components"], key=lambda c: c["id"])
    data["wires"] = sorted(data["wires"], key=lambda w: w["id"])
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


class BoardStateStore:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "board_state.json"

    def load(self) -> BoardState:
        return BoardState.model_validate_json(self.path.read_text()) if self.path.exists() else BoardState()

    def set(self, commit: str | None, circuit: Circuit | None = None) -> BoardState:
        """Record that the board matches `circuit` (at `commit`, if it's committed)."""
        state = BoardState(
            matches_commit=commit,
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            circuit_fingerprint=fingerprint(circuit) if circuit is not None else None,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(state.model_dump_json(indent=2) + "\n")
        return state


# ── Rewire guide ──────────────────────────────────────────────────────────────


class RewireStep(BaseModel):
    step: int
    action: Literal["remove", "place", "move", "add", "change"]
    object_type: Literal["wire", "component"]
    object_id: str
    text: str = Field(description="What to do, e.g. 'Move w5: A4 (GPIO34) → A12 (GPIO12)'.")
    holes: list[Hole] = Field(default=[], description="Holes involved, for highlighting.")
    optional: bool = Field(default=False, description="A move within the same strip: no electrical change.")


def _labeller(circuit: Circuit):
    """hole -> 'A12 (GPIO12)' when an ESP32 pin shares its strip, else the hole name."""
    template = TEMPLATES[circuit.board]
    pin_on_strip = {}
    for c in circuit.components:
        if c.type.value.startswith("esp32"):
            for pin, hole in c.pins.items():
                pin_on_strip[template.strip(hole)] = pin

    def label(hole: Hole) -> str:
        pin = pin_on_strip.get(template.strip(hole)) if template.is_valid(hole) else None
        return f"{hole} ({pin})" if pin else hole

    return label


# Physical order: unplug first, then place parts, then route wires.
_ORDER = {"remove-wire": 0, "remove-component": 1, "place": 2, "move-component": 3, "move-wire": 4, "add": 5, "change": 6}


def objects(circuit: Circuit) -> dict[str, object]:
    return {o.id: o for o in [*circuit.components, *circuit.wires]}


def rewire_guide(from_circuit: Circuit, to_circuit: Circuit) -> list[RewireStep]:
    """Ordered steps that turn a board wired like `from_circuit` into `to_circuit`."""
    old, new = _labeller(from_circuit), _labeller(to_circuit)
    steps: list[tuple[int, RewireStep]] = []

    def add(order: str, change: PlacementChange, action: str, text: str, holes: list[Hole], optional: bool = False):
        step = RewireStep(
            step=0, action=action, object_type=change.object_type, object_id=change.object_id,
            text=text, holes=holes, optional=optional,
        )  # fmt: skip
        steps.append((_ORDER[order], step))

    for c in diff(from_circuit, to_circuit).placement:
        name = c.object_id
        if c.kind == "removed":
            holes = list(c.before.values())
            add(f"remove-{c.object_type}", c, "remove", f"Remove {name} from {', '.join(map(old, holes))}", holes)
        elif c.kind == "added":
            holes = list(c.after.values())
            if c.object_type == "wire":
                ends = " → ".join(new(h) for h in holes)
                add("add", c, "add", f"Add wire {name}: {ends}", holes)
            elif len(holes) > 4:
                first, last = sorted(holes, key=natural_key)[0], sorted(holes, key=natural_key)[-1]
                add("place", c, "place", f"Place {name} ({len(holes)} pins) from {first} to {last}", holes)
            else:
                pins = ", ".join(f"{pin} in {h}" for pin, h in sorted(c.after.items()))
                add("place", c, "place", f"Place {name}: {pins}", holes)
        elif c.kind == "moved":
            parts = [f"{old(c.before[e]) if e in c.before else '-'} → {new(c.after[e]) if e in c.after else '-'}" for e in c.changed_ends]
            text = f"Move {name}: {'; '.join(parts)}"
            if c.within_strip:
                text += " (same strip, optional)"
            holes = [h for e in c.changed_ends for h in (c.before.get(e), c.after.get(e)) if h]
            add(f"move-{c.object_type}", c, "move", text, holes, optional=c.within_strip)
        if c.changed_fields and c.before is not None and c.after is not None:
            was, now = objects(from_circuit)[name], objects(to_circuit)[name]
            fields = ", ".join(f"{f} {getattr(was, f)} → {getattr(now, f)}" for f in c.changed_fields)
            add("change", c, "change", f"Change {name}: {fields}", list(c.after.values()))

    ordered = [s for _, s in sorted(steps, key=lambda item: item[0])]
    return [s.model_copy(update={"step": i}) for i, s in enumerate(ordered, 1)]
