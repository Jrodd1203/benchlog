"""Thin wrapper around the `git` CLI (subprocess). Owner: Person 1.

Git does the real work (staging, commits, branches, history); this only gives the rest of
benchlog typed results for the few operations it needs.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Unit/record separators keep `git log` output parseable whatever the commit message contains.
_FIELD = "\x1f"
_RECORD = "\x1e"


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Commit:
    sha: str
    short_sha: str
    author: str
    date: str  # ISO 8601
    subject: str


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @classmethod
    def find(cls, start: Path | None = None) -> "Repo":
        """The repository containing `start` (default: the current directory)."""
        out = _git(start or Path.cwd(), "rev-parse", "--show-toplevel")
        return cls(Path(out.strip()))

    @classmethod
    def init(cls, path: Path) -> "Repo":
        path.mkdir(parents=True, exist_ok=True)
        _git(path, "init", "--quiet")
        return cls(path)

    def run(self, *args: str) -> str:
        return _git(self.root, *args)

    def head(self) -> str | None:
        """Full sha of HEAD, or None before the first commit."""
        try:
            return self.run("rev-parse", "--verify", "--quiet", "HEAD").strip()
        except GitError:
            return None

    def current_branch(self) -> str:
        return self.run("branch", "--show-current").strip()

    def add(self, *paths: Path) -> None:
        self.run("add", "--", *(self._rel(p) for p in paths))

    def has_staged_changes(self) -> bool:
        try:
            self.run("diff", "--cached", "--quiet")  # exits 1 when something is staged
            return False
        except GitError:
            return True

    def commit(self, message: str) -> Commit:
        """Commit what is staged. Raises GitError if nothing is staged."""
        self.run("commit", "--quiet", "-m", message)
        return self.log(limit=1)[0]

    def log(self, path: Path | None = None, limit: int | None = None) -> list[Commit]:
        """Commits newest first, optionally only those touching `path`."""
        if self.head() is None:
            return []
        args = ["log", f"--format=%H{_FIELD}%h{_FIELD}%an{_FIELD}%aI{_FIELD}%s{_RECORD}"]
        if limit is not None:
            args.append(f"-n{limit}")
        if path is not None:
            args += ["--", self._rel(path)]
        records = self.run(*args).split(_RECORD)
        return [Commit(*r.strip("\n").split(_FIELD)) for r in records if r.strip()]

    def show(self, rev: str, path: Path) -> str | None:
        """Contents of `path` at `rev`, or None if it didn't exist there."""
        try:
            return self.run("show", f"{rev}:{self._rel(path)}")
        except GitError:
            return None

    def is_dirty(self, path: Path | None = None) -> bool:
        """True if `path` (or anything, if None) differs from HEAD or is untracked."""
        args = ["status", "--porcelain"]
        if path is not None:
            args += ["--", self._rel(path)]
        return bool(self.run(*args).strip())

    def remote_url(self, name: str = "origin") -> str | None:
        """The URL remote `name` was given (as configured), or None if there is no such remote."""
        try:
            return self.run("config", "--get", f"remote.{name}.url").strip()
        except GitError:
            return None

    def add_remote(self, url: str, name: str = "origin") -> None:
        self.run("remote", "add", name, url)

    def push(self, branch: str, remote: str = "origin") -> None:
        """Push `branch` and make it track the remote's copy (`git push -u`)."""
        self.run("push", "--quiet", "-u", remote, branch)

    def _rel(self, path: Path) -> str:
        path = path if path.is_absolute() else self.root / path
        return path.resolve().relative_to(self.root).as_posix()


def _git(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise GitError("git is not installed") from e
    if result.returncode != 0:
        msg = (result.stderr or result.stdout).strip() or f"git {args[0]} failed"
        raise GitError(msg)
    return result.stdout
