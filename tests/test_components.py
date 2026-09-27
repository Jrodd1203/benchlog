"""Stage 1 component detection: ESP32 recognition, component suggestions, camera colors."""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.board import BB830
from benchlog.core.models import Circuit, ComponentType, Observation, ObservationKind, Suggestion
from benchlog.core.pairing import HoleChange, apply_observations, find_esp32s, observations_from_occupancy
from benchlog.core.parts import esp32_devkit_v1_30_pins
from benchlog.core.project import Project, ProjectError
from benchlog.core.repo import Repo
from benchlog.core.scan import MAX_PLAUSIBLE_HOLE_CHANGES, BoardReading, ScanStore, scan

runner = CliRunner(env={"COLUMNS": "220"})


def esp32_holes(top: int = 1, left: str = "B", right: str = "I", covered: bool = True) -> set[str]:
    """What the camera sees change when an ESP32 goes in: its pins, and the holes under the board."""
    holes = set(esp32_devkit_v1_30_pins(top, left, right).values())
    if covered:
        cols = "ABCDEFGHIJ"
        holes |= {f"{c}{r}" for c in cols[cols.index(left) : cols.index(right) + 1] for r in range(top, top + 15)}
    return holes


def filled(holes: set[str]) -> list[HoleChange]:
    return [HoleChange(hole=h, change="filled") for h in sorted(holes)]


# ── Recognition ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("top, left, right", [(1, "B", "I"), (20, "B", "I"), (40, "C", "J"), (5, "A", "H")])
def test_esp32_found_anywhere(top: int, left: str, right: str) -> None:
    [(pins, block)] = find_esp32s(esp32_holes(top, left, right), BB830)
    assert pins == esp32_devkit_v1_30_pins(top, left, right)
    assert block == esp32_holes(top, left, right)


def test_esp32_with_hidden_pins_and_a_wire_nearby() -> None:
    holes = esp32_holes(covered=False) - {"B3", "I7", "I8"} | {"A30", "A35"}
    [obs, wire] = observations_from_occupancy(Circuit(), filled(holes))
    assert (obs.kind, obs.object_type, obs.object_id) == (ObservationKind.ADDED, "component", "esp32")
    assert obs.suggested.type == ComponentType.ESP32_DEVKIT_V1_30
    assert obs.after["GPIO13"] == "B14" and obs.after["3V3"] == "I15"
    assert obs.confidence == pytest.approx(27 / 30)
    assert (wire.object_type, wire.after) == ("wire", {"a": "A30", "b": "A35"})


def test_not_an_esp32() -> None:
    # A long flat wire, and too few pins, stay wires.
    assert find_esp32s({f"B{r}" for r in range(1, 16)}, BB830) == []
    assert find_esp32s(set(list(esp32_devkit_v1_30_pins(1).values())[:20]), BB830) == []


def test_accepting_a_recognized_esp32_adds_it() -> None:
    [obs] = observations_from_occupancy(Circuit(), filled(esp32_holes()))
    circuit = apply_observations(Circuit(), [obs])
    [esp32] = circuit.components
    assert (esp32.id, esp32.type, esp32.model) == ("esp32", ComponentType.ESP32_DEVKIT_V1_30, "DOIT ESP32 DevKit V1")
    assert esp32.pins == esp32_devkit_v1_30_pins(1)
    # A second one gets its own id.
    [second] = observations_from_occupancy(circuit, filled(esp32_holes(top=40)))
    assert second.object_id == "esp32_2"


def test_an_esp32_isnt_a_misread(tmp_path: Path) -> None:
    holes = esp32_holes()
    assert len(holes) > MAX_PLAUSIBLE_HOLE_CHANGES
    reading = BoardReading(occupied=sorted(holes), source="camera")
    [obs] = scan(Circuit(), ScanStore(tmp_path), reading, pending=[])
    assert obs.suggested.type == ComponentType.ESP32_DEVKIT_V1_30


def test_unknown_component_must_be_named_before_accepting() -> None:
    obs = Observation(id="obs1", kind="added", object_type="component", object_id="part1",
                      after={"1": "A40", "2": "A44"}, confidence=0.5)  # fmt: skip
    with pytest.raises(ValueError, match="say what this component is"):
        apply_observations(Circuit(), [obs])
    named = obs.model_copy(update={"suggested": Suggestion(type="resistor", value="220Ω")})
    [r] = apply_observations(Circuit(), [named]).components
    assert (r.type, r.value, r.pins) == (ComponentType.RESISTOR, "220Ω", {"1": "A40", "2": "A44"})


# ── Review edit ───────────────────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    project, _ = Project.init(path)
    part = Observation(id="obs1", kind="added", object_type="component", object_id="part1",
                       after={"anode": "E40", "cathode": "F40"}, confidence=0.5)  # fmt: skip
    project.save_observations([part])
    return project


def run(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_name_a_component_and_fix_its_polarity(project: Project) -> None:
    assert "unknown part (say what it is: type=...)" in run("review")
    out = run("review", "edit", "obs1", "type=led", "value=red", "anode=F40", "cathode=E40")
    assert "added component part1: led (red), E40, F40" in out
    run("review", "accept", "obs1")
    [led] = project.load_circuit().components
    assert (led.type, led.value, led.pins) == (ComponentType.LED, "red", {"anode": "F40", "cathode": "E40"})


def test_edit_validation_for_components(project: Project) -> None:
    with pytest.raises(ProjectError, match="unknown component type 'flux_capacitor'"):
        project.edit_observation("obs1", suggestion={"type": "flux_capacitor"})
    with pytest.raises(ProjectError, match="not colour"):
        project.edit_observation("obs1", suggestion={"colour": "red"})
    wire = Observation(id="obs2", kind="added", object_type="wire", object_id="w1", after={"a": "A1", "b": "A5"}, confidence=1)
    project.save_observations([*project.observations(), wire])
    with pytest.raises(ProjectError, match="no value or model"):
        project.edit_observation("obs2", suggestion={"value": "220Ω"})  # a type would turn it into a part


def test_api_edit_names_a_component(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from benchlog.server.app import app as api

    monkeypatch.setenv("BENCHLOG_PROJECT", str(project.repo.root))
    client = TestClient(api)
    body = client.patch("/api/observations/obs1", json={"suggested": {"type": "flux_capacitor"}})
    assert body.status_code == 400
    body = client.patch("/api/observations/obs1", json={"suggested": {"type": "diode", "model": "1N4148"}}).json()
    assert body["suggested"] == {"type": "diode", "value": None, "model": "1N4148"}
    body = client.patch("/api/observations/obs1", json={"suggested": {"type": "led", "value": "green"}}).json()
    assert body["suggested"] == {"type": "led", "value": "green", "model": None}


# ── Colors ────────────────────────────────────────────────────────────────────


cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


@pytest.mark.parametrize(
    "bgr, name",
    [
        ((30, 30, 200), "red"), ((0, 120, 240), "orange"), ((20, 220, 230), "yellow"), ((40, 160, 30), "green"),
        ((200, 90, 20), "blue"), ((160, 40, 130), "purple"), ((20, 20, 20), "black"), ((235, 235, 235), "white"),
        ((130, 130, 130), "grey"), ((30, 20, 210), "red"), ((140, 20, 200), "purple"),
    ],
)  # fmt: skip
def test_color_names(bgr: tuple[int, int, int], name: str) -> None:
    from benchlog.vision.colors import color_name

    patch = np.full((9, 9, 3), bgr, np.uint8)
    noisy = np.clip(patch.astype(int) + np.random.default_rng(1).integers(-12, 13, patch.shape), 0, 255).astype(np.uint8)
    assert color_name(noisy) == name


def test_scan_reports_wire_colors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A calibrated camera scan names the color at each occupied hole, so pairing can use it."""
    from benchlog import camera as camera_glue
    from benchlog.vision import calibration as cal_mod

    fixture = Path(__file__).parent / "fixtures" / "bb830_white_topdown.jpg"
    board = cv2.imread(str(fixture))[74:437, 62:1118]
    desk = np.full((900, 1600, 3), 35, np.uint8)
    desk[268 : 268 + board.shape[0], 272 : 272 + board.shape[1]] = board
    editor = cal_mod.CalibrationEditor()
    editor.update(desk, now=0.0)
    cal = cal_mod.Calibration(corners=editor.corners, holes=editor.holes, reference=desk.copy())
    frame = desk.copy()
    radius = round(cal_mod.hole_pitch_px(cal.holes) * 0.4)
    for hole, bgr in {"A40": (0, 200, 0), "A45": (0, 200, 0), "J10": (0, 0, 200)}.items():
        x, y = cal.holes[hole]
        cv2.circle(frame, (round(x), round(y)), radius, bgr, -1)
    reading = camera_glue.reading_from_frame(frame, "test", cal)
    assert reading.colors == {"A40": "green", "A45": "green", "J10": "red"}
