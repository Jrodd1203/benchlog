"""benchlog command line. Owner: Person 1."""

import functools
import json
from collections import Counter
from pathlib import Path

import typer
from rich import print
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from benchlog.core import prs
from benchlog.core.board_state import RewireStep
from benchlog.core.branches import BoardReport, board_report, checkout as checkout_branch, create_branch, list_branches
from benchlog.core.checks.store import check_project
from benchlog.core.diff import CircuitDiff, describe
from benchlog.core.diff import diff as circuit_diff
from benchlog.core.models import Circuit, Observation, ObservationKind
from benchlog.core.project import CIRCUIT_PATH, HardwareCheck, Project, ProjectError
from benchlog.demo import DEFAULT_WORKSPACE
from benchlog.core.repo import GitError
from benchlog.camera import load_calibration, read_board
from benchlog.core.reconcile import Reconciliation
from benchlog.core.scan import BoardReading, reading_from_circuit
from benchlog.serial.service import SerialSnapshot

app = typer.Typer(help="Version control for breadboard prototypes.", no_args_is_help=True)


def _todo(name: str) -> None:
    print(f"[yellow]benchlog {name}: not implemented yet[/yellow]")
    raise typer.Exit(1)


def _handle_errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ProjectError, GitError) as e:
            print(f"[red]error:[/red] {escape(str(e))}")
            raise typer.Exit(1) from e

    return wrapper


def _circuit_at(project: Project, rev: str) -> Circuit:
    """Circuit at `rev`; an empty board if the circuit didn't exist yet at that revision."""
    try:
        project.repo.run("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    except GitError as e:
        raise ProjectError(f"unknown revision {rev!r}") from e
    return project.circuit_at(rev) or Circuit(board=project.load_circuit().board)


def _summary(d: CircuitDiff) -> str:
    if d.is_empty:
        return "no circuit changes"
    parts = []
    if d.connections:
        n = len(d.connections)
        parts.append(f"{n} connection change{'s' * (n != 1)}")
    for kind, n in Counter(p.kind for p in d.placement).items():
        parts.append(f"{n} {kind}")
    return ", ".join(parts)


def _print_diff(d: CircuitDiff) -> None:
    lines = describe(d)
    for i, line in enumerate(lines):
        if i < len(d.connections):
            color, mark = ("green", "+") if d.connections[i].kind == "connected" else ("red", "-")
        else:
            color, mark = "yellow", "~"
        print(f"  [{color}]{mark} {escape(line)}[/{color}]")


@app.command()
@_handle_errors
def init(board: str = typer.Option("bb830", help="Breadboard template id.")) -> None:
    """Create a benchlog project in the current git repository (or a new one)."""
    project, created = Project.init(board=board)
    if created:
        for item in created:
            print(f"[green]created[/green] {escape(item)}")
    else:
        print("already initialized")
    print(f"project root: {project.repo.root}")


def _describe_observation(o: Observation) -> str:
    before, after = o.before or {}, o.after or {}
    if o.kind == ObservationKind.MOVED:
        detail = ", ".join(f"{k} {before.get(k, '-')} → {after.get(k, '-')}" for k in after if before.get(k) != after.get(k))
    else:
        detail = ", ".join((after or before).values())
    text = f"{o.id}  {o.kind.value} {o.object_type} {o.object_id}: {detail}  (confidence {o.confidence:.2f})"
    if o.uncertain_holes:
        text += f"  check {', '.join(o.uncertain_holes)}"
    return text


_VERDICT_TEXT = {
    "confirmed": ("green", "ESP32 confirms"),
    "conflict": ("red", "ESP32 disagrees"),
    "no_expectation": ("dim", "ESP32 can't tell"),
}


def _print_observations(observations: list[Observation], reconciliation: "Reconciliation | None" = None) -> None:
    verdicts = {v.observation_id: v for v in reconciliation.proposals} if reconciliation else {}
    for o in observations:
        color = "yellow" if o.uncertain_holes else "green"
        line = f"  [{color}]{escape(_describe_observation(o))}[/{color}]"
        verdict = verdicts.get(o.id)
        if verdict is not None and verdict.verdict in _VERDICT_TEXT:
            vcolor, text = _VERDICT_TEXT[verdict.verdict]
            gpios = f" ({', '.join(f'GPIO{g}' for g in verdict.gpios)})" if verdict.gpios else ""
            line += f"  [{vcolor}]{text}{gpios}[/{vcolor}]"
        print(line)
        if verdict is not None and verdict.message:
            print(f"      [red]{escape(verdict.message)}[/red]")


NO_ESP32 = "no ESP32 found"


def _serial_snapshot(project: Project, use_serial: bool) -> tuple["SerialSnapshot | None", str | None]:
    """Read the ESP32 agent (saved port, else auto-detected). Never raises: returns (None, why) instead."""
    if not use_serial:
        return None, None
    from benchlog.serial.agent_client import AgentError, PortBusy
    from benchlog.serial.service import find_esp32_port, snapshot_once

    port = project.config().get("serial_port") or find_esp32_port()
    if not port:
        return None, NO_ESP32

    try:
        return snapshot_once(port), None
    except PortBusy:
        return None, f"serial port {port} is busy (is `benchlog serve` connected to it?); camera only"
    except AgentError as e:
        return None, f"ESP32 not read ({e}); camera only"


def _print_hardware_check(check: "HardwareCheck") -> None:
    if check.status == "passed":
        print("[green]ESP32 check: passed[/green] (the board matches the circuit)")
    elif check.status == "skipped":
        print(f"[dim]ESP32 check: skipped ({escape(check.reason or '')})[/dim]")
    else:
        print(f"[red]ESP32 check: failed[/red] ({len(check.problems)} problem{'s' * (len(check.problems) != 1)})")
        for problem in check.problems:
            print(f"  [red]- {escape(problem)}[/red]")


def _print_serial(snapshot: "SerialSnapshot | None", note: str | None, reconciliation: "Reconciliation") -> None:
    if note:
        print(f"[yellow]serial:[/yellow] {escape(note)}")
    if snapshot is not None:
        probed = [g for g, state in snapshot.probe.pins.items() if state != "unsafe"]
        i2c = ", ".join(snapshot.i2c.devices) or "none"
        print(f"[dim]serial: ESP32 on {escape(snapshot.port)}, {len(probed)} pins probed, I2C devices: {i2c}[/dim]")
    explained = {v.message for v in reconciliation.proposals if v.message}
    for warning in reconciliation.warnings:
        if warning not in explained:  # per-observation messages are printed under the observation
            print(f"[red]ESP32:[/red] {escape(warning)}")


@app.command()
@_handle_errors
def scan(
    image: Path | None = typer.Option(None, "--image", help="Scan a photo instead of the camera."),
    camera: int | None = typer.Option(None, "--camera", help="Camera index (default: `benchlog camera use`, else 0)."),
    simulate: Path | None = typer.Option(None, "--simulate", help="Pretend the board looks like this circuit file."),
    sync: bool = typer.Option(False, "--sync", help="Record the board as matching the circuit; propose nothing."),
    debug: bool = typer.Option(False, "--debug", help="Explain what the camera saw and save images of it."),
    use_serial: bool = typer.Option(
        True, "--serial/--no-serial", help="Check the changes against the ESP32 (port from `benchlog serial use`)."
    ),
) -> None:
    """Capture the board and propose changes since the last reviewed scan.

    With an ESP32 serial agent set up (`benchlog serial use PORT`), each change the camera proposes
    is checked against what the ESP32's pins actually read.
    """
    project = Project.find()
    debug_lines: list[str] = []
    if simulate is not None:
        reading = reading_from_circuit(Circuit.model_validate_json(simulate.read_text()), f"simulated {simulate.name}")
    else:
        debug_dir = project.scans.dir / "debug" if debug else None
        reading = read_board(project.camera_index(camera), image, project.calibration_dir, debug_dir=debug_dir, debug_lines=debug_lines)
    for warning in reading.warnings:
        print(f"[yellow]warning:[/yellow] {escape(warning)}")
    if debug:
        pending = project.pending_observations()
        reference = project.scans.latest() if not pending and project.scans.latest() else project.scans.reference(project.load_circuit())
        filled = sorted(set(reading.occupied) - set(reference.occupied))
        emptied = sorted(set(reference.occupied) - set(reading.occupied))
        for line in debug_lines + [
            f"camera reads occupied: {reading.occupied or 'none'}",
            f"compared with {reference.source}: {reference.occupied or 'none'} occupied",
            f"so filled: {filled or 'none'}, emptied: {emptied or 'none'}",
        ]:
            print(f"[dim]debug:[/dim] {escape(line)}")

    # Read the ESP32 before recording the scan, so its readings are kept with the proposals and saved
    # into the circuit when they're accepted.
    snapshot, note = _serial_snapshot(project, use_serial and not sync)
    if note == NO_ESP32:
        note += "; camera only (pick its port with `benchlog serial list` and `benchlog serial use`)"
    observations = project.scan(reading, sync=sync, serial=snapshot.readings() if snapshot else None)
    if sync:
        project.check_with_serial([], None, None)  # nothing pending: clear old verdicts
        print(f"board recorded as matching the circuit ({len(reading.occupied)} occupied holes)")
        return
    reconciliation = project.check_with_serial(
        observations, snapshot.probe.pins if snapshot else None, snapshot.i2c.devices if snapshot else None
    )
    if debug and snapshot is not None:
        pins = ", ".join(f"GPIO{g} {state}" for g, state in sorted(snapshot.probe.pins.items()))
        print(f"[dim]debug:[/dim] ESP32 pins: {escape(pins)}")
    _print_serial(snapshot, note, reconciliation)
    if not observations:
        print("no changes seen since the last reviewed scan")
        return
    n = len(observations)
    print(f"[bold]{n} change{'s' * (n != 1)} to review[/bold] ({escape(reading.source)})")
    _print_observations(observations, reconciliation)
    print("review with `benchlog review accept|reject|edit`")


review_app = typer.Typer(help="Review what the last scan saw: accept, reject, or correct it.")
app.add_typer(review_app, name="review")


def _ids_or_all(ids: list[str] | None, all_: bool) -> list[str] | None:
    if all_ == bool(ids):
        raise ProjectError("name observations to act on (e.g. obs1 obs2) or pass --all")
    return None if all_ else ids


@review_app.callback(invoke_without_command=True)
@_handle_errors
def review(ctx: typer.Context) -> None:
    """List observations waiting for review."""
    if ctx.invoked_subcommand is not None:
        return
    project = Project.find()
    pending = project.pending_observations()
    if not pending:
        print("nothing to review")
        return
    print(f"[bold]{len(pending)} pending[/bold]")
    _print_observations(pending, project.reconciliation())


@review_app.command()
@_handle_errors
def accept(
    ids: list[str] | None = typer.Argument(None, help="Observation ids, e.g. obs1."),
    all_: bool = typer.Option(False, "--all", help="Accept every pending observation."),
) -> None:
    """Apply observations to the circuit (all or nothing)."""
    project = Project.find()
    before = project.load_circuit()
    accepted = project.accept_observations(_ids_or_all(ids, all_))
    if not accepted:
        print("nothing to review")
        return
    print(f"[green]accepted[/green] {', '.join(o.id for o in accepted)}")
    d = circuit_diff(before, project.load_circuit())
    if not d.is_empty:
        _print_diff(d)
    print("commit with `benchlog commit -m ...`")


@review_app.command()
@_handle_errors
def reject(
    ids: list[str] | None = typer.Argument(None, help="Observation ids, e.g. obs1."),
    all_: bool = typer.Option(False, "--all", help="Reject every pending observation."),
) -> None:
    """Discard observations the camera got wrong."""
    rejected = Project.find().reject_observations(_ids_or_all(ids, all_))
    print(f"rejected {', '.join(o.id for o in rejected)}" if rejected else "nothing to review")


@review_app.command()
@_handle_errors
def edit(
    obs_id: str = typer.Argument(..., help="Observation id, e.g. obs2."),
    ends: list[str] = typer.Argument(..., help="Wire ends to set, e.g. b=J40 (or a=A4 b=J40)."),
) -> None:
    """Set or correct a wire's ends before accepting it."""
    parsed = {}
    for item in ends:
        end, sep, hole = item.partition("=")
        if not sep or not hole:
            raise ProjectError(f"expected END=HOLE like b=J40, got {item!r}")
        parsed[end.strip()] = hole.strip().upper()
    _print_observations([Project.find().edit_observation(obs_id, parsed)])


@app.command()
@_handle_errors
def status() -> None:
    """Show the circuit's changes since the last commit and any pending observations."""
    project = Project.find()
    branch = project.repo.current_branch() or "(detached)"
    head = project.repo.log(limit=1)
    head_text = f"{head[0].short_sha} {escape(head[0].subject)}" if head else "no commits yet"
    print(f"On branch [bold]{escape(branch)}[/bold] · HEAD {head_text}")

    committed = project.circuit_at("HEAD")
    d = circuit_diff(project.baseline(), project.load_circuit())
    if committed is None:
        print(f"Circuit: [yellow]not committed yet[/yellow] ({_summary(d)})")
    elif d.is_empty:
        print("Circuit: matches HEAD")
    else:
        print(f"Circuit: [yellow]{_summary(d)}[/yellow] since HEAD (see `benchlog diff`)")

    pending = project.pending_observations()
    if pending:
        print(f"Observations: [yellow]{len(pending)} pending review[/yellow]")


@app.command()
@_handle_errors
def diff(
    old: str | None = typer.Argument(None, help="Revision to compare from (default: HEAD)."),
    new: str | None = typer.Argument(None, help="Revision to compare to (default: the working circuit)."),
) -> None:
    """Show circuit changes: electrical connections first, then placement."""
    project = Project.find()
    before = _circuit_at(project, old) if old else project.baseline()
    after = _circuit_at(project, new) if new else project.load_circuit()
    d = circuit_diff(before, after)
    if d.is_empty:
        print("no circuit changes")
        return
    print(f"[bold]{_summary(d)}[/bold]")
    _print_diff(d)


@app.command()
@_handle_errors
def commit(
    message: str = typer.Option(..., "-m", "--message"),
    firmware: list[Path] = typer.Option([], "-f", "--firmware", help="Firmware files or folders to include."),
    force: bool = typer.Option(False, "--force", help="Commit even if the ESP32 check fails."),
) -> None:
    """Commit the current circuit (and any firmware paths given).

    First checks the circuit against the real board through the ESP32 (like a merge check): a
    failure blocks the commit unless --force. The result is recorded in the commit message.
    """
    project = Project.find()
    pending = project.pending_observations()
    if pending:
        print(f"[yellow]warning:[/yellow] {len(pending)} observation(s) still pending; committing the accepted circuit")
    if project.circuit_at("HEAD") == project.load_circuit() and not firmware:
        raise ProjectError("nothing to commit: the circuit matches HEAD")
    snapshot, note = _serial_snapshot(project, use_serial=True)
    check = project.hardware_check(
        snapshot.probe.pins if snapshot else None, snapshot.i2c.devices if snapshot else None, reason=note or NO_ESP32
    )
    _print_hardware_check(check)
    if check.status == "failed" and not force:
        raise ProjectError("ESP32 check failed, so nothing was committed. Fix the board or the circuit, or commit anyway with --force")
    before = project.baseline()
    c = project.commit(message, firmware=[p.resolve() for p in firmware], trailer=check.trailer(forced=force))
    print(f"[green]\\[{c.short_sha}][/green] {escape(c.subject)}")
    d = circuit_diff(before, project.load_circuit())
    print(f"  {_summary(d)}")
    # Checks never block a commit; the report is saved so it can be looked up later.
    report = check_project(project)
    failing = sum(r.status == "fail" for r in report.results)
    detail = f" ({failing} failing)" if failing else ""
    print(f"  checks: {STATUS_STYLE[report.status]}{detail}; details with `benchlog check`")


@app.command()
@_handle_errors
def log(limit: int | None = typer.Option(None, "-n", help="Show at most this many commits.")) -> None:
    """Show commits that changed the circuit, newest first."""
    project = Project.find()
    commits = project.history(limit=limit)
    if not commits:
        print(f"no commits touch {CIRCUIT_PATH.as_posix()} yet")
        return
    table = Table(box=None, pad_edge=False)
    for col in ("commit", "date", "author", "message", "changes"):
        table.add_column(col)
    for c in commits:
        after = project.circuit_at(c.sha)
        before = project.circuit_at(f"{c.sha}^") or Circuit(board=after.board)
        table.add_row(
            f"[yellow]{c.short_sha}[/yellow]",
            c.date[:16].replace("T", " "),
            escape(c.author),
            escape(c.subject),
            _summary(circuit_diff(before, after)),
        )
    Console().print(table)


serial_app = typer.Typer(help="Set up the ESP32 serial agent that double-checks what the camera sees.")
app.add_typer(serial_app, name="serial")


@serial_app.command("list")
@_handle_errors
def serial_list() -> None:
    """Show serial ports (the ESP32 is usually a usbserial or SLAB_USBtoUART port)."""
    from benchlog.serial.agent_client import AgentError
    from benchlog.serial.service import available_ports

    try:
        ports = available_ports()
    except AgentError as e:
        raise ProjectError(str(e)) from e
    if not ports:
        print("no serial ports found; is the ESP32 plugged in?")
        return
    try:
        chosen = Project.find().config().get("serial_port")
    except (ProjectError, GitError):
        chosen = None
    for port in ports:
        mark = "  [green](chosen)[/green]" if port.device == chosen else ""
        print(f"{escape(port.device)}  {escape(port.description)}{mark}")
    print("test one with `benchlog serial probe PORT`, then pick it with `benchlog serial use PORT`")


@serial_app.command("use")
@_handle_errors
def serial_use(port: str = typer.Argument(..., help="Port from `benchlog serial list`.")) -> None:
    """Read the ESP32 on this port during every scan (saved for this workstation)."""
    Project.find().set_config("serial_port", port)
    print(f"scans will check changes against the ESP32 on {port}")


@serial_app.command("probe")
@_handle_errors
def serial_probe(port: str | None = typer.Argument(None, help="Port (default: the chosen one).")) -> None:
    """Read every safe pin and scan I2C once, to check the agent works."""
    from benchlog.serial.agent_client import AgentError
    from benchlog.serial.service import snapshot_once

    if port is None:
        port = Project.find().config().get("serial_port")
        if not port:
            raise ProjectError("no port chosen; pass one, or pick one with `benchlog serial use PORT`")
    try:
        snapshot = snapshot_once(port)
    except AgentError as e:
        raise ProjectError(str(e)) from e
    print(f"agent {snapshot.agent or '?'} on {escape(snapshot.port)}")
    for gpio, state in sorted(snapshot.probe.pins.items()):
        print(f"  GPIO{gpio}: {state}")
    print(f"I2C (SDA {snapshot.i2c.sda}, SCL {snapshot.i2c.scl}): {', '.join(snapshot.i2c.devices) or 'no devices'}")


camera_app = typer.Typer(help="Pick and aim the camera over the bench (webcam or Continuity Camera).")
app.add_typer(camera_app, name="camera")


def _vision_camera():
    try:
        from benchlog.vision import camera
    except ImportError as e:
        raise ProjectError("the camera needs the vision pipeline (install with `pip install -e '.[vision]'`)") from e
    return camera


@camera_app.command("list")
@_handle_errors
def camera_list() -> None:
    """Show which camera indices work, at what resolution."""
    cameras = _vision_camera().list_cameras()
    if not cameras:
        raise ProjectError(f"no cameras found. Things to try:\n{_vision_camera().SETUP_HINTS}")
    try:
        chosen = Project.find().camera_index()
    except (ProjectError, GitError):
        chosen = None
    for c in cameras:
        mark = "  [green](chosen)[/green]" if c.index == chosen else ""
        print(f"camera {c.index}: {c.width}x{c.height}{mark}")
    print("aim one with `benchlog camera preview N`, then pick it with `benchlog camera use N`")


@camera_app.command("use")
@_handle_errors
def camera_use(index: int = typer.Argument(..., help="Camera index from `benchlog camera list`.")) -> None:
    """Scan with this camera from now on (saved for this workstation)."""
    Project.find().set_config("camera", index)
    print(f"scans will use camera {index}")


@camera_app.command("preview")
@_handle_errors
def camera_preview(index: int | None = typer.Argument(None, help="Camera index (default: the chosen one).")) -> None:
    """Live view for aiming the camera; with a calibration, shows the tracked hole map too."""
    camera = _vision_camera()
    try:
        project = Project.find()
    except (ProjectError, GitError):
        project = None
    if index is None:
        index = project.camera_index() if project else 0
    calibration = load_calibration(project.calibration_dir if project else None)
    try:
        cap = camera.open_camera(index)
        try:
            camera.preview(cap, calibration)
        finally:
            cap.release()
    except camera.CameraError as e:
        raise ProjectError(str(e)) from e


@camera_app.command("calibrate")
@_handle_errors
def camera_calibrate(
    index: int | None = typer.Argument(None, help="Camera index (default: the chosen one)."),
    board_matches_circuit: bool = typer.Option(
        False, "--board-matches-circuit", help="Calibrate with the circuit built instead of an empty board."
    ),
) -> None:
    """Map every hole on the live image and save it (board empty, or matching the circuit)."""
    camera = _vision_camera()
    from benchlog.core.pairing import occupied_holes
    from benchlog.vision.calibration import run_calibration

    project = Project.find()
    index = project.camera_index(index)
    occupied = sorted(occupied_holes(project.load_circuit())) if board_matches_circuit else []
    if board_matches_circuit:
        print(f"The board must match the circuit exactly ({len(occupied)} occupied holes).")
    else:
        print("The board must be EMPTY (or use --board-matches-circuit).")
    print("Line the dots up with the holes (they snap on), then press Enter.")
    try:
        cap = camera.open_camera(index)
        try:
            calibration = run_calibration(cap, camera=index, occupied=occupied)
        finally:
            cap.release()
    except camera.CameraError as e:
        raise ProjectError(str(e)) from e
    if calibration is None:
        print("calibration cancelled; nothing saved")
        return
    calibration.save(project.calibration_dir)
    project.set_config("camera", index)
    # The board as calibrated is the new starting point for scans.
    source = "calibration (board matching circuit)" if board_matches_circuit else "calibration (empty board)"
    project.scans.reset(BoardReading(occupied=occupied, source=source))
    project.save_observations([])
    w, h = calibration.frame_size
    print(f"[green]saved[/green] hole map for camera {index} at {w}x{h} to {project.calibration_dir}")
    print("scans now track the board from this calibration; check it with `benchlog camera preview`")


STATUS_STYLE = {
    "pass": "[green]pass[/green]",
    "fail": "[red]fail[/red]",
    "needs_confirmation": "[yellow]confirm[/yellow]",
    "not_supported": "[dim]n/a[/dim]",
}


@app.command()
@_handle_errors
def check() -> None:
    """Run circuit checks against the current circuit. Exits 1 if any check fails."""
    report = check_project(Project.find())
    table = Table(box=None, pad_edge=False)
    for col in ("status", "check", "message", "involves"):
        table.add_column(col)
    for r in report.results:
        table.add_row(STATUS_STYLE[r.status], r.check, escape(r.message), escape(", ".join(r.ids)))
    Console().print(table)
    where = f"commit {report.commit[:7]} (saved)" if report.commit else "uncommitted changes"
    print(f"\n[bold]{STATUS_STYLE[report.status]}[/bold] on {where}")
    if report.not_supported:
        names = ", ".join(sorted({r.check for r in report.not_supported}))
        print(f"[dim]not checked (missing data): {names}[/dim]")
    if report.status == "fail":
        raise typer.Exit(1)


# ── Branches, board, PRs ──────────────────────────────────────────────────────


def _print_guide(steps: list[RewireStep]) -> None:
    for step in steps:
        style = "dim" if step.optional else "cyan"
        print(f"  [{style}]{step.step}.[/{style}] {escape(step.text)}")


def _print_board(report: BoardReport) -> None:
    if report.matches:
        print(f"[green]the board matches {report.branch}[/green]")
    elif report.matches is None:
        print("[yellow]benchlog hasn't seen this board yet[/yellow]: scan it to confirm it matches the circuit")
    else:
        assumed = f" (assuming it still matches {report.assumed_from[:7]})" if report.assumed_from else ""
        print(f"[yellow]rewire the board to match {report.branch}{assumed}:[/yellow]")
        _print_guide(report.guide)
        print("then scan to confirm")


@app.command()
@_handle_errors
def branch(name: str | None = typer.Argument(None, help="Create this branch at HEAD. Without it, list branches.")) -> None:
    """List branches, or create one at HEAD."""
    project = Project.find()
    if name:
        b = create_branch(project, name)
        print(f"created branch [bold]{escape(b.name)}[/bold] at {b.head[:7]}; switch to it with `benchlog checkout {b.name}`")
        return
    for b in list_branches(project):
        marker = "[green]*[/green]" if b.current else " "
        print(f"{marker} {escape(b.name):<24} [yellow]{b.head[:7]}[/yellow] {escape(b.subject)}")


@app.command()
@_handle_errors
def checkout(name: str) -> None:
    """Switch to a branch, then show how to rewire the board if it no longer matches."""
    report = checkout_branch(Project.find(), name)
    print(f"on branch [bold]{escape(report.branch)}[/bold]")
    _print_board(report)


@app.command()
@_handle_errors
def board() -> None:
    """Does the physical board match the checked-out circuit?"""
    project = Project.find()
    report = board_report(project)
    state = report.state
    matched = state.matches_commit[:7] if state.matches_commit else "unknown"
    print(f"board last confirmed at: {matched}" + (f" ({state.updated_at})" if state.updated_at else ""))
    _print_board(report)


pr_app = typer.Typer(help="Local pull requests: compare a branch with main, check it, merge it.", no_args_is_help=True)
app.add_typer(pr_app, name="pr")


def _print_pr(detail: prs.PRDetail) -> None:
    pr = detail.pr
    print(f"[bold]#{pr.id} {escape(pr.title)}[/bold]  \\[{pr.status}]  {escape(pr.from_branch)} → {escape(pr.into_branch)}")
    if pr.description:
        print(f"  {escape(pr.description)}")
    print("\n[bold]changes[/bold]")
    for line in detail.summary or ["no circuit changes"]:
        print(f"  {escape(line)}")
    if detail.checks:
        print(f"\n[bold]checks[/bold] on {detail.checks.commit[:7] if detail.checks.commit else '?'}: {STATUS_STYLE[detail.checks.status]}")
        for r in detail.checks.results:
            if r.status == "fail":
                print(f"  {STATUS_STYLE[r.status]} {r.check}: {escape(r.message)}")
    _print_merge_notes(detail.merge)
    tested = pr.tested
    if tested.done:
        print(f"\ntested on {tested.commit[:7]}" + (f": {escape(tested.note)}" if tested.note else ""))
    else:
        print("\n[dim]not tested on the latest commit[/dim]")
    verdict = "[green]can merge[/green]" if detail.merge.allowed else "[red]can't merge[/red]"
    print(f"{verdict}: {escape(detail.merge.reason)}")


def _print_merge_notes(merge: prs.MergeStatus) -> None:
    if merge.warnings:
        print("\n[yellow bold]warnings (confirm before merging, they don't block):[/yellow bold]")
        for w in merge.warnings:
            print(f"  [yellow]![/yellow] {escape(w)}")
    if merge.not_checked:
        print("\n[dim]not checked (missing data, not a pass):[/dim]")
        for n in merge.not_checked:
            print(f"  [dim]- {escape(n)}[/dim]")


@pr_app.command("create")
@_handle_errors
def pr_create(
    title: str = typer.Option(..., "--title", "-t"),
    description: str = typer.Option("", "--description", "-d"),
    into: str | None = typer.Option(None, "--into", help="Branch to merge into (default: main)."),
) -> None:
    """Open a PR from the current branch."""
    project = Project.find()
    pr = prs.create(project, project.repo.current_branch(), into, title, description)
    print(f"opened PR [bold]#{pr.id}[/bold]: {escape(pr.from_branch)} → {escape(pr.into_branch)}")
    _print_pr(prs.get(project, pr.id))


@pr_app.command("list")
@_handle_errors
def pr_list() -> None:
    """All PRs, newest last."""
    items = prs.list_prs(Project.find())
    if not items:
        print("no PRs yet; open one with `benchlog pr create --title ...`")
        return
    table = Table(box=None, pad_edge=False)
    for col in ("#", "status", "title", "branches", "checks", "tested"):
        table.add_column(col)
    for pr in items:
        checks = STATUS_STYLE[pr.checks.overall] if pr.checks else "-"
        table.add_row(
            str(pr.id), pr.status, escape(pr.title), escape(f"{pr.from_branch} → {pr.into_branch}"), checks,
            "yes" if pr.tested.done else "no",
        )  # fmt: skip
    Console().print(table)


@pr_app.command("show")
@_handle_errors
def pr_show(pr_id: int) -> None:
    """Show a PR: its changes, checks, and whether it can be merged."""
    _print_pr(prs.get(Project.find(), pr_id))


@pr_app.command("tested")
@_handle_errors
def pr_tested(pr_id: int, note: str = typer.Option("", "--note", "-n")) -> None:
    """Record that the branch's latest commit was tested on the real board."""
    pr = prs.mark_tested(Project.find(), pr_id, note)
    print(f"PR #{pr.id} marked as tested on {pr.tested.commit[:7]}")


@pr_app.command("merge")
@_handle_errors
def pr_merge(pr_id: int) -> None:
    """Fast-forward the target branch to this PR's branch (checks must pass)."""
    detail = prs.merge(Project.find(), pr_id)
    print(f"[green]merged[/green] PR #{pr_id} into {escape(detail.pr.into_branch)}; now on {escape(detail.pr.into_branch)}")
    if detail.merge.warnings:
        print("[yellow]merged with warnings:[/yellow]")
        for w in detail.merge.warnings:
            print(f"  [yellow]![/yellow] {escape(w)}")


@pr_app.command("close")
@_handle_errors
def pr_close(pr_id: int) -> None:
    """Close a PR without merging it."""
    result = prs.close(Project.find(), pr_id)
    print(f"closed PR #{result.pr.id}")
    if result.guide:
        print(f"to put the board back to {escape(result.pr.into_branch)} (then `benchlog checkout {result.pr.into_branch}`):")
        _print_guide(result.guide)


@app.command()
def schema(output: Path | None = typer.Option(None, "-o", "--output", help="Write to a file instead of stdout.")) -> None:
    """Print the JSON Schema of the types shared with the web UI."""
    from benchlog.core.schema import json_schema

    text = json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"
    if output:
        output.write_text(text)
    else:
        typer.echo(text, nl=False)


demo_app = typer.Typer(help="Demo projects to browse in the UI.")
app.add_typer(demo_app, name="demo")


@demo_app.command("seed")
@_handle_errors
def demo_seed(
    workspace: Path = typer.Option(DEFAULT_WORKSPACE, "--dir", help="Projects folder to create them in."),
    reset: bool = typer.Option(False, "--reset", help="Rebuild demo projects that already exist."),
) -> None:
    """Create demo projects with history, branches and PRs (real git repos, safe to explore)."""
    from benchlog.demo import seed

    for demo, path, what in seed(workspace.expanduser().resolve(), reset=reset):
        color = "green" if what == "created" else "yellow"
        print(f"[{color}]{escape(demo.folder)}[/{color}]: {escape(what)}  [dim]{escape(str(path))}[/dim]")
    print("browse them in the UI: `benchlog serve` (the projects screen lists this folder)")


@app.command()
@_handle_errors
def serve(
    port: int = typer.Option(8000, help="Port to listen on."),
    host: str = typer.Option(
        "127.0.0.1", help="Address to listen on; 0.0.0.0 makes it reachable from other devices on the network."
    ),
    project_dir: Path | None = typer.Option(
        None, "--project", help="benchlog project to serve (default: the one containing the current folder)."
    ),
    workspace: Path = typer.Option(
        DEFAULT_WORKSPACE, help="Projects folder the UI can browse and open (see `benchlog demo seed`)."
    ),
    reload: bool = typer.Option(
        False, "--reload/--no-reload", help="Restart when benchlog's code changes (development; drops the ESP32 link)."
    ),
) -> None:
    """Start the local API for the web UI (needs the `server` extra)."""
    try:
        import uvicorn
    except ImportError as e:
        raise ProjectError("the API needs the server extra (install with `pip install -e '.[server]'`)") from e
    import os

    import benchlog

    workspace = workspace.expanduser().resolve()
    try:
        project = Project.find(project_dir.resolve() if project_dir else None)
    except ProjectError:
        # Not inside a project: open the first one in the projects folder, if there is one.
        found = sorted(p for p in workspace.glob(f"*/{CIRCUIT_PATH}")) if not project_dir and workspace.is_dir() else []
        if not found:
            raise
        project = Project.find(found[0].parent.parent)
    # The app finds the project per request through these, in this process and in reload workers.
    os.environ["BENCHLOG_PROJECT"] = str(project.repo.root)
    os.environ["BENCHLOG_PROJECTS_ROOT"] = str(workspace)  # the UI's project list (GET /api/projects)
    shown = "localhost" if host in ("127.0.0.1", "localhost") else host
    print(f"serving [bold]{escape(str(project.repo.root))}[/bold] at http://{shown}:{port} (API docs: /docs)")
    if workspace.is_dir():
        print(f"[dim]projects folder: {escape(str(workspace))}[/dim]")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("[yellow]warning:[/yellow] reachable from other devices on this network, with no login")
    uvicorn.run(
        "benchlog.server.app:app",
        host=host,
        port=port,
        reload=reload,
        # Only benchlog's own code; scans and commits write project files that mustn't trigger restarts.
        reload_dirs=[str(Path(benchlog.__file__).parent)] if reload else None,
    )
