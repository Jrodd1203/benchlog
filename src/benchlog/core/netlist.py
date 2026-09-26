"""Compute electrical nets from a circuit and its breadboard template. Owner: Person 1.

A net is a set of points that are electrically connected. On a breadboard, strips are connected
internally, wires join two strips, and component pins sit on a strip. Parts like resistors are not
treated as connections: each of their pins is on its own net.
"""

import re

from pydantic import BaseModel

from benchlog.core.board import TEMPLATES
from benchlog.core.models import Circuit


def natural_key(s: str) -> list[int | str]:
    """Sort key that orders "GPIO4" before "GPIO12" and "4L" before "21R"."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", s)]


class Net(BaseModel):
    """One group of connected points. All lists are naturally sorted.

    `id` is the first pin (or strip, if the net has no pins). It is deterministic but not stable
    across revisions: compare nets between revisions by their `pins`, not their `id`.
    """

    id: str
    pins: list[str]  # "component.pin", e.g. "esp32.GPIO34"
    strips: list[str]
    wires: list[str]


class Netlist(BaseModel):
    nets: list[Net]

    def net_of_pin(self, ref: str) -> Net | None:
        return next((n for n in self.nets if ref in n.pins), None)

    def net_of_strip(self, strip: str) -> Net | None:
        return next((n for n in self.nets if strip in n.strips), None)

    def connected_pins(self, ref: str) -> list[str]:
        """Other component pins on the same net as `ref`."""
        net = self.net_of_pin(ref)
        return [p for p in net.pins if p != ref] if net else []


def netlist(circuit: Circuit) -> Netlist:
    template = TEMPLATES[circuit.board]
    parent: dict[str, str] = {}

    def find(strip: str) -> str:
        parent.setdefault(strip, strip)
        while parent[strip] != strip:
            parent[strip] = parent[parent[strip]]
            strip = parent[strip]
        return strip

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    pins_on: dict[str, str] = {}  # pin ref -> strip
    for c in circuit.components:
        for pin, hole in c.pins.items():
            pins_on[f"{c.id}.{pin}"] = find(template.strip(hole))
    for w in circuit.wires:
        union(template.strip(w.a), template.strip(w.b))

    groups: dict[str, dict[str, set[str]]] = {}

    def group(strip: str) -> dict[str, set[str]]:
        return groups.setdefault(find(strip), {"pins": set(), "strips": set(), "wires": set()})

    for strip in parent:
        group(strip)["strips"].add(strip)
    for ref, strip in pins_on.items():
        group(strip)["pins"].add(ref)
    for w in circuit.wires:
        group(template.strip(w.a))["wires"].add(w.id)

    nets = []
    for g in groups.values():
        pins = sorted(g["pins"], key=natural_key)
        strips = sorted(g["strips"], key=natural_key)
        wires = sorted(g["wires"], key=natural_key)
        nets.append(Net(id=pins[0] if pins else strips[0], pins=pins, strips=strips, wires=wires))
    return Netlist(nets=sorted(nets, key=lambda n: natural_key(n.id)))
