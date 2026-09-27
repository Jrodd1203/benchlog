"""A benchlog project: where the circuit and pending observations live inside a git repo.

    benchlog/circuit.json        committed: the accepted circuit
    .benchlog/observations.json  gitignored: scan results waiting for review
    .benchlog/calibration/       gitignored: hole map + empty-board reference image for this camera
    .benchlog/config.json        gitignored: this workstation's settings, e.g. {"camera": 1}
    .benchlog/reconciliation.json  gitignored: the ESP32's verdicts on the pending observations
    .benchlog/baseline/          gitignored: empty-board capture from `benchlog.vision.capture`
    .benchlog/scans/             gitignored: reference and latest board readings, and the serial
                                 readings taken with the latest scan (serial.json)
    .benchlog/board_state.json   gitignored: the last commit the physical board matched
    .benchlog/prs/               gitignored: pull request pointers (see core/prs.py)
    .benchlog/checks/            gitignored: check reports, one per commit
"""

import json
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, TypeAdapter

from benchlog.core.board import TEMPLATES
from benchlog.core.board_state import BoardStateStore, fingerprint
from benchlog.core.models import Circuit, ComponentType, Hole, Observation, ObservationKind, ObservationStatus, Suggestion
from benchlog.core.pairing import apply_observations
from benchlog.core.parts import esp32_devkit_v1_30_pins
from benchlog.core.reconcile import Reconciliation, SerialReadings, reconcile, serial_record
from benchlog.core.repo import Commit, GitError, Repo
from benchlog.core.scan import BoardReading, MisreadError, ScanStore
from benchlog.core.scan import scan as run_scan
from benchlog.core.serialize import dump

CIRCUIT_PATH = Path("benchlog/circuit.json")
STATE_DIR = Path(".benchlog")
OBSERVATIONS_PATH = STATE_DIR / "observations.json"
CALIBRATION_DIR = STATE_DIR / "calibration"
CONFIG_PATH = STATE_DIR / "config.json"
RECONCILIATION_PATH = STATE_DIR / "reconciliation.json"

_observations = TypeAdapter(list[Observation])


class HardwareCheck(BaseModel):
    """Does the real board (as the ESP32 senses it) match a circuit? Gates commits, like a merge check."""

    status: Literal["passed", "failed", "skipped"]
    problems: list[str] = []
    reason: str | None = None  # why it was skipped

    def trailer(self, forced: bool = False) -> str:
        """Git trailer recording the result in the commit message."""
        return f"ESP32-Check: {self.status}" + (" (forced)" if forced and self.status == "failed" else "")


def _anchor_esp32(current: dict[str, Hole], given: dict[str, Hole], template) -> dict[str, Hole]:
    """All 30 DevKit pins from one or two the user placed (EN, and VIN to set which way round).

    EN and VIN are the two ends of the same header, so VIN's hole sets the orientation; with EN
    alone, the current orientation is kept.
    """
    if "EN" not in given:
        raise ProjectError("to place the ESP32, give EN (and VIN to set which way round), e.g. EN=A40 VIN=A26")
    en = given["EN"]
    col, en_row = en[0], int(en[1:])
    right = {"A": "H", "B": "I", "C": "J"}.get(col)
    if right is None or en[1] in "+-":
        raise ProjectError("EN must be in column A, B or C (the ESP32 straddles the centre channel)")
    if "VIN" in given:
        vin = given["VIN"]
        if vin[0] != col or abs(int(vin[1:]) - en_row) != 14:
            raise ProjectError("VIN must be in the same column as EN, 14 holes away (the other end of that header)")
        en_first = int(vin[1:]) > en_row
    elif current.get("EN") and current.get("VIN"):
        en_first = int(current["VIN"][1:]) > int(current["EN"][1:])
    else:
        en_first = True
    top = en_row if en_first else en_row - 14
    pins = esp32_devkit_v1_30_pins(top, col, right)
    if not en_first:  # mirrored end to end: EN at the bottom row, VIN at the top
        pins = {name: f"{hole[0]}{2 * top + 14 - int(hole[1:])}" for name, hole in pins.items()}
    bad = [h for h in pins.values() if not template.is_valid(h)]
    if bad:
        raise ProjectError(f"the ESP32 wouldn't fit there (off the board at {', '.join(bad[:3])})")
    return pins


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
        self.serial_readings_path = self.scans.dir / "serial.json"

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

    def first_row(self) -> int:
        """The number printed on the board's first row: 1 (benchlog's own numbering) or 0."""
        return int(self.config().get("first_row", 1))

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

    def scan(self, reading: BoardReading, sync: bool = False, serial: SerialReadings | None = None) -> list[Observation]:
        """Record a board reading and save the changes it implies as pending observations.

        With `sync`, the reading becomes the reference (the board matches the circuit) and
        nothing is proposed. `serial` is what the ESP32 agent read during the scan; it is kept
        until the proposals are accepted, then saved into the circuit.
        """
        self._keep_serial_readings(serial)
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

    def check_with_serial(
        self, observations: list[Observation], pins: dict[int, str] | None, i2c_devices: list[str] | None
    ) -> Reconciliation:
        """Run the reconciler on a scan's observations against what the ESP32 sensed, and keep the result.

        `pins`/`i2c_devices` are None when no serial agent was read: every verdict is then "not_checked".
        """
        result = reconcile(self.load_circuit(), observations, pins, i2c_devices)
        path = self.repo.root / RECONCILIATION_PATH
        path.parent.mkdir(exist_ok=True)
        path.write_text(result.model_dump_json(indent=2) + "\n")
        return result

    def reconciliation(self) -> Reconciliation | None:
        """The ESP32's verdicts from the last scan, if it has any."""
        path = self.repo.root / RECONCILIATION_PATH
        return Reconciliation.model_validate_json(path.read_text()) if path.exists() else None

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

    def _keep_serial_readings(self, readings: SerialReadings | None) -> None:
        if readings is None:
            self.serial_readings_path.unlink(missing_ok=True)  # never pair old readings with a new scan
            return
        self.serial_readings_path.parent.mkdir(parents=True, exist_ok=True)
        self.serial_readings_path.write_text(readings.model_dump_json(indent=2) + "\n")

    def serial_readings(self) -> SerialReadings | None:
        """The serial readings taken with the latest scan, if any."""
        path = self.serial_readings_path
        return SerialReadings.model_validate_json(path.read_text()) if path.exists() else None

    def accept_observations(self, ids: list[str] | None = None) -> list[Observation]:
        """Apply pending observations to the circuit. All or nothing: if one can't be applied, none are.

        If the scan had serial readings, their verdicts for the resulting circuit are saved in
        `circuit.serial`, so they are committed with it.
        """
        observations, chosen = self._select_pending(ids)
        try:
            circuit = apply_observations(self.load_circuit(), chosen)
        except ValueError as e:
            raise ProjectError(str(e)) from e
        readings = self.serial_readings()
        if readings is not None:
            circuit = circuit.model_copy(update={"serial": serial_record(circuit, readings)})
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

    def edit_observation(
        self, obs_id: str, ends: dict[str, Hole] | None = None, suggestion: dict[str, str | None] | None = None
    ) -> Observation:
        """Correct a pending observation before accepting it.

        Wires: set or correct ends, e.g. {"b": "J40"}. Components: set pins (e.g. {"anode": "E18"},
        to fix polarity) and say what the part is: {"type": "resistor", "value": "220Ω"}.
        The user has now looked at it, so the whole observation counts as confirmed.
        """
        observations, [obs] = self._select_pending([obs_id])
        ends, suggestion = ends or {}, suggestion or {}
        if obs.kind == ObservationKind.REMOVED:
            raise ProjectError(f"{obs_id}: a removal has nothing to edit; reject it if it's wrong")
        if obs.object_type == "wire":
            if suggestion:
                raise ProjectError(f"{obs_id}: type, value and model are for components, not wires")
            bad_ends = set(ends) - {"a", "b"}
            if bad_ends:
                raise ProjectError(f"{obs_id}: a wire's ends are 'a' and 'b', not {', '.join(sorted(bad_ends))}")
        template = TEMPLATES[self.load_circuit().board]
        bad_holes = [h for h in ends.values() if not template.is_valid(h)]
        if bad_holes:
            raise ProjectError(f"not a hole on {template.id}: {', '.join(bad_holes)}")
        after = {**(obs.after or {}), **ends}
        if obs.suggested and obs.suggested.type == ComponentType.ESP32_DEVKIT_V1_30 and ends:
            after = _anchor_esp32(obs.after or {}, ends, template)
        update: dict = {"after": after, "uncertain_holes": [], "confidence": 1.0}
        if suggestion:
            bad_keys = set(suggestion) - {"type", "value", "model"}
            if bad_keys:
                raise ProjectError(f"{obs_id}: can set type, value and model, not {', '.join(sorted(bad_keys))}")
            previous = obs.suggested.model_dump(mode="json") if obs.suggested else {}
            if "type" in suggestion and suggestion["type"] != previous.get("type"):
                previous = {}  # a different kind of part: its old value and model don't apply
            merged = {**previous, **suggestion}
            try:
                update["suggested"] = Suggestion.model_validate(merged)
            except ValueError as e:
                kinds = ", ".join(t.value for t in ComponentType)
                raise ProjectError(f"{obs_id}: unknown component type {suggestion.get('type')!r} (one of: {kinds})") from e
        edited = obs.model_copy(update=update)
        self.save_observations([edited if o.id == obs_id else o for o in observations])
        return edited

    def history(self, limit: int | None = None) -> list[Commit]:
        """Commits that changed the circuit, newest first."""
        return self.repo.log(path=self.circuit_path, limit=limit)

    def commit(self, message: str, firmware: list[Path] = (), trailer: str | None = None) -> Commit:
        """Stage the circuit (and any firmware paths) and commit, with an optional trailer line."""
        self.repo.add(self.circuit_path, *firmware)
        if not self.repo.has_staged_changes():
            raise ProjectError("nothing to commit: the circuit and firmware match HEAD")
        board_matched = self.board_matches_working()
        commit = self.repo.commit(f"{message}\n\n{trailer}" if trailer else message)
        if board_matched:
            self.board_confirmed()  # the board matched what was just committed
        return commit

    def hardware_check(self, pins: dict[int, str] | None, i2c_devices: list[str] | None, reason: str | None = None) -> HardwareCheck:
        """Check the working circuit against what the ESP32 sensed (None: no reading, so skipped).

        Fails on any pin that reads differently from what the circuit implies, and on declared I2C
        parts that don't answer. Undeclared I2C devices are only reported by scans, not failures.
        """
        if pins is None:
            return HardwareCheck(status="skipped", reason=reason or "no ESP32 reading")
        result = reconcile(self.load_circuit(), [], pins, i2c_devices)
        if not result.pins:
            return HardwareCheck(status="skipped", reason="the circuit has no ESP32 to check against")
        problems = [p.message for p in result.pins if p.verdict == "conflict" and p.message]
        problems += [f"{c.component} doesn't answer on I2C (expected at {c.address})" for c in result.i2c if c.status == "missing"]
        return HardwareCheck(status="failed" if problems else "passed", problems=problems)
