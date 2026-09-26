"""A benchlog project: where the circuit and pending observations live inside a git repo.

    benchlog/circuit.json        committed: the accepted circuit
    .benchlog/observations.json  gitignored: scan results waiting for review
    .benchlog/calibration/       gitignored: hole map + empty-board reference image for this camera
    .benchlog/config.json        gitignored: this workstation's settings, e.g. {"camera": 1}
    .benchlog/baseline/          gitignored: empty-board capture from `benchlog.vision.capture`
    .benchlog/scans/             gitignored: reference and latest board readings
"""

import json
from pathlib import Path

from pydantic import TypeAdapter

from benchlog.core.board import TEMPLATES
from benchlog.core.board_state import BoardStateStore, fingerprint
from benchlog.core.models import Circuit, Hole, Observation, ObservationKind, ObservationStatus
from benchlog.core.pairing import apply_observations
from benchlog.core.repo import Commit, GitError, Repo
from benchlog.core.scan import BoardReading, MisreadError, ScanStore
from benchlog.core.scan import scan as run_scan
from benchlog.core.serialize import dump

CIRCUIT_PATH = Path("benchlog/circuit.json")
STATE_DIR = Path(".benchlog")
OBSERVATIONS_PATH = STATE_DIR / "observations.json"
CALIBRATION_DIR = STATE_DIR / "calibration"
CONFIG_PATH = STATE_DIR / "config.json"

_observations = TypeAdapter(list[Observation])


class ProjectError(RuntimeError):
    pass


class Project:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.circuit_path = repo.root / CIRCUIT_PATH
        self.observations_path = repo.root / OBSERVATIONS_PATH
        self.scans = ScanStore(repo.root / STATE_DIR)
        self.calibration_dir = repo.root / CALIBRATION_DIR
        self.state_dir = repo.root / STATE_DIR
        self.board_state = BoardStateStore(self.state_dir)

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
        if project.ensure_state_ignored():
            created.append(f".gitignore entry {STATE_DIR.as_posix()}/")
        return project, created

    def ensure_state_ignored(self) -> bool:
        """Add .benchlog/ to .gitignore if it isn't there. Returns True if it was added."""
        gitignore = self.repo.root / ".gitignore"
        existing = gitignore.read_text() if gitignore.exists() else ""
        entry = f"{STATE_DIR.as_posix()}/"
        if entry in existing.splitlines():
            return False
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        gitignore.write_text(f"{existing}{prefix}{entry}\n")
        return True

    def matches_head(self) -> bool:
        """True if the working circuit is exactly the committed one."""
        return self.repo.head() is not None and self.circuit_at("HEAD") == self.load_circuit()

    def board_confirmed(self) -> None:
        """Record that the physical board matches the working circuit right now."""
        self.ensure_state_ignored()
        self.board_state.set(self.repo.head() if self.matches_head() else None, self.load_circuit())

    def board_matches_working(self) -> bool:
        """True if the board was last confirmed to match exactly the working circuit."""
        return self.board_state.load().circuit_fingerprint == fingerprint(self.load_circuit())

    def config(self) -> dict:
        path = self.repo.root / CONFIG_PATH
        return json.loads(path.read_text()) if path.exists() else {}

    def set_config(self, key: str, value: object) -> None:
        path = self.repo.root / CONFIG_PATH
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({**self.config(), key: value}, indent=2, sort_keys=True) + "\n")

    def camera_index(self, override: int | None = None) -> int:
        """The camera to scan with: an explicit override, else the saved one, else 0."""
        return override if override is not None else int(self.config().get("camera", 0))

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

    def scan(self, reading: BoardReading, sync: bool = False) -> list[Observation]:
        """Record a board reading and save the changes it implies as pending observations.

        With `sync`, the reading becomes the reference (the board matches the circuit) and
        nothing is proposed.
        """
        if sync:
            self.scans.save_latest(reading)
            self.scans.promote_latest()
            self.save_observations([])
            self.board_confirmed()
            return []
        try:
            observations = run_scan(self.load_circuit(), self.scans, reading, self.pending_observations())
        except MisreadError as e:
            raise ProjectError(str(e)) from e
        self.save_observations(observations)
        if not observations:
            self.board_confirmed()  # a clean scan: the board matches the circuit
        return observations

    def _select_pending(self, ids: list[str] | None) -> tuple[list[Observation], list[Observation]]:
        """(all observations, the pending ones named by `ids`, or every pending one if None)."""
        observations = self.observations()
        pending = {o.id: o for o in observations if o.status == ObservationStatus.PENDING}
        if ids is None:
            return observations, list(pending.values())
        unknown = [i for i in ids if i not in pending]
        if unknown:
            raise ProjectError(f"no pending observation {', '.join(unknown)}")
        return observations, [pending[i] for i in dict.fromkeys(ids)]

    def _set_status(
        self, observations: list[Observation], chosen: list[Observation], status: ObservationStatus
    ) -> list[Observation]:
        """Save `chosen` with a new status; returns them as saved."""
        updated = {o.id: o.model_copy(update={"status": status}) for o in chosen}
        self.save_observations([updated.get(o.id, o) for o in observations])
        return list(updated.values())

    def accept_observations(self, ids: list[str] | None = None) -> list[Observation]:
        """Apply pending observations to the circuit. All or nothing: if one can't be applied, none are."""
        observations, chosen = self._select_pending(ids)
        try:
            circuit = apply_observations(self.load_circuit(), chosen)
        except ValueError as e:
            raise ProjectError(str(e)) from e
        self.save_circuit(circuit)
        accepted = self._set_status(observations, chosen, ObservationStatus.ACCEPTED)
        self._reviewed()
        return accepted

    def reject_observations(self, ids: list[str] | None = None) -> list[Observation]:
        observations, chosen = self._select_pending(ids)
        rejected = self._set_status(observations, chosen, ObservationStatus.REJECTED)
        self._reviewed()
        return rejected

    def _reviewed(self) -> None:
        """Once every scan proposal is reviewed, the working circuit describes the board."""
        if not self.pending_observations():
            self.board_confirmed()

    def edit_observation(self, obs_id: str, ends: dict[str, Hole]) -> Observation:
        """Set or correct wire ends of a pending added/moved wire, e.g. {"b": "J40"}.

        The user has now looked at this wire, so the whole observation counts as confirmed.
        """
        observations, [obs] = self._select_pending([obs_id])
        if obs.object_type != "wire" or obs.kind == ObservationKind.REMOVED:
            raise ProjectError(f"{obs_id}: only added or moved wires can be edited")
        bad_ends = set(ends) - {"a", "b"}
        if bad_ends:
            raise ProjectError(f"{obs_id}: a wire's ends are 'a' and 'b', not {', '.join(sorted(bad_ends))}")
        template = TEMPLATES[self.load_circuit().board]
        bad_holes = [h for h in ends.values() if not template.is_valid(h)]
        if bad_holes:
            raise ProjectError(f"not a hole on {template.id}: {', '.join(bad_holes)}")
        edited = obs.model_copy(update={"after": {**(obs.after or {}), **ends}, "uncertain_holes": [], "confidence": 1.0})
        self.save_observations([edited if o.id == obs_id else o for o in observations])
        return edited

    def history(self, limit: int | None = None) -> list[Commit]:
        """Commits that changed the circuit, newest first."""
        return self.repo.log(path=self.circuit_path, limit=limit)

    def commit(self, message: str, firmware: list[Path] = ()) -> Commit:
        """Stage the circuit (and any firmware paths) and commit."""
        self.repo.add(self.circuit_path, *firmware)
        if not self.repo.has_staged_changes():
            raise ProjectError("nothing to commit: the circuit and firmware match HEAD")
        board_matched = self.board_matches_working()
        commit = self.repo.commit(message)
        if board_matched:
            self.board_confirmed()  # the board matched what was just committed
        return commit
