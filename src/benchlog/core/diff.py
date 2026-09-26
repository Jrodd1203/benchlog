"""Diff two circuits: electrical changes first, placement changes second. Owner: Person 1.

Placement changes say which wires/components were added, removed, moved, or edited.
Connection changes say which component pins became connected or disconnected, comparing the
netlists of both revisions. A move that stays within the same strips changes placement only.
"""

from itertools import combinations
from typing import Literal

from pydantic import BaseModel

from benchlog.core.board import TEMPLATES
from benchlog.core.models import Circuit, Component, Hole, Wire
from benchlog.core.netlist import natural_key, netlist

_FIELDS = {"wire": ("color", "label"), "component": ("type", "model", "value")}


class PlacementChange(BaseModel):
    kind: Literal["added", "removed", "moved", "modified"]
    object_type: Literal["wire", "component"]
    object_id: str
    # Same shape as Observation: {"a", "b"} for wires, the pins map for components.
    before: dict[str, Hole] | None = None
    after: dict[str, Hole] | None = None
    changed_ends: list[str] = []  # ends/pins whose hole changed (moves only)
    within_strip: bool = False  # every changed end stayed on its strip (moves only)
    changed_fields: list[str] = []  # e.g. ["value"]; a moved object can also have these


class ConnectionChange(BaseModel):
    kind: Literal["connected", "disconnected"]
    a: str  # component pin refs, naturally sorted so a < b
    b: str


class CircuitDiff(BaseModel):
    connections: list[ConnectionChange]
    placement: list[PlacementChange]

    @property
    def is_empty(self) -> bool:
        return not self.connections and not self.placement

    @property
    def electrical(self) -> bool:
        return bool(self.connections)


def _ends(obj: Wire | Component) -> dict[str, Hole]:
    return {"a": obj.a, "b": obj.b} if isinstance(obj, Wire) else dict(obj.pins)


def _same_placement(obj_type: str, before: dict[str, Hole], after: dict[str, Hole]) -> bool:
    # A wire plugged in the other way round is the same wire in the same place.
    if obj_type == "wire":
        return sorted(before.values()) == sorted(after.values())
    return before == after


def _placement(old: Circuit, new: Circuit) -> list[PlacementChange]:
    template = TEMPLATES[new.board]
    changes = []
    for obj_type, old_objs, new_objs in (
        ("component", old.components, new.components),
        ("wire", old.wires, new.wires),
    ):
        before_by_id = {o.id: o for o in old_objs}
        after_by_id = {o.id: o for o in new_objs}
        for obj_id in sorted(before_by_id.keys() | after_by_id.keys(), key=natural_key):
            b, a = before_by_id.get(obj_id), after_by_id.get(obj_id)
            if a is None:
                changes.append(PlacementChange(kind="removed", object_type=obj_type, object_id=obj_id, before=_ends(b)))
                continue
            if b is None:
                changes.append(PlacementChange(kind="added", object_type=obj_type, object_id=obj_id, after=_ends(a)))
                continue
            fields = [f for f in _FIELDS[obj_type] if getattr(b, f) != getattr(a, f)]
            before, after = _ends(b), _ends(a)
            if not _same_placement(obj_type, before, after):
                ends = [e for e in sorted(before.keys() | after.keys(), key=natural_key) if before.get(e) != after.get(e)]
                within = all(
                    e in before and e in after and template.strip(before[e]) == template.strip(after[e])
                    for e in ends
                )
                changes.append(
                    PlacementChange(
                        kind="moved", object_type=obj_type, object_id=obj_id, before=before, after=after,
                        changed_ends=ends, within_strip=within, changed_fields=fields,
                    )
                )  # fmt: skip
            elif fields:
                changes.append(
                    PlacementChange(
                        kind="modified", object_type=obj_type, object_id=obj_id, before=before, after=after,
                        changed_fields=fields,
                    )
                )  # fmt: skip
    return changes


def _connected_pairs(circuit: Circuit) -> set[tuple[str, str]]:
    pairs = set()
    for net in netlist(circuit).nets:
        pairs.update(combinations(net.pins, 2))  # pins are already naturally sorted
    return pairs


def _connections(old: Circuit, new: Circuit) -> list[ConnectionChange]:
    before, after = _connected_pairs(old), _connected_pairs(new)
    changes = [ConnectionChange(kind="disconnected", a=a, b=b) for a, b in before - after]
    changes += [ConnectionChange(kind="connected", a=a, b=b) for a, b in after - before]
    return sorted(changes, key=lambda c: (natural_key(c.a), natural_key(c.b), c.kind))


def diff(old: Circuit, new: Circuit) -> CircuitDiff:
    return CircuitDiff(connections=_connections(old, new), placement=_placement(old, new))


def describe(d: CircuitDiff) -> list[str]:
    """Plain-English lines, electrical changes first. Used by the CLI and PR comments."""
    lines = [f"{c.a} {c.kind} {'to' if c.kind == 'connected' else 'from'} {c.b}" for c in d.connections]
    for p in d.placement:
        if p.kind == "moved":
            ends = ", ".join(f"{e} {p.before.get(e, '-')} → {p.after.get(e, '-')}" for e in p.changed_ends)
            note = " (same strip, no electrical change)" if p.within_strip else ""
            lines.append(f"{p.object_id} moved: {ends}{note}")
        elif p.kind in ("added", "removed"):
            holes = ", ".join((p.after or p.before).values())
            lines.append(f"{p.object_id} {p.kind} ({holes})")
        if p.changed_fields:
            lines.append(f"{p.object_id} changed {', '.join(p.changed_fields)}")
    return lines
