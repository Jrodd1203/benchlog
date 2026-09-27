"""`benchlog scan` checking the camera's proposals against the ESP32 serial agent, and `benchlog serial`."""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app
from benchlog.core.project import Project
from benchlog.core.repo import Repo
from benchlog.serial import service as service_module
from benchlog.serial.agent_client import AgentClient, PortBusy
from benchlog.serial.service import PortInfo
from benchlog.serial.service import find_esp32_port as REAL_FIND_ESP32_PORT  # before conftest stubs it

from test_agent_client import FakeAgent

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"
runner = CliRunner(env={"COLUMNS": "250"})


class Esp32(FakeAgent):
    """The fake agent, reading chosen states on chosen pins (everything else floats)."""

    def __init__(self, pins: dict[int, str]) -> None:
        super().__init__()
        self.pins = pins

    def respond(self, line: str) -> bytes:
        request_id, command, *args = line.split()
        if command == "HELLO":
            reply = {"id": int(request_id), "ok": True, "board": "esp32", "agent": "0.1.0", "pins": [4, 18, 21]}
        elif command == "PROBE":
            probed = [int(p) for p in args] or [4, 18, 21]
            reply = {"id": int(request_id), "ok": True, "pins": {str(p): self.pins.get(p, "floating") for p in probed}}
        else:
            return super().respond(line)
        return (json.dumps(reply) + "\r\n").encode()


def plug_in(monkeypatch: pytest.MonkeyPatch, esp32: FakeAgent | None) -> None:
    """Make any serial port open `esp32` (or report busy when None)."""

    def opener(port: str, baud: int) -> FakeAgent:
        if esp32 is None:
            raise PortBusy(f"another process is using the port {port}")
        return esp32

    monkeypatch.setattr(service_module, "AgentClient", lambda port: AgentClient(port, timeout=0.2, opener=opener))


def run(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    run("init")
    shutil.copy(EXAMPLES / "working.json", path / "benchlog" / "circuit.json")
    run("commit", "-m", "Working circuit")
    return Project.find()


@pytest.fixture
def ground_gpio18(tmp_path: Path) -> Path:
    """The working circuit plus a wire from GPIO18's row (strip 7R) to the GND rail."""
    data = json.loads((EXAMPLES / "working.json").read_text())
    data["wires"].append({"id": "wx", "a": "J7", "b": "R-30", "color": "black"})
    path = tmp_path / "grounded.json"
    path.write_text(json.dumps(data))
    return path


def test_esp32_confirms_the_camera(project: Project, ground_gpio18: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plug_in(monkeypatch, Esp32({18: "pulled_low"}))
    run("serial", "use", "/dev/fake")
    out = run("scan", "--simulate", str(ground_gpio18))
    assert "serial: ESP32 on /dev/fake" in out
    assert "added wire w7: J7, R-30" in out and "ESP32 confirms (GPIO18)" in out
    # Saved, so review shows it too.
    assert "ESP32 confirms (GPIO18)" in run("review")
    assert project.reconciliation().proposals[0].verdict == "confirmed"


def test_esp32_disagrees(project: Project, ground_gpio18: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plug_in(monkeypatch, Esp32({}))  # GPIO18 floats: the new wire isn't making contact
    run("serial", "use", "/dev/fake")
    out = run("scan", "--simulate", str(ground_gpio18))
    assert "ESP32 disagrees (GPIO18)" in out
    assert "GPIO18 should read pulled_low (tied to GND) but reads floating" in out


def test_busy_port_scans_camera_only(project: Project, ground_gpio18: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plug_in(monkeypatch, None)
    run("serial", "use", "/dev/fake")
    out = run("scan", "--simulate", str(ground_gpio18))
    assert "is busy (is `benchlog serve` connected to it?); camera only" in out
    assert "added wire w7" in out and "ESP32" not in out.split("added wire")[1]
    assert project.reconciliation().serial_checked is False


def test_no_port_chosen_or_no_serial(project: Project, ground_gpio18: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plug_in(monkeypatch, Esp32({18: "pulled_low"}))
    out = run("scan", "--simulate", str(ground_gpio18))
    assert "serial: no ESP32 found; camera only" in out  # said, never silent
    assert "ESP32" not in out.split("added wire")[1]  # but no verdicts
    run("review", "reject", "--all")
    run("serial", "use", "/dev/fake")
    run("scan", "--sync", "--simulate", str(EXAMPLES / "working.json"))
    assert "ESP32" not in run("scan", "--no-serial", "--simulate", str(ground_gpio18))


def test_serial_probe(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    plug_in(monkeypatch, Esp32({18: "pulled_low"}))
    assert "no port chosen" in runner.invoke(app, ["serial", "probe"]).output
    out = run("serial", "probe", "/dev/fake")
    assert "GPIO18: pulled_low" in out and "GPIO4: floating" in out and "0x76" in out


def test_api_scan_saves_the_verdicts(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from benchlog.server.app import app as api

    monkeypatch.setenv("BENCHLOG_PROJECT", str(project.repo.root))
    client = TestClient(api)
    assert client.get("/api/reconciliation").json() is None
    circuit = json.loads((EXAMPLES / "moved-wire.json").read_text())
    assert client.post("/api/scan", json={"simulate": circuit}).status_code == 200
    saved = client.get("/api/reconciliation").json()
    assert [p["observation_id"] for p in saved["proposals"]] == ["obs1"]


# ── Commit gate ───────────────────────────────────────────────────────────────


def commit_message(project: Project) -> str:
    return project.repo.run("log", "-1", "--format=%B")


def test_commit_passes_when_the_board_matches(project: Project, ground_gpio18: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plug_in(monkeypatch, Esp32({18: "pulled_low"}))
    run("serial", "use", "/dev/fake")
    shutil.copy(ground_gpio18, project.circuit_path)
    out = run("commit", "-m", "Ground GPIO18")
    assert "ESP32 check: passed" in out
    assert commit_message(project).strip().endswith("ESP32-Check: passed")
    assert project.history()[0].subject == "Ground GPIO18"


def test_commit_is_blocked_when_the_board_disagrees(project: Project, ground_gpio18: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plug_in(monkeypatch, Esp32({}))  # GPIO18 floats: the wire to GND isn't really there
    run("serial", "use", "/dev/fake")
    shutil.copy(ground_gpio18, project.circuit_path)
    head = project.repo.head()
    result = runner.invoke(app, ["commit", "-m", "Ground GPIO18"])
    assert result.exit_code == 1
    assert "ESP32 check: failed" in result.output and "GPIO18 should read pulled_low" in result.output
    assert "--force" in result.output
    assert project.repo.head() == head  # nothing committed

    out = run("commit", "-m", "Ground GPIO18", "--force")
    assert "ESP32 check: failed" in out
    assert commit_message(project).strip().endswith("ESP32-Check: failed (forced)")


def test_commit_without_an_esp32_is_skipped_not_blocked(project: Project, ground_gpio18: Path) -> None:
    shutil.copy(ground_gpio18, project.circuit_path)
    out = run("commit", "-m", "Ground GPIO18")
    assert "ESP32 check: skipped (no ESP32 found)" in out
    assert commit_message(project).strip().endswith("ESP32-Check: skipped")


def test_commit_auto_detects_the_esp32(project: Project, ground_gpio18: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plug_in(monkeypatch, Esp32({18: "pulled_low"}))
    monkeypatch.setattr(service_module, "find_esp32_port", lambda: "/dev/cu.usbserial-0001")
    shutil.copy(ground_gpio18, project.circuit_path)
    assert "ESP32 check: passed" in run("commit", "-m", "Ground GPIO18")


def test_nothing_to_commit_doesnt_probe(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_open(port: str):
        raise AssertionError("probed the ESP32 with nothing to commit")

    monkeypatch.setattr(service_module, "AgentClient", must_not_open)
    run("serial", "use", "/dev/fake")
    assert "nothing to commit" in runner.invoke(app, ["commit", "-m", "again"]).output


def test_find_esp32_port(monkeypatch: pytest.MonkeyPatch) -> None:
    ports = [
        PortInfo(device="/dev/cu.Bluetooth-Incoming-Port", description="n/a"),
        PortInfo(device="/dev/tty.usbserial-0001", description="CP2102 USB to UART"),
        PortInfo(device="/dev/cu.usbserial-0001", description="CP2102 USB to UART"),
    ]
    monkeypatch.setattr(service_module, "available_ports", lambda: ports)
    assert REAL_FIND_ESP32_PORT() == "/dev/cu.usbserial-0001"
    monkeypatch.setattr(service_module, "available_ports", lambda: ports[:1])
    assert REAL_FIND_ESP32_PORT() is None


def test_api_commit_is_gated(project: Project, ground_gpio18: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from benchlog.server.app import app as api

    monkeypatch.setenv("BENCHLOG_PROJECT", str(project.repo.root))
    client = TestClient(api)
    shutil.copy(ground_gpio18, project.circuit_path)
    # No agent connected in the server: the check is skipped, the commit goes through.
    body = client.post("/api/commit", json={"message": "Ground GPIO18"}).json()
    assert body["hardware"]["status"] == "skipped"
    assert commit_message(project).strip().endswith("ESP32-Check: skipped")


def test_cli_scan_saves_serial_results_into_the_circuit(
    project: Project, ground_gpio18: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plug_in(monkeypatch, Esp32({18: "pulled_low"}))
    run("serial", "use", "/dev/fake")
    run("scan", "--simulate", str(ground_gpio18))
    run("review", "accept", "--all")
    saved = project.load_circuit().serial
    assert saved is not None and saved.port == "/dev/fake"
    assert next(p for p in saved.pins if p.gpio == 18).verdict == "confirmed"


def test_cli_scan_without_an_esp32_saves_no_serial_results(project: Project, ground_gpio18: Path) -> None:
    run("scan", "--simulate", str(ground_gpio18))
    run("review", "accept", "--all")
    assert project.load_circuit().serial is None
