import json
from pathlib import Path

from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.schema import SHARED_MODELS, json_schema


def test_schema_references_every_shared_model() -> None:
    schema = json_schema()
    assert set(schema["properties"]) == set(SHARED_MODELS)
    for prop in schema["properties"].values():
        assert prop["$ref"].removeprefix("#/$defs/") in schema["$defs"]
    assert {"Wire", "Component", "PlacementChange", "ConnectionChange", "Net"} <= set(schema["$defs"])


def test_schema_command_prints_json() -> None:
    result = CliRunner().invoke(app, ["schema"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["title"] == "Benchlog"


def test_schema_command_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "schema.json"
    result = CliRunner().invoke(app, ["schema", "-o", str(out)])
    assert result.exit_code == 0
    assert json.loads(out.read_text()) == json_schema()
