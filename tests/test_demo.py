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

    from benchlog.server import deps
    from benchlog.server.app import app as api

    monkeypatch.setenv("BENCHLOG_WORKSPACE", str(workspace))
    monkeypatch.setenv("BENCHLOG_PROJECT", str(workspace / "led-bar"))
    deps.open_project(None)
    yield TestClient(api)
    deps.open_project(None)


def test_list_and_open_projects(client) -> None:
    listed = client.get("/api/projects").json()
    assert {p["id"] for p in listed} == {d.folder for d in DEMOS}
    assert listed[0]["id"] == "led-bar" and listed[0]["open"]  # the open one first
    names = {p["id"]: p["name"] for p in listed}
    assert names["weather-station"] == "Weather station"
    assert all(p["demo"] and p["updated"] and p["board"] == "bb830" for p in listed)

    opened = client.post("/api/projects/pot-led/open").json()
    assert opened["open"] and opened["id"] == "pot-led"
    assert client.get("/api/projects/current").json()["id"] == "pot-led"
    # Every other route now works on pot-led.
    assert client.get("/api/history").json()[0]["commit"]["subject"] == "Add status LED on GPIO13"
    assert [p["title"] for p in client.get("/api/prs").json()] == ["Move sensor to GPIO12"]

    missing = client.post("/api/projects/nope/open")
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
