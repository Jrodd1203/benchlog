"""benchlog command line. Owner: Person 1. Commands are stubs until the core lands."""

import typer
from rich import print

app = typer.Typer(help="Version control for breadboard prototypes.", no_args_is_help=True)


def _todo(name: str) -> None:
    print(f"[yellow]benchlog {name}: not implemented yet[/yellow]")
    raise typer.Exit(1)


@app.command()
def init() -> None:
    """Create a benchlog project in the current git repository."""
    _todo("init")


@app.command()
def scan() -> None:
    """Capture the board and propose observed changes."""
    _todo("scan")


@app.command()
def status() -> None:
    """Show observed, accepted and committed circuit state."""
    _todo("status")


@app.command()
def diff() -> None:
    """Show circuit changes since the last commit."""
    _todo("diff")


@app.command()
def commit(message: str = typer.Option(..., "-m", "--message")) -> None:
    """Commit the current circuit (and staged firmware)."""
    _todo("commit")


@app.command()
def log() -> None:
    """Show circuit history."""
    _todo("log")


@app.command()
def check() -> None:
    """Run circuit checks against the current circuit."""
    _todo("check")


@app.command()
def serve(port: int = 8000) -> None:
    """Start the local API for the web UI (needs the `server` extra)."""
    import uvicorn

    uvicorn.run("benchlog.server.app:app", host="127.0.0.1", port=port, reload=True)
