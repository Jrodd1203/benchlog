from pathlib import Path

import pytest
from pydantic import ValidationError

from benchlog.core.board import BB830, BreadboardTemplate
from benchlog.core.models import Circuit, Observation, Wire
from benchlog.core.serialize import dump, load_circuit

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.mark.parametrize("name", ["empty", "working", "moved-wire"])
def test_example_circuits_are_valid(name: str) -> None:
    load_circuit(EXAMPLES / "circuits" / f"{name}.json")


def test_example_observation_is_valid() -> None:
    Observation.model_validate_json((EXAMPLES / "observations" / "moved-wire.json").read_text())


def test_examples_are_canonical(tmp_path: Path) -> None:
    for path in (EXAMPLES / "circuits").glob("*.json"):
        dump(load_circuit(path), tmp_path / path.name)
        assert (tmp_path / path.name).read_text() == path.read_text()


def test_moved_wire_only_changes_pot_signal() -> None:
    working = load_circuit(EXAMPLES / "circuits" / "working.json")
    moved = load_circuit(EXAMPLES / "circuits" / "moved-wire.json")
    assert working.components == moved.components
    changed = [(a, b) for a, b in zip(working.wires, moved.wires) if a != b]
    assert [(a.b, b.b) for a, b in changed] == [("A4", "A12")]


def test_strips() -> None:
    assert BB830.strip("A21") == BB830.strip("E21") == "21L"
    assert BB830.strip("F21") == BB830.strip("J21") == "21R"
    assert BB830.strip("R+1") == BB830.strip("R+50") == "R+"
    split = BreadboardTemplate(id="split", rows=63, rail_length=50, rail_split=True)
    assert split.strip("R+1") != split.strip("R+50")


@pytest.mark.parametrize("hole", ["K1", "A0", "A64", "R+51", "X+1", "a1"])
def test_invalid_holes(hole: str) -> None:
    assert not BB830.is_valid(hole)


def test_circuit_rejects_shared_hole() -> None:
    with pytest.raises(ValidationError, match="already used"):
        Circuit(wires=[Wire(id="w1", a="A1", b="A2"), Wire(id="w2", a="A1", b="A3")])


def test_circuit_rejects_unknown_hole() -> None:
    with pytest.raises(ValidationError, match="not a hole"):
        Circuit(wires=[Wire(id="w1", a="A1", b="Z9")])
