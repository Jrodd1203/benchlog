import shutil
from pathlib import Path

import pytest

from benchlog.core.board_state import rewire_guide
from benchlog.core.branches import board_report, checkout, create_branch, list_branches
from benchlog.core.models import Circuit, Component, ComponentType, Wire
from benchlog.core.project import Project, ProjectError
from benchlog.core.repo import Repo
from benchlog.core.scan import reading_from_circuit
from benchlog.core.serialize import dump, load_circuit

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"


def load(name: str) -> Circuit:
    return load_circuit(EXAMPLES / f"{name}.json")


def with_w5_at(hole: str) -> Circuit:
    circuit = load("working")
    wires = [w.model_copy(update={"b": hole}) if w.id == "w5" else w for w in circuit.wires]
    return circuit.model_copy(update={"wires": wires})


def commit_circuit(project: Project, circuit: Circuit, message: str) -> str:
    dump(circuit, project.circuit_path)
    return project.commit(message).sha


@pytest.fixture
def project(tmp_path: Path) -> Project:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    project, _ = Project.init(path)
    shutil.copy(EXAMPLES / "working.json", project.circuit_path)
    project.repo.add(path / ".gitignore")
    project.commit("Working circuit")
    repo.run("branch", "-M", "main")
    return project


@pytest.fixture
def feature(project: Project) -> Project:
    """A 'feature' branch that moves w5 to GPIO12 (A12); the board is confirmed on main."""
    project.board_confirmed()
    create_branch(project, "feature")
    project.repo.run("checkout", "--quiet", "feature")
    commit_circuit(project, load("moved-wire"), "Move pot to GPIO12")
    project.repo.run("checkout", "--quiet", "main")
    return project


def test_init_ignores_the_state_folder(project: Project):
    assert ".benchlog/" in (project.repo.root / ".gitignore").read_text().splitlines()


def test_list_and_create_branches(project: Project):
    assert [(b.name, b.current) for b in list_branches(project)] == [("main", True)]
    created = create_branch(project, "try-led")
    assert created.head == project.repo.head() and not created.current
    assert project.repo.current_branch() == "main"  # creating doesn't switch
    with pytest.raises(ProjectError, match="already exists"):
        create_branch(project, "try-led")
    with pytest.raises(ProjectError, match="not a valid branch name"):
        create_branch(project, "bad name")


def test_checkout_switches_the_circuit_and_guides_rewiring(feature: Project):
    report = checkout(feature, "feature")
    assert feature.repo.current_branch() == "feature"
    assert feature.load_circuit() == load("moved-wire")
    assert report.matches is False
    [step] = report.guide
    assert step.text == "Move w5: A4 (GPIO34) → A12 (GPIO12)"
    assert (step.action, step.object_id, step.holes) == ("move", "w5", ["A4", "A12"])


def test_checkout_back_needs_no_rewiring(feature: Project):
    checkout(feature, "feature")
    report = checkout(feature, "main")
    assert report.matches is True and report.guide == []


def test_checkout_refuses_uncommitted_circuit_changes(feature: Project):
    dump(with_w5_at("A6"), feature.circuit_path)
    with pytest.raises(ProjectError, match="uncommitted changes. Commit them first"):
        checkout(feature, "feature")
    assert feature.repo.current_branch() == "main"


def test_checkout_refuses_pending_scan_proposals(feature: Project):
    feature.scan(reading_from_circuit(with_w5_at("A6"), "simulated"))
    assert feature.pending_observations()
    with pytest.raises(ProjectError, match="scan proposal\\(s\\) still pending"):
        checkout(feature, "feature")


def test_scans_after_checkout_measure_against_the_new_circuit(feature: Project):
    feature.scan(reading_from_circuit(load("working"), "simulated"))  # clean scan on main: saves a reference
    checkout(feature, "feature")

    # Board not rewired yet: the scan proposes exactly the rewiring still to do.
    [obs] = feature.scan(reading_from_circuit(load("working"), "simulated"))
    assert (obs.object_id, obs.before["b"], obs.after["b"]) == ("w5", "A12", "A4")
    feature.reject_observations()

    # Rewired: the scan is clean and the board now matches the branch.
    assert feature.scan(reading_from_circuit(load("moved-wire"), "simulated")) == []
    assert feature.board_state.load().matches_commit == feature.repo.head()
    assert board_report(feature).matches is True


def test_board_state_unknown_until_confirmed(project: Project):
    report = board_report(project)
    assert report.matches is None and report.state.matches_commit is None


def test_clean_scan_confirms_the_board(project: Project):
    assert project.scan(reading_from_circuit(load("working"), "simulated")) == []
    assert project.board_state.load().matches_commit == project.repo.head()


def test_commit_after_review_moves_the_board_state(project: Project):
    project.board_confirmed()
    project.scan(reading_from_circuit(with_w5_at("A6"), "simulated"))
    project.accept_observations()
    new = project.commit("w5 to GPIO32").sha
    assert project.board_state.load().matches_commit == new


def test_commit_of_unscanned_edits_forgets_the_board(project: Project):
    project.board_confirmed()
    new = commit_circuit(project, with_w5_at("A6"), "edited by hand")
    assert project.board_state.load().matches_commit != new


def test_rewire_guide_order_and_text():
    before = load("working")
    after = Circuit.model_validate({
        **before.model_dump(),
        "wires": [w.model_dump() for w in before.wires if w.id != "w6"] + [{"id": "w7", "a": "J7", "b": "R-7"}],
        "components": [c.model_dump() for c in before.components]
        + [Component(id="led2", type=ComponentType.LED, pins={"anode": "A40", "cathode": "A42"}).model_dump()],
    })  # fmt: skip
    after.components[[c.id for c in after.components].index("r1")].value = "1kΩ"
    steps = rewire_guide(before, after)
    assert [s.step for s in steps] == [1, 2, 3, 4]
    assert [s.text for s in steps] == [
        "Remove w6 from J18, R-18",
        "Place led2: anode in A40, cathode in A42",
        "Add wire w7: J7 (GPIO18) → R-7",
        "Change r1: value 220Ω → 1kΩ",
    ]


def test_same_strip_move_is_optional():
    [step] = rewire_guide(load("working"), with_w5_at("C4"))
    assert step.optional and step.text.endswith("(same strip, optional)")
