"""Demo projects (`benchlog demo seed`) and browsing/switching projects through the API."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core import prs
from benchlog.core.project import Project
from benchlog.demo import DEMOS, is_demo, seed

runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("projects")
    assert [what for _, _, what in seed(path)] == ["created"] * len(DEMOS)
    return path


def pr_state(project: Project) -> tuple[str, bool]:
    [pr] = prs.list_prs(project)
    return pr.status, prs.get(project, pr.id).merge.allowed


def test_projects_are_real_clean_repos(workspace: Path) -> None:
    for demo in DEMOS:
        project = Project.find(workspace / demo.folder)
        assert is_demo(project.repo.root)
        assert project.repo.current_branch() == "main"
        assert not project.repo.is_dirty()
        assert len(project.history()) >= 3
        assert (project.repo.root / "README.md").read_text().startswith(f"# {demo.name}")


def test_each_project_shows_a_different_pr_story(workspace: Path) -> None:
    pot = Project.find(workspace / "pot-led")
    assert pr_state(pot) == ("open", False)  # blocked: GPIO12 is a strapping pin
    [pr] = prs.list_prs(pot)
    assert "GPIO12" in prs.get(pot, pr.id).merge.reason

    weather = Project.find(workspace / "weather-station")
    [merged] = prs.list_prs(weather)
    assert merged.status == "merged" and merged.tested.done
    assert any(w.label == "SDA to GPIO21" for w in weather.load_circuit().wires)  # merged into main

    assert pr_state(Project.find(workspace / "led-bar")) == ("open", True)  # ready to merge


def test_history_is_spread_over_days(workspace: Path) -> None:
    dates = {c.date[:10] for c in Project.find(workspace / "pot-led").repo.log()}
    assert len(dates) >= 3


def test_reseeding_leaves_projects_alone_unless_reset(workspace: Path, tmp_path: Path) -> None:
    assert {what for _, _, what in seed(workspace)} == {"exists (use --reset to rebuild)"}
    mine = tmp_path / "pot-led"
    mine.mkdir()
    (mine / "notes.txt").write_text("not a demo")
    [(_, _, what), *_] = seed(tmp_path, reset=True)
    assert what == "exists and isn't a demo project, left alone"
    assert (mine / "notes.txt").exists()


def test_seed_command(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "seed", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert result.output.count("created") == len(DEMOS)
    again = runner.invoke(app, ["demo", "seed", "--dir", str(tmp_path), "--reset"])
    assert again.output.count("created") == len(DEMOS)


# ── API ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from benchlog.server import app as app_module
    from benchlog.server import deps

    monkeypatch.setattr(deps, "PROJECTS_ROOT", workspace)
    monkeypatch.setattr(app_module, "PROJECTS_ROOT", workspace)
    monkeypatch.setenv("BENCHLOG_PROJECT", str(workspace / "led-bar"))
    return TestClient(app_module.app)


def test_seeded_projects_are_listed(client) -> None:
    listed = {p["id"]: p for p in client.get("/api/projects").json()}
    assert set(listed) == {d.folder for d in DEMOS}
    assert listed["weather-station"]["name"] == "Weather station"
    assert all(p["updated"] and p["board"] == "bb830" for p in listed.values())


def test_every_route_follows_the_selected_project(client) -> None:
    # Without ?project= the routes use the project serve started with (led-bar)...
    assert [p["title"] for p in client.get("/api/prs").json()] == ["Add a third LED"]
    # ...and with it, every route (including branches and PRs) uses the selected one.
    assert [p["title"] for p in client.get("/api/prs", params={"project": "pot-led"}).json()] == ["Move sensor to GPIO12"]
    branches = client.get("/api/branches", params={"project": "pot-led"}).json()
    assert {b["name"] for b in branches} == {"main", "move-sensor"}
    history = client.get("/api/history", params={"project": "pot-led"}).json()
    assert history[0]["commit"]["subject"] == "Add status LED on GPIO13"
    missing = client.get("/api/prs", params={"project": "nope"})
    assert missing.status_code == 400 and "nope" in missing.json()["detail"]


def test_serve_opens_a_workspace_project_from_anywhere(workspace: Path, tmp_path: Path, monkeypatch) -> None:
    uvicorn = pytest.importorskip("uvicorn")
    started = {}
    monkeypatch.setattr(uvicorn, "run", lambda target, **kw: started.update(kw))
    monkeypatch.chdir(tmp_path)  # not inside any project
    result = runner.invoke(app, ["serve", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "led-bar" in result.output.replace("\n", "")  # first project alphabetically
    assert started["port"] == 8000
