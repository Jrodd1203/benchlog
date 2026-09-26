"""Small circuit helpers shared by the checks."""

import re

from benchlog.core.models import Circuit, ComponentType
from benchlog.core.netlist import Net, Netlist

GPIO_RE = re.compile(r"^GPIO(\d+)$")
SUPPLY_PINS = {"3V3", "VIN"}  # VIN is the 5 V pin on the DevKit


def esp32_id(circuit: Circuit) -> str | None:
    return next((c.id for c in circuit.components if c.type == ComponentType.ESP32_DEVKIT_V1_30), None)


def gpio_pins(circuit: Circuit, esp32: str) -> dict[int, str]:
    """GPIO number -> pin ref, e.g. {12: "esp32.GPIO12"}."""
    component = next(c for c in circuit.components if c.id == esp32)
    return {int(m[1]): f"{esp32}.{pin}" for pin in component.pins if (m := GPIO_RE.match(pin))}


def power_pins(net: Net, esp32: str) -> tuple[list[str], list[str]]:
    """(ground pin refs, supply pin refs) of the ESP32 on `net`."""
    own = [p for p in net.pins if p.startswith(f"{esp32}.")]
    grounds = [p for p in own if p.split(".", 1)[1].startswith("GND")]
    supplies = [p for p in own if p.split(".", 1)[1] in SUPPLY_PINS]
    return grounds, supplies


def attached(nl: Netlist, ref: str) -> tuple[list[str], list[str]]:
    """(other pin refs, wire ids) on the same net as pin `ref`."""
    net = nl.net_of_pin(ref)
    if net is None:
        return [], []
    return [p for p in net.pins if p != ref], net.wires
