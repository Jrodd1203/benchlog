from pathlib import Path

from benchlog.core.diff import describe, diff
from benchlog.core.models import Circuit
from benchlog.core.serialize import load_circuit

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"


def load(name: str) -> Circuit:
    return load_circuit(EXAMPLES / f"{name}.json")


def edit(circuit: Circuit, obj_type: str, obj_id: str, **changes) -> Circuit:
    data = circuit.model_dump()
    obj = next(o for o in data[f"{obj_type}s"] if o["id"] == obj_id)
    obj.update(changes)
    return Circuit.model_validate(data)


def test_same_circuit_is_empty() -> None:
    assert diff(load("working"), load("working")).is_empty


def test_demo_moved_wire() -> None:
    d = diff(load("working"), load("moved-wire"))
    assert [(c.kind, c.a, c.b) for c in d.connections] == [
        ("connected", "esp32.GPIO12", "pot1.wiper"),
        ("disconnected", "esp32.GPIO34", "pot1.wiper"),
    ]
    [move] = d.placement
    assert (move.kind, move.object_id, move.changed_ends, move.within_strip) == ("moved", "w5", ["b"], False)
    assert move.before == {"a": "F21", "b": "A4"}
    assert move.after == {"a": "F21", "b": "A12"}
    assert describe(d) == [
        "esp32.GPIO12 connected to pot1.wiper",
        "esp32.GPIO34 disconnected from pot1.wiper",
        "w5 moved: b A4 → A12",
    ]


def test_move_within_strip_is_placement_only() -> None:
    working = load("working")
    d = diff(working, edit(working, "wire", "w5", a="G21"))
    assert not d.electrical
    [move] = d.placement
    assert move.within_strip
    assert describe(d) == ["w5 moved: a F21 → G21 (same strip, no electrical change)"]


def test_reversed_wire_is_unchanged() -> None:
    working = load("working")
    assert diff(working, edit(working, "wire", "w5", a="A4", b="F21")).is_empty


def test_value_change_is_modified() -> None:
    working = load("working")
    d = diff(working, edit(working, "component", "r1", value="330Ω"))
    assert not d.electrical
    assert [(p.kind, p.object_id, p.changed_fields) for p in d.placement] == [("modified", "r1", ["value"])]
    assert describe(d) == ["r1 changed value"]


def test_empty_to_working_adds_everything() -> None:
    d = diff(load("empty"), load("working"))
    assert all(p.kind == "added" for p in d.placement)
    assert [p.object_id for p in d.placement] == ["esp32", "led1", "pot1", "r1", "w1", "w2", "w3", "w4", "w5", "w6"]
    assert all(c.kind == "connected" for c in d.connections)
    assert any((c.a, c.b) == ("esp32.GPIO34", "pot1.wiper") for c in d.connections)


def test_removed_wire_disconnects() -> None:
    working = load("working")
    data = working.model_dump()
    data["wires"] = [w for w in data["wires"] if w["id"] != "w5"]
    d = diff(working, Circuit.model_validate(data))
    assert [(p.kind, p.object_id) for p in d.placement] == [("removed", "w5")]
    assert [(c.kind, c.a, c.b) for c in d.connections] == [("disconnected", "esp32.GPIO34", "pot1.wiper")]
