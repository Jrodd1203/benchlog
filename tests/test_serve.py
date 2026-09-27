"""`benchlog serve` options, and the check routes being reachable through the app."""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.project import Project
from benchlog.core.repo import Repo

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from fastapi.testclient import TestClient  # noqa: E402

from benchlog.server.app import app as api  # noqa: E402

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"
runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    project, _ = Project.init(path)
    shutil.copy(EXAMPLES / "working.json", project.circuit_path)
    project.commit("Working circuit")
    monkeypatch.delenv("BENCHLOG_PROJECT", raising=False)
    return project


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda target, **kwargs: calls.update(target=target, **kwargs))
    return calls


def test_defaults(project: Project, started: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.chdir(project.repo.root / "benchlog")  # anywhere inside the project
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert started["host"] == "127.0.0.1" and started["port"] == 8000
    assert started["reload"] is False and started["reload_dirs"] is None
    assert os.environ["BENCHLOG_PROJECT"] == str(project.repo.root)
    assert f"serving {project.repo.root} at http://localhost:8000" in result.output.replace("\n", "")


def test_project_host_and_reload(project: Project, started: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # not inside the project
    result = runner.invoke(
        app, ["serve", "--project", str(project.repo.root), "--host", "0.0.0.0", "--port", "9000", "--reload"]
    )
    assert result.exit_code == 0, result.output
    assert (started["host"], started["port"], started["reload"]) == ("0.0.0.0", 9000, True)
    assert started["reload_dirs"][0].endswith("benchlog")  # watches benchlog's code, not project files
    assert "reachable from other devices" in result.output


def test_not_a_project(tmp_path: Path, started: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["serve", "--workspace", str(tmp_path / "no-projects")])
    assert result.exit_code == 1
    assert "benchlog init" in result.output
    assert not started  # never started a server that can't serve anything


def test_check_routes_are_served(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHLOG_PROJECT", str(project.repo.root))
    client = TestClient(api)
    ran = client.post("/api/checks/run")
    assert ran.status_code == 200, ran.text
    # The working circuit matches HEAD, so the report is saved for HEAD and can be fetched back.
    saved = client.get("/api/checks", params={"commit": "HEAD"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == ran.json()["status"]
    assert client.get("/api/checks", params={"commit": "nope"}).status_code == 400
