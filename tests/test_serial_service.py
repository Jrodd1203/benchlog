import threading

import pytest

from benchlog.serial.agent_client import AgentClient, AgentTimeout, PortBusy
from benchlog.serial.service import SerialService

from test_agent_client import FakeAgent


class Board:
    """Hands out FakeAgents and remembers them, standing in for a USB port."""

    def __init__(self) -> None:
        self.agents: list[FakeAgent] = []
        self.busy = False

    def client(self, port: str) -> AgentClient:
        def opener(port: str, baud: int) -> FakeAgent:
            if self.busy:
                raise PortBusy(f"another process is using the port {port}")
            self.agents.append(FakeAgent())
            return self.agents[-1]

        return AgentClient(port, timeout=0.05, opener=opener)

    @property
    def agent(self) -> FakeAgent:
        return self.agents[-1]


@pytest.fixture
def board() -> Board:
    return Board()


@pytest.fixture
def service(board: Board):
    service = SerialService(client_factory=board.client, ping_interval=None)
    yield service
    service.close()


def test_status_before_connect(service: SerialService):
    status = service.status()
    assert not status.connected and status.port is None
    assert service.snapshot() is None


def test_connect_reports_status(service: SerialService):
    status = service.connect("/dev/fake")
    assert status.connected
    assert (status.port, status.agent, status.pins) == ("/dev/fake", "0.1.0", [4, 18, 21])
    assert status.last_seen is not None


def test_connect_error_is_raised_and_remembered(service: SerialService, board: Board):
    board.busy = True
    with pytest.raises(PortBusy):
        service.connect("/dev/fake")
    status = service.status()
    assert not status.connected
    assert "another process" in status.last_error


def test_reconnect_closes_the_old_port(service: SerialService, board: Board):
    service.connect("/dev/fake")
    first = board.agent
    service.connect("/dev/fake")
    assert first.closed and not board.agent.closed


def test_snapshot_probes_then_scans_i2c(service: SerialService, board: Board):
    service.connect("/dev/fake")
    snapshot = service.snapshot()
    assert snapshot.probe.pins == {4: "floating", 18: "floating", 21: "floating"}
    assert snapshot.i2c.devices == ["0x76"]
    assert (snapshot.port, snapshot.agent) == ("/dev/fake", "0.1.0")
    assert [line.split()[1] for line in board.agent.sent] == ["HELLO", "PROBE", "I2C"]


def test_snapshot_is_none_when_unplugged(service: SerialService, board: Board):
    service.connect("/dev/fake")
    board.agent.unplugged = True
    assert service.snapshot() is None
    status = service.status()
    assert not status.connected and "lost the agent" in status.last_error
    assert service.snapshot() is None  # stays quiet until the next connect


def test_snapshot_is_none_on_timeout(service: SerialService, board: Board, monkeypatch: pytest.MonkeyPatch):
    service.connect("/dev/fake")
    monkeypatch.setattr(AgentClient, "i2c", lambda self: (_ for _ in ()).throw(AgentTimeout("no reply to I2C")))
    assert service.snapshot() is None
    assert service.status().last_error == "no reply to I2C"


def test_ping_updates_status(service: SerialService, board: Board):
    service.connect("/dev/fake")
    board.agent.unplugged = True
    assert not service.ping()
    assert not service.status().connected


def test_background_ping_only_pings(board: Board):
    service = SerialService(client_factory=board.client, ping_interval=0.01)
    service.connect("/dev/fake")
    pinged = threading.Event()
    agent = board.agent
    normal = agent.respond

    def watch(line: str) -> bytes:
        pinged.set()
        return normal(line)

    agent.respond = watch
    assert pinged.wait(2)
    service.close()
    commands = {line.split()[1] for line in agent.sent}
    assert commands == {"HELLO", "PING"}
    assert agent.closed


# ── Routes ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(service: SerialService):
    pytest.importorskip("fastapi")  # the `server` extra
    pytest.importorskip("httpx")  # needed by TestClient
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from benchlog.server import serial_routes

    app = FastAPI()
    app.include_router(serial_routes.router)
    app.state.serial = service
    return TestClient(app)


def test_routes_connect_status_probe(client, board: Board):
    assert client.get("/api/serial/status").json()["connected"] is False
    assert client.post("/api/serial/probe").status_code == 503

    response = client.post("/api/serial/connect", json={"port": "/dev/fake"})
    assert response.status_code == 200 and response.json()["agent"] == "0.1.0"
    assert client.get("/api/serial/status").json()["connected"] is True

    snapshot = client.post("/api/serial/probe").json()
    assert snapshot["probe"]["pins"] == {"4": "floating", "18": "floating", "21": "floating"}
    assert snapshot["i2c"]["devices"] == ["0x76"]


def test_route_connect_busy_port_is_400(client, board: Board):
    board.busy = True
    response = client.post("/api/serial/connect", json={"port": "/dev/fake"})
    assert response.status_code == 400
    assert "another process" in response.json()["detail"]


def test_route_ports(client, monkeypatch: pytest.MonkeyPatch):
    from benchlog.serial.service import PortInfo
    from benchlog.server import serial_routes

    monkeypatch.setattr(serial_routes, "available_ports", lambda: [PortInfo(device="/dev/cu.x", description="CP2102")])
    assert client.get("/api/serial/ports").json() == [{"device": "/dev/cu.x", "description": "CP2102"}]


def test_scan_includes_serial_verdicts(tmp_path, monkeypatch: pytest.MonkeyPatch, service: SerialService, board: Board):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import json
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from benchlog.core.project import Project
    from benchlog.core.repo import Repo
    from benchlog.server.app import app

    examples = Path(__file__).parent.parent / "examples" / "circuits"
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    project, _ = Project.init(path)
    shutil.copy(examples / "working.json", project.circuit_path)
    project.commit("Working circuit")
    monkeypatch.setenv("BENCHLOG_PROJECT", str(path))

    # The camera sees a new wire from GPIO18 (I7's row) to the GND rail, but the fake agent
    # reads GPIO18 as floating, so the wire is flagged as maybe not seated.
    service.connect("/dev/fake")
    simulated = json.loads((examples / "working.json").read_text())
    simulated["wires"].append({"id": "w7", "a": "J7", "b": "R-7"})
    app.state.serial = service
    try:
        data = TestClient(app).post("/api/scan", json={"simulate": simulated}).json()
    finally:
        app.state.serial = None
    assert data["serial"]["probe"]["pins"]["18"] == "floating"
    [obs] = data["observations"]
    [verdict] = data["reconciliation"]["proposals"]
    assert verdict["observation_id"] == obs["id"] and verdict["verdict"] == "conflict"
    assert "may not be seated" in data["reconciliation"]["warnings"][0]


# ── Auto-connect for scans and commits (the web app never has to press Connect) ──


@pytest.fixture
def api_project(tmp_path, monkeypatch: pytest.MonkeyPatch, service: SerialService):
    """A committed working circuit, served by the real app with `service` as its serial agent."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from benchlog.core.project import Project
    from benchlog.core.repo import Repo
    from benchlog.server.app import app

    examples = Path(__file__).parent.parent / "examples" / "circuits"
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    project, _ = Project.init(path)
    shutil.copy(examples / "working.json", project.circuit_path)
    project.commit("Working circuit")
    monkeypatch.setenv("BENCHLOG_PROJECT", str(path))
    app.state.serial, app.state.serial_failed = service, {}
    yield project, TestClient(app)
    app.state.serial, app.state.serial_failed = None, {}


def _scan(client) -> dict:
    import json
    from pathlib import Path

    circuit = json.loads((Path(__file__).parent.parent / "examples" / "circuits" / "working.json").read_text())
    return client.post("/api/scan", json={"simulate": circuit}).json()


def test_scan_connects_to_an_auto_detected_esp32(api_project, service: SerialService, monkeypatch: pytest.MonkeyPatch):
    from benchlog.serial import service as service_module

    _, client = api_project
    monkeypatch.setattr(service_module, "find_esp32_port", lambda: "/dev/cu.usbserial-0001")
    data = _scan(client)
    assert data["reconciliation"]["serial_checked"] is True
    assert service.status().connected and service.status().port == "/dev/cu.usbserial-0001"


def test_scan_prefers_the_saved_port(api_project, service: SerialService, monkeypatch: pytest.MonkeyPatch):
    from benchlog.serial import service as service_module

    project, client = api_project
    project.set_config("serial_port", "/dev/saved")
    monkeypatch.setattr(service_module, "find_esp32_port", lambda: "/dev/other")
    _scan(client)
    assert service.status().port == "/dev/saved"


def test_scan_without_an_esp32_stays_camera_only(api_project, service: SerialService):
    _, client = api_project  # conftest: no ESP32 is auto-detected
    data = _scan(client)
    assert data["serial"] is None and data["reconciliation"]["serial_checked"] is False
    assert not service.status().connected


def test_failed_auto_connect_isnt_retried_on_every_scan(
    api_project, service: SerialService, board: Board, monkeypatch: pytest.MonkeyPatch
):
    from benchlog.serial import service as service_module

    _, client = api_project
    monkeypatch.setattr(service_module, "find_esp32_port", lambda: "/dev/busy")
    board.busy = True
    attempts = []
    real_client = board.client
    monkeypatch.setattr(board, "client", lambda port: attempts.append(port) or real_client(port))
    service._client_factory = board.client

    assert _scan(client)["serial"] is None
    assert _scan(client)["serial"] is None
    assert attempts == ["/dev/busy"]  # the second scan didn't wait on the busy port again
    assert "another process" in client.get("/api/serial/status").json()["last_error"]


def test_status_light_connects_when_an_esp32_is_plugged_in(api_project, service: SerialService, monkeypatch: pytest.MonkeyPatch):
    from benchlog.serial import service as service_module

    _, client = api_project
    assert client.get("/api/serial/status").json()["connected"] is False  # nothing plugged in
    monkeypatch.setattr(service_module, "find_esp32_port", lambda: "/dev/cu.usbserial-0001")
    status = client.get("/api/serial/status").json()
    assert status["connected"] is True and status["port"] == "/dev/cu.usbserial-0001"


def test_status_works_outside_a_project(service: SerialService, monkeypatch: pytest.MonkeyPatch, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from benchlog.server.app import app

    monkeypatch.delenv("BENCHLOG_PROJECT", raising=False)
    monkeypatch.chdir(tmp_path)  # not a project: the light still answers
    app.state.serial, app.state.serial_failed = service, {}
    try:
        assert TestClient(app).get("/api/serial/status").json()["connected"] is False
    finally:
        app.state.serial = None
