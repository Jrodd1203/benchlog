import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.models import ObservationStatus
from benchlog.core.project import Project, ProjectError
from benchlog.core.repo import Repo
from benchlog.core.scan import reading_from_circuit, scan
from benchlog.core.serialize import load_circuit

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"
runner = CliRunner(env={"COLUMNS": "200"})


def run(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def fail(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code != 0, result.output
    return result.output


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    run("init")
    shutil.copy(EXAMPLES / "working.json", path / "benchlog" / "circuit.json")
    run("commit", "-m", "Working circuit")
    return Project.find()


def simulate(circuit_json: Path) -> str:
    return run("scan", "--simulate", str(circuit_json))


def test_full_demo_loop(project: Project) -> None:
    simulate(EXAMPLES / "moved-wire.json")
    assert "obs1  moved wire w5: b A4 → A12" in run("review")

    out = run("review", "accept", "obs1")
    assert "accepted obs1" in out
    assert "+ esp32.GPIO12 connected to pot1.wiper" in out
    assert project.load_circuit() == load_circuit(EXAMPLES / "moved-wire.json")
    assert "nothing to review" in run("review")

    run("commit", "-m", "Move sensor to GPIO 12")
    assert "matches HEAD" in run("status")
    # The reviewed scan is now the reference: the same board shows nothing new.
    assert "no changes seen" in simulate(EXAMPLES / "moved-wire.json")


def test_reject_leaves_circuit_alone(project: Project) -> None:
    before = project.load_circuit()
    simulate(EXAMPLES / "moved-wire.json")
    assert "rejected obs1" in run("review", "reject", "--all")
    assert project.load_circuit() == before
    assert [o.status for o in project.observations()] == [ObservationStatus.REJECTED]


def test_accept_needs_ids_or_all(project: Project) -> None:
    simulate(EXAMPLES / "moved-wire.json")
    assert "--all" in fail("review", "accept")
    assert "--all" in fail("review", "accept", "obs1", "--all")
    assert "no pending observation obs9" in fail("review", "accept", "obs9")


def test_half_wire_must_be_finished_before_accepting(project: Project) -> None:
    # The camera sees one new hole (A40) and nothing else: a wire whose other end is hidden.
    before = project.load_circuit()
    reading = reading_from_circuit(before, "sim")
    reading.occupied.append("A40")
    project.save_observations(scan(before, project.scans, reading, pending=[]))
    [half] = project.pending_observations()
    assert half.after == {"a": "A40"} and half.uncertain_holes == ["A40"]

    assert "other end" in fail("review", "accept", half.id)
    assert project.load_circuit() == before  # all or nothing

    edited = run("review", "edit", half.id, "b=j45")
    assert "A40, J45" in edited and "check" not in edited
    run("review", "accept", half.id)
    new_wire = next(w for w in project.load_circuit().wires if w.id == half.object_id)
    assert (new_wire.a, new_wire.b) == ("A40", "J45")


def test_edit_validation(project: Project) -> None:
    simulate(EXAMPLES / "moved-wire.json")
    assert "not a hole" in fail("review", "edit", "obs1", "b=Z99")
    assert "ends are 'a' and 'b'" in fail("review", "edit", "obs1", "c=A5")
    assert "END=HOLE" in fail("review", "edit", "obs1", "A5")
    # Correcting the camera's guess: the end actually went to A13.
    run("review", "edit", "obs1", "b=A13")
    run("review", "accept", "--all")
    assert next(w for w in project.load_circuit().wires if w.id == "w5").b == "A13"


def test_accept_unknown_id_raises(project: Project) -> None:
    project.save_observations([])
    with pytest.raises(ProjectError, match="no pending observation"):
        project.accept_observations(["obs1"])
