import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.models import Circuit, ObservationStatus
from benchlog.core.project import Project
from benchlog.core.repo import Repo
from benchlog.core.scan import BoardReading, MisreadError, ScanStore, changes_between, reading_from_circuit, scan
from benchlog.core.serialize import load_circuit

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"
runner = CliRunner(env={"COLUMNS": "200"})


def load(name: str) -> Circuit:
    return load_circuit(EXAMPLES / f"{name}.json")


def run(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


# ── core ──────────────────────────────────────────────────────────────────────


def test_changes_between_readings() -> None:
    ref = BoardReading(occupied=["A1", "A4"], source="ref")
    cur = BoardReading(occupied=["A1", "A12"], colors={"A12": "yellow"}, confidence=0.6, source="cur")
    assert [(c.hole, c.change, c.color, c.confidence) for c in changes_between(ref, cur)] == [
        ("A4", "emptied", None, 0.6),
        ("A12", "filled", "yellow", 0.6),
    ]


def test_first_scan_uses_circuit_when_no_baseline(tmp_path: Path) -> None:
    store = ScanStore(tmp_path)
    working = load("working")
    [obs] = scan(working, store, reading_from_circuit(load("moved-wire"), "sim"), pending=[])
    assert (obs.object_id, obs.after) == ("w5", {"a": "F21", "b": "A12"})


def test_occlusions_cancel_against_baseline(tmp_path: Path) -> None:
    # The baseline says C5 looks occupied (e.g. hidden under a part); it keeps looking occupied.
    store = ScanStore(tmp_path)
    store.baseline_path.parent.mkdir(parents=True)
    store.baseline_path.write_text(json.dumps({"occupied": ["C5"]}))
    reading = BoardReading(occupied=["C5", "A40", "A45"], source="cam")
    [obs] = scan(Circuit(), store, reading, pending=[])
    assert (obs.kind.value, obs.after) == ("added", {"a": "A40", "b": "A45"})


def test_rescan_before_review_keeps_the_same_reference(tmp_path: Path) -> None:
    store = ScanStore(tmp_path)
    working = load("working")
    first = scan(working, store, reading_from_circuit(load("moved-wire"), "sim"), pending=[])
    # Scanning again with the first scan still unreviewed must still report the move.
    again = scan(working, store, reading_from_circuit(load("moved-wire"), "sim"), pending=first)
    assert [o.object_id for o in again] == ["w5"]


def test_reviewed_scan_becomes_the_reference(tmp_path: Path) -> None:
    store = ScanStore(tmp_path)
    working, moved = load("working"), load("moved-wire")
    scan(working, store, reading_from_circuit(moved, "sim"), pending=[])
    # After review (nothing pending), the same board shows no new changes.
    assert scan(moved, store, reading_from_circuit(moved, "sim"), pending=[]) == []


def test_misread_is_rejected_and_not_saved(tmp_path: Path) -> None:
    store = ScanStore(tmp_path)
    junk = BoardReading(occupied=[f"A{r}" for r in range(20, 64)] + [f"J{r}" for r in range(20, 64)], source="cam")
    with pytest.raises(MisreadError):
        scan(Circuit(), store, junk, pending=[])
    assert store.latest() is None


# ── CLI ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    run("init")
    shutil.copy(EXAMPLES / "working.json", path / "benchlog" / "circuit.json")
    run("commit", "-m", "Working circuit")
    return path


def test_scan_simulated_demo(project_dir: Path) -> None:
    out = run("scan", "--simulate", str(EXAMPLES / "moved-wire.json"))
    assert "1 change to review" in out
    assert "obs1  moved wire w5: b A4 → A12  (confidence 1.00)" in out

    [obs] = Project.find().observations()
    assert obs.status == ObservationStatus.PENDING
    assert "1 pending review" in run("status")


def test_scan_with_no_changes(project_dir: Path) -> None:
    assert "no changes seen" in run("scan", "--simulate", str(EXAMPLES / "working.json"))


def test_scan_sync_records_reference(project_dir: Path) -> None:
    run("scan", "--simulate", str(EXAMPLES / "moved-wire.json"))
    assert "matching the circuit" in run("scan", "--sync", "--simulate", str(EXAMPLES / "working.json"))
    assert Project.find().observations() == []
    assert "no changes seen" in run("scan", "--simulate", str(EXAMPLES / "working.json"))
