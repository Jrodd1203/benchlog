import json
import shutil
from pathlib import Path

import pytest

from benchlog.core import prs
from benchlog.core.branches import create_branch
from benchlog.core.models import Circuit, Wire
from benchlog.core.project import Project, ProjectError
from benchlog.core.repo import Repo
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


def on(project: Project, branch: str) -> None:
    project.repo.run("checkout", "--quiet", branch)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    """main has the working circuit; 'feature' moves w5 to GPIO12 (checks fail). Checked out: feature."""
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    project, _ = Project.init(path)
    shutil.copy(EXAMPLES / "working.json", project.circuit_path)
    project.repo.add(path / ".gitignore")
    project.commit("Working circuit")
    repo.run("branch", "-M", "main")
    create_branch(project, "feature")
    on(project, "feature")
    commit_circuit(project, load("moved-wire"), "Move pot to GPIO12")
    monkeypatch.chdir(path)
    monkeypatch.setenv("BENCHLOG_PROJECT", str(path))
    return project


def pr_file(project: Project, pr_id: int) -> dict:
    [path] = (project.repo.root / ".benchlog" / "prs").glob(f"{pr_id:04d}-*.json")
    return json.loads(path.read_text())


def test_create_saves_a_pointer_and_checks_the_branch(project: Project):
    pr = prs.create(project, "feature", "main", "Move the pot", "Try GPIO12")
    assert (pr.id, pr.status, pr.from_branch, pr.into_branch) == (1, "open", "feature", "main")
    assert pr.checks.commit == project.repo.head() and pr.checks.overall == "fail"

    data = pr_file(project, 1)
    assert set(data) == {
        "id", "title", "description", "from_branch", "into_branch", "status", "created_at", "updated_at",
        "last_checked_commit", "checks", "tested", "merged",
    }  # fmt: skip
    assert "wires" not in json.dumps(data)  # a pointer, never circuit data
    assert (project.repo.root / ".benchlog" / "prs" / "0001-move-the-pot.json").exists()


def test_into_defaults_to_main(project: Project):
    assert prs.create(project, "feature", None, "Move the pot").into_branch == "main"


def test_create_validation(project: Project):
    with pytest.raises(ProjectError, match="into itself"):
        prs.create(project, "main", "main", "x")
    create_branch(project, "empty")  # at feature's head
    with pytest.raises(ProjectError, match="no commits that aren't already in"):
        prs.create(project, "empty", "feature", "x")
    prs.create(project, "feature", "main", "first")
    with pytest.raises(ProjectError, match="already an open PR"):
        prs.create(project, "feature", "main", "second")


def test_detail_reads_both_circuits_from_git(project: Project):
    pr = prs.create(project, "feature", "main", "Move the pot")
    detail = prs.get(project, pr.id)
    assert detail.before == load("working") and detail.after == load("moved-wire")
    [move] = detail.diff.placement
    assert (move.object_id, move.before["b"], move.after["b"]) == ("w5", "A4", "A12")
    assert "w5 moved: b A4 → A12" in detail.summary
    assert detail.checks.status == "fail"
    assert detail.merge.allowed is False and "GPIO12 held high at boot" in detail.merge.reason


def test_new_commit_shows_up_and_reruns_checks(project: Project):
    pr = prs.create(project, "feature", "main", "Move the pot")
    old_head = pr.checks.commit
    new_head = commit_circuit(project, with_w5_at("A6"), "Use GPIO32 instead")  # a safe pin

    detail = prs.get(project, pr.id)
    assert detail.after == with_w5_at("A6")  # nothing about the circuit was stored in the PR
    assert detail.checks.commit == new_head != old_head
    assert detail.checks.status == "pass"
    assert detail.pr.last_checked_commit == new_head and detail.pr.checks.overall == "pass"
    assert detail.merge.reason == "no checks failed and the branch can be fast-forwarded"
    assert detail.merge.allowed is True


def test_tested_resets_on_a_new_commit(project: Project):
    pr = prs.create(project, "feature", "main", "Move the pot")
    tested = prs.mark_tested(project, pr.id, "pot reads 0-3.3 V")
    assert tested.tested.done and tested.tested.commit == project.repo.head()
    assert prs.get(project, pr.id).pr.tested.done is True

    commit_circuit(project, with_w5_at("A6"), "Use GPIO32 instead")
    detail = prs.get(project, pr.id)
    assert detail.pr.tested.done is False and detail.pr.tested.note == "pot reads 0-3.3 V"
    assert pr_file(project, pr.id)["tested"]["done"] is False


def test_merge_blocked_when_checks_fail(project: Project):
    pr = prs.create(project, "feature", "main", "Move the pot")
    with pytest.raises(ProjectError, match="checks failed"):
        prs.merge(project, pr.id)
    assert prs.get(project, pr.id).pr.status == "open"


def test_merge_fast_forwards_main(project: Project):
    head = commit_circuit(project, with_w5_at("A6"), "Use GPIO32 instead")
    pr = prs.create(project, "feature", "main", "Pot on GPIO32")
    detail = prs.merge(project, pr.id)
    assert detail.pr.status == "merged"
    assert project.repo.current_branch() == "main"
    assert project.repo.head() == head  # fast-forward: no merge commit
    # The merged PR still shows what it changed.
    assert detail.before == load("working") and detail.after == with_w5_at("A6")
    assert detail.merge.allowed is False and detail.merge.reason == "this PR is merged"


def with_wire_on_gpio2() -> Circuit:
    """A wire on strapping pin GPIO2 (I12's row): a needs_confirmation warning, not a failure."""
    circuit = load("working")
    return circuit.model_copy(update={"wires": [*circuit.wires, Wire(id="w7", a="J12", b="J40")]})


def test_warnings_dont_block_merge_but_are_listed(project: Project):
    head = commit_circuit(project, with_wire_on_gpio2(), "Use GPIO2 for a button")
    pr = prs.create(project, "feature", "main", "Button on GPIO2")
    detail = prs.get(project, pr.id)
    assert detail.checks.status == "needs_confirmation"
    assert detail.merge.allowed is True
    assert detail.merge.reason.endswith("; 1 warning to confirm first")
    [warning] = detail.merge.warnings
    assert warning.startswith("GPIO2 is a strapping pin")

    merged = prs.merge(project, pr.id)
    assert merged.pr.status == "merged" and project.repo.head() == head
    assert merged.merge.warnings == [warning]  # still shown after the merge


def test_not_supported_is_listed_never_a_pass(project: Project):
    commit_circuit(project, with_w5_at("A6"), "Use GPIO32 instead")
    detail = prs.get(project, prs.create(project, "feature", "main", "Pot on GPIO32").id)
    assert detail.merge.allowed is True and detail.merge.warnings == []
    assert detail.merge.not_checked == ["No serial data recorded. Connect the ESP32 and run the checks from the web app."]
    serial = next(r for r in detail.checks.results if r.check == "serial_conflicts")
    assert serial.status == "not_supported"


def test_fail_blocks_even_with_warnings(project: Project):
    circuit = with_wire_on_gpio2()
    circuit = circuit.model_copy(update={"wires": [w.model_copy(update={"b": "A12"}) if w.id == "w5" else w for w in circuit.wires]})
    commit_circuit(project, circuit, "GPIO2 button and pot on GPIO12")
    detail = prs.get(project, prs.create(project, "feature", "main", "Both").id)
    assert detail.merge.allowed is False and detail.merge.reason.startswith("checks failed: GPIO12 held high")
    assert len(detail.merge.warnings) == 1


def test_merge_moves_the_board_state_when_the_board_matched_the_branch(project: Project):
    head = commit_circuit(project, with_w5_at("A6"), "Use GPIO32 instead")
    project.board_confirmed()
    pr = prs.create(project, "feature", "main", "Pot on GPIO32")
    prs.merge(project, pr.id)
    assert project.board_state.load().matches_commit == head == project.repo.head()


def test_merge_blocked_when_main_moved(project: Project):
    commit_circuit(project, with_w5_at("A6"), "Use GPIO32 instead")
    pr = prs.create(project, "feature", "main", "Pot on GPIO32")
    on(project, "main")
    commit_circuit(project, with_w5_at("A7"), "Someone else changed main")
    on(project, "feature")

    detail = prs.get(project, pr.id)
    assert detail.merge.allowed is False
    assert detail.merge.reason == "main changed since this branch was created. Update your branch first."
    with pytest.raises(ProjectError, match="main changed since this branch was created"):
        prs.merge(project, pr.id)


def test_merge_refuses_uncommitted_changes(project: Project):
    commit_circuit(project, with_w5_at("A6"), "Use GPIO32 instead")
    pr = prs.create(project, "feature", "main", "Pot on GPIO32")
    dump(with_w5_at("A7"), project.circuit_path)
    with pytest.raises(ProjectError, match="uncommitted changes"):
        prs.merge(project, pr.id)


def test_close_returns_the_way_back(project: Project):
    project.board_confirmed()  # the board is wired like the feature branch
    pr = prs.create(project, "feature", "main", "Move the pot")
    result = prs.close(project, pr.id)
    assert result.pr.status == "closed"
    assert [s.text for s in result.guide] == ["Move w5: A12 (GPIO12) → A4 (GPIO34)"]
    with pytest.raises(ProjectError, match="already closed"):
        prs.close(project, pr.id)
    with pytest.raises(ProjectError, match="is closed"):
        prs.mark_tested(project, pr.id)


def test_list(project: Project):
    prs.create(project, "feature", "main", "Move the pot")
    assert [(p.id, p.title) for p in prs.list_prs(project)] == [(1, "Move the pot")]


# ── API ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from benchlog.server.app import app

    return TestClient(app)


def test_api_branches_and_board(project: Project, client):
    branches = client.get("/api/branches").json()
    assert [(b["name"], b["current"]) for b in branches] == [("feature", True), ("main", False)]
    assert client.post("/api/branches", json={"name": "try"}).json()["name"] == "try"
    assert client.post("/api/branches", json={"name": "try"}).status_code == 400
    assert client.get("/api/board-state").json()["matches"] is None

    project.board_confirmed()
    report = client.post("/api/checkout", json={"branch": "main"}).json()
    assert report["branch"] == "main" and report["guide"][0]["text"] == "Move w5: A12 (GPIO12) → A4 (GPIO34)"


def test_api_pr_flow(project: Project, client):
    created = client.post("/api/prs", json={"title": "Move the pot"}).json()
    assert created["pr"]["from_branch"] == "feature" and created["merge"]["allowed"] is False
    assert set(created) == {"pr", "before", "after", "diff", "summary", "checks", "merge"}

    commit_circuit(project, with_w5_at("A6"), "Use GPIO32 instead")
    detail = client.get("/api/prs/1").json()
    assert detail["checks"]["status"] == "pass" and detail["merge"]["allowed"] is True

    assert client.post("/api/prs/1/tested", json={"note": "works"}).json()["tested"]["done"] is True
    merged = client.post("/api/prs/1/merge").json()
    assert merged["pr"]["status"] == "merged"
    assert client.post("/api/prs/1/merge").status_code == 400
    assert [p["status"] for p in client.get("/api/prs").json()] == ["merged"]
    assert client.get("/api/prs/9").status_code == 404
