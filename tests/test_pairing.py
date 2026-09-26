from pathlib import Path

import pytest

from benchlog.core.models import Circuit, Component, Observation, Wire
from benchlog.core.pairing import HoleChange, apply_observations, hole_changes_between, observations_from_occupancy
from benchlog.core.serialize import load_circuit

EXAMPLES = Path(__file__).parent.parent / "examples"


def load(name: str) -> Circuit:
    return load_circuit(EXAMPLES / "circuits" / f"{name}.json")


def filled(hole: str, color: str | None = None, confidence: float = 1.0) -> HoleChange:
    return HoleChange(hole=hole, change="filled", color=color, confidence=confidence)


def emptied(hole: str, confidence: float = 1.0) -> HoleChange:
    return HoleChange(hole=hole, change="emptied", confidence=confidence)


def summary(observations: list[Observation]) -> list[tuple]:
    return [(o.kind.value, o.object_type, o.object_id, o.before, o.after, o.uncertain_holes) for o in observations]


def test_demo_scan_matches_example_observation() -> None:
    working, moved = load("working"), load("moved-wire")
    changes = hole_changes_between(working, moved)
    assert [(c.hole, c.change) for c in changes] == [("A4", "emptied"), ("A12", "filled")]

    [obs] = observations_from_occupancy(working, changes)
    expected = Observation.model_validate_json((EXAMPLES / "observations" / "moved-wire.json").read_text())
    assert (obs.kind, obs.object_type, obs.object_id, obs.before, obs.after) == (
        expected.kind, expected.object_type, expected.object_id, expected.before, expected.after,
    )  # fmt: skip
    assert obs.uncertain_holes == []
    assert apply_observations(working, [obs]) == moved


def test_empty_to_working_round_trip_holes() -> None:
    working = load("working")
    changes = hole_changes_between(Circuit(), working)
    observations = observations_from_occupancy(Circuit(), changes)
    # Without registered components, the camera sees 6 colored wires plus component pins; straight
    # runs (the ESP32's pin rows) come out as flat-wire guesses. Every changed hole is accounted for.
    assert all(o.kind.value == "added" for o in observations)
    seen = set()
    for o in observations:
        ends = list(o.after.values())
        seen.update(ends)
        if len(ends) == 2 and ends[0][0] == ends[1][0] and ends[0][1] not in "+-":
            lo, hi = sorted(int(e[1:]) for e in ends)
            seen.update(f"{ends[0][0]}{r}" for r in range(lo, hi + 1))
    assert {c.hole for c in changes} <= seen


def test_no_changes() -> None:
    assert observations_from_occupancy(load("working"), []) == []


def test_contradictory_changes_are_ignored() -> None:
    # A4 is occupied in the circuit (can't be newly filled); A30 is empty (can't be emptied).
    assert observations_from_occupancy(load("working"), [filled("A4"), emptied("A30")]) == []


def test_end_pulled_out_with_nowhere_to_go_is_uncertain_removal() -> None:
    [obs] = observations_from_occupancy(load("working"), [emptied("A4")])
    assert summary([obs]) == [("removed", "wire", "w5", {"a": "F21", "b": "A4"}, None, ["A4"])]
    assert obs.confidence < 0.5


def test_moved_end_prefers_matching_color() -> None:
    changes = [emptied("A4"), filled("A6", color="black"), filled("A12", color="yellow"), filled("A40", color="black")]
    observations = observations_from_occupancy(load("working"), changes)
    assert summary(observations)[0] == ("moved", "wire", "w5", {"a": "F21", "b": "A4"}, {"a": "F21", "b": "A12"}, [])
    # The two black holes left over become a new wire.
    assert summary(observations)[1] == ("added", "wire", "w7", None, {"a": "A6", "b": "A40"}, [])


def test_moved_end_without_colors_picks_nearest_but_flags_it() -> None:
    [move, extra] = observations_from_occupancy(load("working"), [emptied("A4"), filled("A6"), filled("A40")])
    assert move.after == {"a": "F21", "b": "A6"}
    assert move.uncertain_holes == ["A6"]
    assert move.confidence < 1
    assert extra.after == {"a": "A40"} and extra.uncertain_holes == ["A40"]


def test_whole_wire_moved_needs_color_to_keep_identity() -> None:
    circuit = Circuit(wires=[Wire(id="w1", a="A30", b="J30", color="red")])
    moved = observations_from_occupancy(circuit, [emptied("A30"), emptied("J30"), filled("A40", "red"), filled("J40", "red")])
    assert summary(moved) == [("moved", "wire", "w1", {"a": "A30", "b": "J30"}, {"a": "A40", "b": "J40"}, ["A40", "J40"])]

    replaced = observations_from_occupancy(circuit, [emptied("A30"), emptied("J30"), filled("A40"), filled("J40")])
    assert [(o.kind.value, o.object_id) for o in replaced] == [("removed", "w1"), ("added", "w2")]


def test_component_shifted_down_two_rows() -> None:
    circuit = Circuit(components=[Component(id="r1", type="resistor", pins={"1": "A30", "2": "A34"})])
    [obs] = observations_from_occupancy(circuit, [emptied("A30"), emptied("A34"), filled("A32"), filled("A36")])
    assert summary([obs]) == [("moved", "component", "r1", {"1": "A30", "2": "A34"}, {"1": "A32", "2": "A36"}, [])]
    assert apply_observations(circuit, [obs]).components[0].pins == {"1": "A32", "2": "A36"}


def test_component_partly_missing_is_uncertain() -> None:
    [obs] = observations_from_occupancy(load("working"), [emptied("H21")])
    assert (obs.kind.value, obs.object_id, obs.uncertain_holes) == ("removed", "pot1", ["H21"])
    assert obs.confidence < 0.5


def test_camera_confidence_carries_through() -> None:
    [obs] = observations_from_occupancy(load("working"), [emptied("A4", 0.9), filled("A12", confidence=0.7)])
    assert obs.confidence == 0.7


def test_apply_rejects_incomplete_new_wire() -> None:
    [obs] = observations_from_occupancy(load("working"), [filled("A40")])
    with pytest.raises(ValueError, match="other end"):
        apply_observations(load("working"), [obs])


def test_apply_added_and_removed_wires() -> None:
    working = load("working")
    observations = observations_from_occupancy(working, [emptied("J18"), emptied("R-18"), filled("A40"), filled("A45")])
    result = apply_observations(working, observations)
    ids = {w.id for w in result.wires}
    assert "w6" not in ids and "w7" in ids
    assert next(w for w in result.wires if w.id == "w7").model_dump(include={"a", "b"}) == {"a": "A40", "b": "A45"}


def test_flat_wire_covering_holes_is_one_wire() -> None:
    # A pre-cut jumper lying on C37..C43: every hole under it looks filled.
    changes = [filled(f"C{r}") for r in range(37, 44)]
    [obs] = observations_from_occupancy(Circuit(), changes)
    assert (obs.kind.value, obs.after, obs.uncertain_holes) == ("added", {"a": "C37", "b": "C43"}, ["C37", "C43"])


def test_flat_wire_across_a_row_and_leftovers() -> None:
    # One wire across row 10 (A..E), plus an unrelated pair of holes elsewhere.
    changes = [filled(f"{c}10") for c in "ABCDE"] + [filled("J40", "red"), filled("J50", "red")]
    observations = observations_from_occupancy(Circuit(), changes)
    assert sorted((o.after["a"], o.after["b"]) for o in observations) == [("A10", "E10"), ("J40", "J50")]


def test_two_adjacent_holes_are_not_a_flat_run() -> None:
    [obs] = observations_from_occupancy(Circuit(), [filled("C37"), filled("C38")])
    assert obs.after == {"a": "C37", "b": "C38"} and obs.uncertain_holes == []
