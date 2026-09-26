from pathlib import Path

from benchlog.core.models import Circuit, Component, Wire
from benchlog.core.netlist import natural_key, netlist
from benchlog.core.serialize import load_circuit

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"


def load(name: str) -> Circuit:
    return load_circuit(EXAMPLES / f"{name}.json")


def test_empty_board_has_no_nets() -> None:
    assert netlist(load("empty")).nets == []


def test_working_circuit_connections() -> None:
    nl = netlist(load("working"))
    assert nl.connected_pins("pot1.wiper") == ["esp32.GPIO34"]
    assert nl.connected_pins("esp32.GPIO13") == ["r1.1"]
    assert nl.connected_pins("r1.2") == ["led1.anode"]
    assert nl.net_of_pin("esp32.3V3").pins == ["esp32.3V3", "pot1.1"]
    assert nl.net_of_pin("esp32.GND2").pins == ["esp32.GND2", "led1.cathode", "pot1.3"]
    assert nl.net_of_pin("esp32.GND2").wires == ["w2", "w4", "w6"]
    # GND1 on the left side isn't wired to anything.
    assert nl.connected_pins("esp32.GND1") == []


def test_moved_wire_connects_pot_to_gpio12() -> None:
    nl = netlist(load("moved-wire"))
    assert nl.connected_pins("pot1.wiper") == ["esp32.GPIO12"]
    assert nl.connected_pins("esp32.GPIO34") == []


def test_move_within_strip_keeps_netlist() -> None:
    working = load("working")
    data = working.model_dump()
    wire = next(w for w in data["wires"] if w["id"] == "w5")
    wire["a"] = "G21"  # F21 -> G21 is the same strip
    moved = Circuit.model_validate(data)
    assert netlist(moved) == netlist(working)


def test_wire_chain_joins_strips() -> None:
    circuit = Circuit(
        components=[
            Component(id="r1", type="resistor", pins={"1": "A1", "2": "A5"}),
            Component(id="r2", type="resistor", pins={"1": "A9", "2": "A20"}),
        ],
        wires=[Wire(id="w1", a="B5", b="L+1"), Wire(id="w2", a="L+30", b="B9")],
    )
    nl = netlist(circuit)
    net = nl.net_of_pin("r1.2")
    assert net.pins == ["r1.2", "r2.1"]
    assert net.strips == ["5L", "9L", "L+"]
    assert net.wires == ["w1", "w2"]
    # A part's two pins are separate nets.
    assert nl.net_of_pin("r1.1") != nl.net_of_pin("r1.2")


def test_wire_with_unused_ends_is_its_own_net() -> None:
    nl = netlist(Circuit(wires=[Wire(id="w1", a="A1", b="A2")]))
    assert [(n.id, n.strips, n.wires) for n in nl.nets] == [("1L", ["1L", "2L"], ["w1"])]


def test_natural_key() -> None:
    assert sorted(["GPIO12", "GPIO4", "GPIO34"], key=natural_key) == ["GPIO4", "GPIO12", "GPIO34"]
