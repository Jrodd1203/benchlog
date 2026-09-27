"""benchlog push: publishing commits to a remote (a local bare repo stands in for GitHub)."""

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.remote import github_slug, site_link
from benchlog.core.repo import Repo

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
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    repo = Repo.init(tmp_path / "project")
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    repo.run("checkout", "-q", "-b", "main")
    monkeypatch.chdir(repo.root)
    monkeypatch.delenv("BENCHLOG_SITE", raising=False)
    run("init")
    return repo


@pytest.fixture(autouse=True)
def gh_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake gh on PATH (tests never reach GitHub): logs each call; `repo create` adds $FAKE_REMOTE as origin."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh-calls"
    gh = bin_dir / "gh"
    gh.write_text(
        f'#!/bin/sh\necho "$@" >> "{log}"\n'
        'if [ "$1 $2" = "repo create" ]; then git remote add origin "$FAKE_REMOTE"; fi\n'
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    path.mkdir()
    Repo(path).run("init", "--quiet", "--bare")
    return path


def commit_something(repo: Repo) -> None:
    run("commit", "-m", "start the circuit")


def remote_head(remote: Path, branch: str = "main") -> str:
    return Repo(remote).run("rev-parse", branch).strip()


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("https://github.com/you/my-circuit", "you/my-circuit"),
        ("https://github.com/you/my-circuit.git", "you/my-circuit"),
        ("git@github.com:you/my.circuit.git", "you/my.circuit"),
        ("ssh://git@github.com/you/my-circuit/", "you/my-circuit"),
        ("https://gitlab.com/you/my-circuit", None),
        ("/tmp/remote.git", None),
    ],
)
def test_github_slug(url: str, slug: str | None) -> None:
    assert github_slug(url) == slug


def test_site_link() -> None:
    assert site_link("you/c", "https://bench.app/") == "https://bench.app/?repo=you/c"
    assert site_link("you/c", None) is None


def test_nothing_committed_yet(project: Repo, remote: Path) -> None:
    assert "commit the circuit first" in fail("push", str(remote))


def test_not_connected_explains_how(project: Repo) -> None:
    commit_something(project)
    out = fail("push")
    assert "benchlog push <its URL>" in out and "--create NAME" in out


def test_first_push_connects_then_plain_push(project: Repo, remote: Path) -> None:
    commit_something(project)
    out = run("push", str(remote))
    assert "pushed main" in out
    assert project.remote_url() == str(remote)
    assert remote_head(remote) == project.head()

    (project.root / "benchlog" / "circuit.json").write_text(
        (project.root / "benchlog" / "circuit.json").read_text().replace('"wires": []', '"wires": [{"id": "w1", "a": "A1", "b": "A5"}]')
    )
    run("commit", "-m", "add a wire")
    run("push")
    assert remote_head(remote) == project.head()
    # The same URL again is fine; a different one isn't.
    run("push", str(remote))
    assert "already connected" in fail("push", "https://github.com/someone/else")


def test_uncommitted_changes_are_mentioned(project: Repo, remote: Path) -> None:
    commit_something(project)
    circuit = project.root / "benchlog" / "circuit.json"
    circuit.write_text(circuit.read_text().replace('"wires": []', '"wires": [{"id": "w1", "a": "A1", "b": "A5"}]'))
    assert "only commits are pushed" in run("push", str(remote))


def test_github_remote_prints_the_site_link_and_is_tagged(project: Repo, remote: Path, gh_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commit_something(project)
    # Rewrite the GitHub URL to the local stand-in, so the remote reads as GitHub but pushes locally.
    project.run("config", f"url.{remote}.insteadOf", "https://github.com/you/my-circuit")
    out = run("push", "https://github.com/you/my-circuit")
    assert "Explore → you/my-circuit" in out and "tagged benchlog-circuit" in out
    assert gh_log.read_text() == "repo edit you/my-circuit --add-topic benchlog-circuit\n"
    monkeypatch.setenv("BENCHLOG_SITE", "https://bench.app")
    assert "https://bench.app/?repo=you/my-circuit" in run("push")
    assert gh_log.read_text().count("\n") == 1  # tagged once, when connecting


def test_without_gh_says_how_to_tag(project: Repo, remote: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commit_something(project)
    project.run("config", f"url.{remote}.insteadOf", "https://github.com/you/my-circuit")
    monkeypatch.setattr("benchlog.core.remote.shutil.which", lambda _: None)
    assert "add the topic benchlog-circuit" in run("push", "https://github.com/you/my-circuit")


def test_create_without_gh(project: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    commit_something(project)
    monkeypatch.setattr("benchlog.core.remote.shutil.which", lambda _: None)
    assert "GitHub CLI" in fail("push", "--create", "my-circuit")


def test_create_with_gh(project: Repo, remote: Path, gh_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commit_something(project)
    monkeypatch.setenv("FAKE_REMOTE", str(remote))
    run("push", "--create", "my-circuit")
    assert gh_log.read_text().startswith("repo create my-circuit --public")
    assert remote_head(remote) == project.head()
    assert "already connected" in fail("push", "--create", "another")
