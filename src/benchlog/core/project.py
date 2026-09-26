"""A benchlog project: where the circuit and pending observations live inside a git repo.

    benchlog/circuit.json        committed: the accepted circuit
    .benchlog/observations.json  gitignored: scan results waiting for review
    .benchlog/calibration.json   gitignored: camera calibration for this workstation
    .benchlog/baseline/          gitignored: empty-board capture from `benchlog.vision.capture`
    .benchlog/scans/             gitignored: reference and latest board readings
"""

import json
from pathlib import Path

from pydantic import TypeAdapter

from benchlog.core.models import Circuit, Observation, ObservationStatus
from benchlog.core.repo import Commit, GitError, Repo
from benchlog.core.scan import ScanStore
from benchlog.core.serialize import dump

CIRCUIT_PATH = Path("benchlog/circuit.json")
STATE_DIR = Path(".benchlog")
OBSERVATIONS_PATH = STATE_DIR / "observations.json"
CALIBRATION_PATH = STATE_DIR / "calibration.json"

_observations = TypeAdapter(list[Observation])


class ProjectError(RuntimeError):
    pass


class Project:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.circuit_path = repo.root / CIRCUIT_PATH
        self.observations_path = repo.root / OBSERVATIONS_PATH
        self.scans = ScanStore(repo.root / STATE_DIR)

    @classmethod
    def find(cls, start: Path | None = None) -> "Project":
        try:
            repo = Repo.find(start)
        except GitError as e:
            raise ProjectError("not inside a git repository; run `benchlog init`") from e
        project = cls(repo)
        if not project.circuit_path.exists():
            raise ProjectError(f"no {CIRCUIT_PATH} in {repo.root}; run `benchlog init`")
        return project

    @classmethod
    def init(cls, start: Path | None = None, board: str = "bb830") -> tuple["Project", list[str]]:
        """Set up (or finish setting up) a project. Returns the project and what was created."""
        start = start or Path.cwd()
        created = []
        try:
            repo = Repo.find(start)
        except GitError:
            repo = Repo.init(start)
            created.append("git repository")
        project = cls(repo)

        if not project.circuit_path.exists():
            project.circuit_path.parent.mkdir(parents=True, exist_ok=True)
            dump(Circuit(board=board), project.circuit_path)
            created.append(CIRCUIT_PATH.as_posix())
        (repo.root / STATE_DIR).mkdir(exist_ok=True)

        gitignore = repo.root / ".gitignore"
        existing = gitignore.read_text() if gitignore.exists() else ""
        entry = f"{STATE_DIR.as_posix()}/"
        if entry not in existing.splitlines():
            prefix = "" if not existing or existing.endswith("\n") else "\n"
            gitignore.write_text(f"{existing}{prefix}{entry}\n")
            created.append(f".gitignore entry {entry}")
        return project, created

    def load_circuit(self) -> Circuit:
        return Circuit.model_validate_json(self.circuit_path.read_text())

    def save_circuit(self, circuit: Circuit) -> None:
        dump(circuit, self.circuit_path)

    def circuit_at(self, rev: str) -> Circuit | None:
        """The committed circuit at `rev`, or None if it didn't exist there (or `rev` doesn't)."""
        text = self.repo.show(rev, self.circuit_path)
        return None if text is None else Circuit.model_validate_json(text)

    def baseline(self) -> Circuit:
        """The circuit at HEAD, or an empty board before the first commit."""
        return self.circuit_at("HEAD") or Circuit(board=self.load_circuit().board)

    def observations(self) -> list[Observation]:
        if not self.observations_path.exists():
            return []
        return _observations.validate_json(self.observations_path.read_text())

    def save_observations(self, observations: list[Observation]) -> None:
        self.observations_path.parent.mkdir(exist_ok=True)
        data = _observations.dump_python(observations, mode="json", exclude_none=True)
        self.observations_path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    def pending_observations(self) -> list[Observation]:
        return [o for o in self.observations() if o.status == ObservationStatus.PENDING]

    def history(self, limit: int | None = None) -> list[Commit]:
        """Commits that changed the circuit, newest first."""
        return self.repo.log(path=self.circuit_path, limit=limit)

    def commit(self, message: str, firmware: list[Path] = ()) -> Commit:
        """Stage the circuit (and any firmware paths) and commit."""
        self.repo.add(self.circuit_path, *firmware)
        if not self.repo.has_staged_changes():
            raise ProjectError("nothing to commit: the circuit and firmware match HEAD")
        return self.repo.commit(message)
