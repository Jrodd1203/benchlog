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

from benchlog.core.diff import CircuitDiff, describe
from benchlog.core.diff import diff as circuit_diff
from benchlog.core.models import Circuit
from benchlog.core.project import CIRCUIT_PATH, Project, ProjectError
from benchlog.core.repo import GitError

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


@app.command()
def scan() -> None:
    """Capture the board and propose observed changes."""
    _todo("scan")


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


@app.command()
def check() -> None:
    """Run circuit checks against the current circuit."""
    _todo("check")


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
