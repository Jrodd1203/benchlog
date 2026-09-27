"""Hole numbers as printed on boards that start counting at 0."""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.numbering import from_printed, to_printed
from benchlog.core.project import Project
from benchlog.core.repo import Repo

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"
runner = CliRunner(env={"COLUMNS": "200"})


def test_translation() -> None:
    assert to_printed("w5 moved: b A4 → A12, R+15 unchanged", 0) == "w5 moved: b A3 → A11, R+15 unchanged"
    assert to_printed("esp32.GPIO12 connected to pot1.wiper; ESP32 DevKit V1; 0x76; J63", 0) == (
        "esp32.GPIO12 connected to pot1.wiper; ESP32 DevKit V1; 0x76; J62"
    )
    assert to_printed("A4", 1) == "A4"
    assert from_printed("g30", 0) == "G31" and from_printed("G30", 1) == "G30"
    assert from_printed("R+5", 0) == "R+5"  # rails aren't numbered on boards


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    project, _ = Project.init(path)
    shutil.copy(EXAMPLES / "working.json", project.circuit_path)
    project.commit("Working circuit")
    return project


def run(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_cli_shows_and_accepts_printed_numbers(project: Project) -> None:
    run("camera", "numbering", "0")
    out = run("scan", "--simulate", str(EXAMPLES / "moved-wire.json"), "--no-serial")
    assert "w5: b A3 → A11" in out  # internally A4 → A12
    # Correct the end, typing the number printed on the board.
    assert "b A3 → A12" in run("review", "edit", "obs1", "b=A12")
    run("review", "accept", "obs1")
    wire = next(w for w in project.load_circuit().wires if w.id == "w5")
    assert wire.b == "A13"  # stored internally
    run("camera", "numbering", "1")
    assert "A13" in run("diff")


def test_numbering_validation(project: Project) -> None:
    result = runner.invoke(app, ["camera", "numbering", "5"])
    assert result.exit_code == 1 and "numbered 1" in result.output


def test_api_reports_numbering(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from benchlog.server.app import app as api

    monkeypatch.setenv("BENCHLOG_PROJECT", str(project.repo.root))
    project.set_config("first_row", 0)
    assert TestClient(api).get("/api/numbering").json() == {"first_row": 0}


def test_calibration_keeps_numbering(tmp_path: Path) -> None:
    cv2 = pytest.importorskip("cv2")
    from benchlog.vision import calibration as cm

    from test_calibration import DESK

    editor = cm.CalibrationEditor()
    editor.update(DESK, now=0.0)
    editor.on_key(ord("n"), DESK, 0.1)
    assert editor.first_row == 0
    cal = cm.Calibration(corners=editor.corners, holes=editor.named_holes(), reference=DESK, first_row=editor.first_row)
    cal.save(tmp_path)
    assert cm.Calibration.load(tmp_path).first_row == 0
    assert cv2 is not None
