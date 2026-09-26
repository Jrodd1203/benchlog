from pathlib import Path

import pytest

from benchlog.core.repo import GitError, Repo


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = Repo.init(tmp_path / "project")
    r.run("config", "user.name", "Test")
    r.run("config", "user.email", "test@example.com")
    return r


def write(repo: Repo, rel: str, text: str) -> Path:
    path = repo.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_empty_repo(repo: Repo) -> None:
    assert repo.head() is None
    assert repo.log() == []
    assert not repo.is_dirty()


def test_find_from_subdirectory(repo: Repo) -> None:
    sub = repo.root / "a" / "b"
    sub.mkdir(parents=True)
    assert Repo.find(sub).root == repo.root


def test_find_outside_repo_raises(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        Repo.find(tmp_path)


def test_commit_log_and_show(repo: Repo) -> None:
    circuit = write(repo, "benchlog/circuit.json", "v1\n")
    repo.add(circuit)
    first = repo.commit("Add circuit | with odd chars\n\nand a body")
    assert first.subject == "Add circuit | with odd chars"
    assert repo.head() == first.sha

    circuit.write_text("v2\n")
    assert repo.is_dirty(circuit)
    repo.add(circuit)
    second = repo.commit("Move wire")

    write(repo, "firmware/main.ino", "x\n")
    repo.add(Path("firmware/main.ino"))
    repo.commit("Firmware only")

    assert [c.subject for c in repo.log()] == ["Firmware only", "Move wire", "Add circuit | with odd chars"]
    assert [c.sha for c in repo.log(path=circuit)] == [second.sha, first.sha]
    assert [c.subject for c in repo.log(limit=1)] == ["Firmware only"]
    assert repo.show(first.sha, circuit) == "v1\n"
    assert repo.show("HEAD", Path("benchlog/circuit.json")) == "v2\n"
    assert repo.show(first.sha, Path("firmware/main.ino")) is None
    assert not repo.is_dirty()


def test_commit_with_nothing_staged_raises(repo: Repo) -> None:
    write(repo, "a.txt", "a\n")
    repo.add(Path("a.txt"))
    repo.commit("first")
    with pytest.raises(GitError):
        repo.commit("empty")


def test_untracked_file_is_dirty(repo: Repo) -> None:
    write(repo, "new.txt", "x\n")
    assert repo.is_dirty()
    assert repo.is_dirty(Path("new.txt"))


def test_current_branch(repo: Repo) -> None:
    repo.run("checkout", "--quiet", "-b", "experiment")
    assert repo.current_branch() == "experiment"
