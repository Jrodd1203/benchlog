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

from benchlog.core.checks.store import check_project
from benchlog.core.diff import CircuitDiff, describe
from benchlog.core.diff import diff as circuit_diff
from benchlog.core.models import Circuit, Observation, ObservationKind
from benchlog.core.project import CIRCUIT_PATH, Project, ProjectError
from benchlog.core.repo import GitError
from benchlog.camera import load_calibration, read_board
from benchlog.core.scan import BoardReading, reading_from_circuit

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


def _print_observations(observations: list[Observation]) -> None:
    for o in observations:
        color = "yellow" if o.uncertain_holes else "green"
        print(f"  [{color}]{escape(_describe_observation(o))}[/{color}]")


@app.command()
@_handle_errors
def scan(
    image: Path | None = typer.Option(None, "--image", help="Scan a photo instead of the camera."),
    camera: int | None = typer.Option(None, "--camera", help="Camera index (default: `benchlog camera use`, else 0)."),
    simulate: Path | None = typer.Option(None, "--simulate", help="Pretend the board looks like this circuit file."),
    sync: bool = typer.Option(False, "--sync", help="Record the board as matching the circuit; propose nothing."),
    debug: bool = typer.Option(False, "--debug", help="Explain what the camera saw and save images of it."),
) -> None:
    """Capture the board and propose changes since the last reviewed scan."""
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

    observations = project.scan(reading, sync=sync)
    if sync:
        print(f"board recorded as matching the circuit ({len(reading.occupied)} occupied holes)")
        return
    if not observations:
        print("no changes seen since the last reviewed scan")
        return
    n = len(observations)
    print(f"[bold]{n} change{'s' * (n != 1)} to review[/bold] ({escape(reading.source)})")
    _print_observations(observations)
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
    pending = Project.find().pending_observations()
    if not pending:
        print("nothing to review")
        return
    print(f"[bold]{len(pending)} pending[/bold]")
    _print_observations(pending)


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
) -> None:
    """Commit the current circuit (and any firmware paths given)."""
    project = Project.find()
    pending = project.pending_observations()
    if pending:
        print(f"[yellow]warning:[/yellow] {len(pending)} observation(s) still pending; committing the accepted circuit")
    before = project.baseline()
    c = project.commit(message, firmware=[p.resolve() for p in firmware])
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


@app.command()
def schema(output: Path | None = typer.Option(None, "-o", "--output", help="Write to a file instead of stdout.")) -> None:
    """Print the JSON Schema of the types shared with the web UI."""
    from benchlog.core.schema import json_schema

    text = json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"
    if output:
        output.write_text(text)
    else:
        typer.echo(text, nl=False)


@app.command()
def serve(port: int = 8000) -> None:
    """Start the local API for the web UI (needs the `server` extra)."""
    import uvicorn

    uvicorn.run("benchlog.server.app:app", host="127.0.0.1", port=port, reload=True)
