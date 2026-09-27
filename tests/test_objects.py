"""Outline-and-measure: new objects on the board, measured in millimetres."""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from typer.testing import CliRunner  # noqa: E402

from benchlog.cli.main import app  # noqa: E402
from benchlog.core.project import Project  # noqa: E402
from benchlog.core.repo import Repo  # noqa: E402
from benchlog.vision import calibration as cm  # noqa: E402
from benchlog.vision.objects import find_objects  # noqa: E402

from test_calibration import DESK, moved  # noqa: E402


@pytest.fixture(scope="module")
def cal() -> cm.Calibration:
    editor = cm.CalibrationEditor()
    editor.update(DESK, now=0.0)
    return cm.Calibration(corners=editor.corners, holes=editor.holes, reference=DESK.copy())


def parts(cal: cm.Calibration) -> np.ndarray:
    """The empty board with parts drawn at their datasheet sizes."""
    holes = cal.holes
    px = cm.hole_pitch_px(holes) / 2.54  # pixels per mm
    frame = DESK.copy()

    def at(name: str) -> tuple[int, int]:
        return tuple(round(v) for v in holes[name])

    # A green jumper lying from A40 to A50, 1.5 mm thick.
    cv2.line(frame, at("A40"), at("A50"), (40, 170, 40), max(1, round(1.5 * px)))
    # A beige 6 x 2.3 mm resistor between C20 and C24, legs bent into the holes.
    cv2.line(frame, at("C20"), at("C24"), (150, 150, 150), max(1, round(0.6 * px)))
    centre = np.mean([holes["C20"], holes["C24"]], axis=0)
    cv2.fillPoly(frame, [cv2.boxPoints((tuple(centre), (6 * px, 2.3 * px), 0)).astype(np.int32)], (120, 180, 210))
    # A red 5 mm LED dome over H30/I30.
    centre = np.mean([holes["H30"], holes["I30"]], axis=0)
    cv2.circle(frame, (round(centre[0]), round(centre[1])), round(2.5 * px), (40, 40, 200), -1)
    # A black TO-92 transistor (~4.5 x 3.5 mm) over D10-D12.
    cv2.fillPoly(frame, [cv2.boxPoints((holes["D11"], (4.5 * px, 3.5 * px), 0)).astype(np.int32)], (25, 25, 25))
    return frame


def by_color(objects) -> dict:
    return {o.color: o for o in objects}


def test_empty_board_has_no_objects(cal: cm.Calibration) -> None:
    frame, _ = moved(DESK, 1.0, 12, -7)
    assert find_objects(cal, frame, cm.track(cal, frame)) == []


def test_measures_parts_in_millimetres(cal: cm.Calibration) -> None:
    frame = parts(cal)
    objects = find_objects(cal, frame, cm.track(cal, frame))
    assert len(objects) == 4
    found = by_color(objects)

    wire = found["green"]
    assert wire.length_mm == pytest.approx(25.4, abs=2.5) and wire.width_mm < 2.6
    assert wire.ends == ("A40", "A50") and wire.fill > 0.85 and wire.aspect > 8

    led = found["red"]
    assert led.length_mm == pytest.approx(5, abs=0.8) and led.width_mm == pytest.approx(5, abs=0.8)
    assert led.roundness > 0.85 and set(led.holes) == {"H30", "I30"}

    transistor = found["black"]
    assert transistor.length_mm == pytest.approx(4.5, abs=0.8) and transistor.width_mm == pytest.approx(3.5, abs=0.8)
    assert "D11" in transistor.holes and transistor.fill > 0.9

    resistor = found["orange"]  # beige reads as a pale orange
    assert resistor.ends == ("C20", "C24")
    assert resistor.width_mm == pytest.approx(2.3, abs=0.6)
    assert resistor.fill < 0.8  # a fat body with thin legs, unlike a uniform wire
    assert resistor.saturation < wire.saturation


def test_measurements_survive_a_bumped_board(cal: cm.Calibration) -> None:
    frame, _ = moved(parts(cal), 1.5, 20, -10)
    found = by_color(find_objects(cal, frame, cm.track(cal, frame)))
    assert found["red"].length_mm == pytest.approx(5, abs=0.8)
    assert found["green"].ends == ("A40", "A50")


def test_scan_debug_lists_objects(cal: cm.Calibration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    project, _ = Project.init(path)
    cal.save(project.calibration_dir)
    photo = tmp_path / "parts.png"
    cv2.imwrite(str(photo), parts(cal))
    result = CliRunner(env={"COLUMNS": "300"}).invoke(app, ["scan", "--image", str(photo), "--debug", "--no-serial"])
    assert result.exit_code == 0, result.output
    assert "objects since calibration: 4" in result.output
    assert "ends A40-A50" in result.output
    assert (project.scans.dir / "debug" / "objects.png").exists()
