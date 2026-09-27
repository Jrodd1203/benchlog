"""Part recognition: classifying measured objects, turning them into observations, anchoring an ESP32."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.models import Circuit, ComponentType, Observation
from benchlog.core.pairing import HoleChange, apply_observations, observations_from_occupancy
from benchlog.core.parts import esp32_devkit_v1_30_pins
from benchlog.core.project import Project, ProjectError
from benchlog.core.recognize import PartFeatures, PartGuess, classify
from benchlog.core.repo import Repo


def f(length, width, body_length, body_width, sat, val, color="grey", area=None) -> PartFeatures:
    return PartFeatures(
        length_mm=length, width_mm=width, area_mm2=area if area is not None else length * width * 0.6,
        body_length_mm=body_length, body_width_mm=body_width, body_color=color, body_saturation=sat, body_value=val,
    )  # fmt: skip


# Measured on real parts (benchlog scan --debug), one per row.
REAL = {
    "resistor": (f(19.5, 2.6, 7.1, 2.6, 84, 173, "blue"), "resistor"),
    "led": (f(11.2, 7.6, 7.8, 5.9, 164, 217, "red"), "led"),
    "diode": (f(18.5, 3.3, 7.3, 3.5, 8, 79), "diode"),
    "ceramic": (f(6.4, 2.4, 6.2, 2.4, 109, 167, "red"), "capacitor_ceramic"),
    "electrolytic": (f(9.8, 4.6, 6.6, 4.3, 5, 141), "capacitor_electrolytic"),
    "transistor": (f(6.5, 5.4, 5.6, 3.9, 20, 50, "black"), "transistor_npn"),
    "pot": (f(28.1, 22.0, 21.7, 17.1, 27, 142), "potentiometer"),
    "esp32": (f(67.7, 43.3, 60.5, 39.6, 23, 109), "esp32_devkit_v1_30"),
    "loose wire": (f(132.6, 21.8, 131.1, 4.2, 108, 203, "red"), "wire"),
    "flat wire": (f(54.9, 4.5, 53.5, 2.2, 20, 144), "wire"),
}


@pytest.mark.parametrize("name", REAL)
def test_classifies_real_parts(name: str) -> None:
    features, kind = REAL[name]
    assert classify(features)[0] == kind


def test_specks_and_oddities_are_left_alone() -> None:
    assert classify(f(1.0, 0.9, 1.2, 0.9, 13, 150, area=0.8))[0] is None
    assert classify(f(9.0, 9.0, 9.0, 9.0, 5, 200))[0] is None  # a big white blob: nothing we know


# ── Pairing with recognized parts ─────────────────────────────────────────────


def filled(*holes: str) -> list[HoleChange]:
    return [HoleChange(hole=h, change="filled") for h in holes]


def test_a_recognized_part_is_one_observation_not_junk_wires() -> None:
    resistor = PartGuess(kind="resistor", confidence=0.7, pins={"1": "C26", "2": "C33"}, claims=[f"C{i}" for i in range(26, 34)])
    # Only the body's holes registered (thin legs are faint), plus a shadow hole next to it.
    changes = filled("C29", "C30", "C31", "D31")
    [obs] = observations_from_occupancy(Circuit(), changes, [resistor])
    assert (obs.object_type, obs.object_id, obs.after) == ("component", "r1", {"1": "C26", "2": "C33"})
    assert obs.suggested.type == ComponentType.RESISTOR
    [r] = apply_observations(Circuit(), [obs]).components
    assert (r.type, r.pins) == (ComponentType.RESISTOR, {"1": "C26", "2": "C33"})


def test_a_recognized_wire_and_unrelated_holes() -> None:
    wire = PartGuess(kind="wire", confidence=0.8, pins={"a": "B56", "b": "J8"}, claims=["B56", "J8", "E30"], color="red")
    observations = observations_from_occupancy(Circuit(), filled("B56", "E30", "A1", "A5"), [wire])
    assert [(o.object_type, o.after) for o in observations] == [
        ("wire", {"a": "B56", "b": "J8"}),
        ("wire", {"a": "A1", "b": "A5"}),  # far from the wire: still paired the old way
    ]


def test_parts_already_in_the_circuit_arent_proposed_again() -> None:
    circuit = Circuit(components=[{"id": "r1", "type": "resistor", "pins": {"1": "C26", "2": "C33"}}])
    resistor = PartGuess(kind="resistor", confidence=0.7, pins={"1": "C26", "2": "C33"}, claims=["C29", "C30"])
    assert observations_from_occupancy(circuit, filled("C29", "C30"), [resistor]) == []


def test_ids_per_kind() -> None:
    parts = [
        PartGuess(kind="esp32_devkit_v1_30", confidence=0.5, pins=esp32_devkit_v1_30_pins(20), claims=["B20"]),
        PartGuess(kind="led", confidence=0.7, pins={"anode": "A50", "cathode": "A51"}, claims=["A50"]),
    ]
    observations = observations_from_occupancy(Circuit(), filled("B20", "A50"), parts)
    assert sorted(o.object_id for o in observations) == ["esp32", "led1"]


# ── Anchoring an ESP32 in review ──────────────────────────────────────────────


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    path = tmp_path / "project"
    Repo.init(path)
    monkeypatch.chdir(path)
    project, _ = Project.init(path)
    guess = Observation(
        id="obs1", kind="added", object_type="component", object_id="esp32", after=esp32_devkit_v1_30_pins(22, "A", "H"),
        confidence=0.35, suggested={"type": "esp32_devkit_v1_30"}, uncertain_holes=["A22", "A36"],
    )  # fmt: skip
    project.save_observations([guess])
    return project


def test_anchor_esp32_with_en_and_vin(project: Project) -> None:
    edited = project.edit_observation("obs1", {"EN": "B40", "VIN": "B26"})
    pins = edited.after
    assert (pins["EN"], pins["VIN"], pins["3V3"], pins["GPIO23"]) == ("B40", "B26", "I26", "I40")
    assert pins["GPIO13"] == "B27" and len(set(pins.values())) == 30
    # The normal way round too.
    pins = project.edit_observation("obs1", {"EN": "B10", "VIN": "B24"}).after
    assert pins == esp32_devkit_v1_30_pins(10, "B", "I")


def test_anchor_esp32_keeps_orientation_with_en_only(project: Project) -> None:
    pins = project.edit_observation("obs1", {"EN": "C5"}).after
    assert pins == esp32_devkit_v1_30_pins(5, "C", "J")


def test_anchor_esp32_validation(project: Project) -> None:
    with pytest.raises(ProjectError, match="same column as EN, 14 holes away"):
        project.edit_observation("obs1", {"EN": "B40", "VIN": "B30"})
    with pytest.raises(ProjectError, match="column A, B or C"):
        project.edit_observation("obs1", {"EN": "E40"})
    with pytest.raises(ProjectError, match="give EN"):
        project.edit_observation("obs1", {"VIN": "B26"})
    with pytest.raises(ProjectError, match="wouldn't fit"):
        project.edit_observation("obs1", {"EN": "B60"})


def test_anchor_esp32_from_the_cli(project: Project) -> None:
    result = CliRunner(env={"COLUMNS": "200"}).invoke(app, ["review", "edit", "obs1", "EN=B40", "VIN=B26"])
    assert result.exit_code == 0, result.output
    assert "added component esp32: esp32_devkit_v1_30, 30 pins" in result.output
    assert "check" not in result.output


def test_vivid_wire_with_a_plug_housing_is_a_wire() -> None:
    # A loose jumper piece: its thickest part is the dark plug housing, but it's red all along.
    piece = PartFeatures(
        length_mm=29.0, width_mm=6.4, area_mm2=60, body_length_mm=4.8, body_width_mm=3.8,
        body_color="grey", body_saturation=30, body_value=80, saturation=153,
    )  # fmt: skip
    assert classify(piece)[0] == "wire"
    # A diode has the same kind of dark body and legs, but it's colourless overall.
    assert classify(piece.model_copy(update={"saturation": 13}))[0] == "diode"


def test_wire_pieces_cut_by_the_board_edge_are_joined() -> None:
    pytest.importorskip("cv2")  # the vision code; the core-only CI job runs without it
    from types import SimpleNamespace

    from benchlog.vision.parts import _join_halves

    left = SimpleNamespace(color="red", holes=["G32", "H32"])
    right = SimpleNamespace(color="red", holes=["G42"])
    other = SimpleNamespace(color="green", holes=["A5"])
    [wire] = _join_halves([(left, "G32"), (other, "A5"), (right, "G42")])
    assert (wire.kind, wire.pins, wire.uncertain) == ("wire", {"a": "G32", "b": "G42"}, ["G32", "G42"])
    assert set(wire.claims) == {"G32", "H32", "G42"}


def test_close_calls_between_two_holes_are_flagged() -> None:
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    from benchlog.vision.parts import _Holes

    cal = SimpleNamespace(holes={"G32": (100.0, 100.0), "H32": (100.0, 120.0), "A1": (0.0, 0.0), "A2": (20.0, 0.0), "B1": (0.0, 20.0)})
    holes = _Holes(cal)
    assert not holes.ambiguous((100, 102))  # right on G32
    assert holes.ambiguous((100, 108.6))  # 0.43 of a hole from G32, 0.57 from H32: can't tell
    assert not holes.ambiguous((100, 104))


# ── Correcting a part the camera took for a wire ──────────────────────────────


@pytest.fixture
def wire_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    path = tmp_path / "project"
    Repo.init(path)
    monkeypatch.chdir(path)
    project, _ = Project.init(path)
    project.save_observations([
        Observation(id="obs1", kind="added", object_type="wire", object_id="w1", after={"a": "C26", "b": "C33"}, confidence=0.4),
        Observation(id="obs2", kind="added", object_type="wire", object_id="w2", after={"a": "R-5"}, confidence=0.4),
        Observation(id="obs3", kind="added", object_type="component", object_id="r1", after={"1": "A1", "2": "A5"},
                    confidence=0.7, suggested={"type": "resistor"}),
    ])  # fmt: skip
    return project


def test_a_wire_that_is_really_a_resistor(wire_project: Project) -> None:
    edited = wire_project.edit_observation("obs1", suggestion={"type": "resistor", "value": "220Ω"})
    assert (edited.object_type, edited.object_id, edited.after) == ("component", "r2", {"1": "C26", "2": "C33"})
    assert (edited.suggested.type, edited.suggested.value) == (ComponentType.RESISTOR, "220Ω")
    wire_project.accept_observations(["obs1"])
    [r] = [c for c in wire_project.load_circuit().components if c.id == "r2"]
    assert (r.type, r.value, r.pins) == (ComponentType.RESISTOR, "220Ω", {"1": "C26", "2": "C33"})
    assert wire_project.load_circuit().wires == []


def test_a_wire_that_is_really_an_led_with_a_missing_leg(wire_project: Project) -> None:
    with pytest.raises(ProjectError, match="cathode goes"):
        wire_project.edit_observation("obs2", suggestion={"type": "led"})
    edited = wire_project.edit_observation("obs2", {"b": "J2"}, {"type": "led"})
    assert (edited.object_id, edited.after) == ("led1", {"anode": "R-5", "cathode": "J2"})
    # Pins can be named directly too (the band side of a diode, say).
    edited = wire_project.edit_observation("obs1", {"cathode": "C26", "anode": "C33"}, {"type": "diode"})
    assert edited.after == {"anode": "C33", "cathode": "C26"}


def test_three_legged_parts_need_their_pins(wire_project: Project) -> None:
    with pytest.raises(ProjectError, match="give the transistor_npn's pins"):
        wire_project.edit_observation("obs1", suggestion={"type": "transistor_npn"})
    edited = wire_project.edit_observation("obs1", {"E": "C26", "B": "C27", "C": "C28"}, {"type": "transistor_npn"})
    assert (edited.object_id, edited.after) == ("q1", {"E": "C26", "B": "C27", "C": "C28"})
    with pytest.raises(ProjectError, match="unknown component type"):
        wire_project.edit_observation("obs2", suggestion={"type": "banana"})
    with pytest.raises(ProjectError, match="no value or model"):
        wire_project.edit_observation("obs2", suggestion={"value": "220Ω"})


def test_wire_to_resistor_from_the_cli(wire_project: Project) -> None:
    result = CliRunner(env={"COLUMNS": "200"}).invoke(app, ["review", "edit", "obs1", "type=resistor"])
    assert result.exit_code == 0, result.output
    assert "added component r2: resistor" in result.output
