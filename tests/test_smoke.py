from typer.testing import CliRunner

from benchlog.cli.main import app


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
