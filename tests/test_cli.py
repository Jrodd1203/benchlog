import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.models import Observation
from benchlog.core.project import Project
from benchlog.core.repo import Repo

EXAMPLES = Path(__file__).parent.parent / "examples"
runner = CliRunner(env={"COLUMNS": "200"})


def run(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def fail(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 1, result.output
    return result.output


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    return path


def use_example(project_dir: Path, name: str) -> None:
    shutil.copy(EXAMPLES / "circuits" / f"{name}.json", project_dir / "benchlog" / "circuit.json")


def test_commands_outside_project_fail(project_dir: Path) -> None:
    assert "benchlog init" in fail("status")


def test_init_is_idempotent(project_dir: Path) -> None:
    out = run("init")
    assert "benchlog/circuit.json" in out
    assert (project_dir / ".gitignore").read_text() == ".benchlog/\n"
    assert "already initialized" in run("init")
    assert (project_dir / ".gitignore").read_text() == ".benchlog/\n"


def test_init_creates_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert "git repository" in run("init")
    assert (tmp_path / ".git").exists()


def test_demo_workflow(project_dir: Path) -> None:
    run("init")
    assert "not committed yet" in run("status")

    use_example(project_dir, "working")
    run("commit", "-m", "Working circuit")
    assert "matches HEAD" in run("status")
    assert "no circuit changes" in run("diff")
    assert "nothing to commit" in fail("commit", "-m", "again")

    use_example(project_dir, "moved-wire")
    status = run("status")
    assert "2 connection changes, 1 moved" in status
    out = run("diff")
    assert "+ esp32.GPIO12 connected to pot1.wiper" in out
    assert "- esp32.GPIO34 disconnected from pot1.wiper" in out
    assert "~ w5 moved: b A4 → A12" in out

    run("commit", "-m", "Move sensor to GPIO 12")
    log = run("log")
    assert "Move sensor to GPIO 12" in log and "Working circuit" in log
    assert "2 connection changes, 1 moved" in log
    assert "Working circuit" not in run("log", "-n", "1")

    assert "w5 moved: b A4 → A12" in run("diff", "HEAD~1", "HEAD")
    assert "unknown revision" in fail("diff", "nope")


def test_commit_includes_firmware(project_dir: Path) -> None:
    run("init")
    (project_dir / "firmware").mkdir()
    (project_dir / "firmware" / "main.ino").write_text("void setup() {}\n")
    run("commit", "-m", "Firmware", "-f", "firmware")
    repo = Repo(project_dir)
    assert repo.show("HEAD", Path("firmware/main.ino")) == "void setup() {}\n"


def test_pending_observations_are_reported(project_dir: Path) -> None:
    run("init")
    obs = Observation.model_validate_json((EXAMPLES / "observations" / "moved-wire.json").read_text())
    Project.find().save_observations([obs])
    assert "1 pending review" in run("status")
    assert "still pending" in run("commit", "-m", "Initial")
