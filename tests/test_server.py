import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")  # the `server` extra; CI installs only core + dev

from fastapi.testclient import TestClient  # noqa: E402

from benchlog.core.models import Circuit  # noqa: E402
from benchlog.core.project import Project  # noqa: E402
from benchlog.core.repo import Repo  # noqa: E402
from benchlog.server.app import app  # noqa: E402

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"
client = TestClient(app)


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text())


def same_circuit(data: dict, name: str) -> bool:
    return Circuit.model_validate(data) == Circuit.model_validate(example(name))


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    project, _ = Project.init(path)
    shutil.copy(EXAMPLES / "working.json", project.circuit_path)
    project.commit("Working circuit")
    monkeypatch.setenv("BENCHLOG_PROJECT", str(path))
    return project


def ok(response) -> dict | list:
    assert response.status_code == 200, response.text
    return response.json()


def test_health() -> None:
    assert ok(client.get("/api/health")) == {"status": "ok"}


def test_not_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHLOG_PROJECT", str(tmp_path))
    r = client.get("/api/status")
    assert r.status_code == 400 and "benchlog init" in r.json()["detail"]


def test_status_and_circuit(project: Project) -> None:
    status = ok(client.get("/api/status"))
    assert status["committed"] and status["pending"] == 0
    assert status["head"]["subject"] == "Working circuit"
    assert status["changes"]["lines"] == []
    assert same_circuit(ok(client.get("/api/circuit")), "working")


def test_demo_loop_over_the_api(project: Project) -> None:
    scan = ok(client.post("/api/scan", json={"simulate": example("moved-wire")}))
    [obs] = scan["observations"]
    assert (obs["id"], obs["kind"], obs["object_id"], obs["after"]) == ("obs1", "moved", "w5", {"a": "F21", "b": "A12"})
    assert ok(client.get("/api/status"))["pending"] == 1
    assert [o["id"] for o in ok(client.get("/api/observations", params={"pending_only": True}))] == ["obs1"]

    accepted = ok(client.post("/api/observations/accept", json={"ids": ["obs1"]}))
    assert same_circuit(accepted["circuit"], "moved-wire")
    assert "w5 moved: b A4 → A12" in accepted["changes"]["lines"]

    diff = ok(client.get("/api/diff"))
    assert diff["lines"][:2] == ["esp32.GPIO12 connected to pot1.wiper", "esp32.GPIO34 disconnected from pot1.wiper"]

    committed = ok(client.post("/api/commit", json={"message": "Move sensor to GPIO 12"}))
    assert committed["commit"]["subject"] == "Move sensor to GPIO 12"

    history = ok(client.get("/api/history"))
    assert [h["commit"]["subject"] for h in history] == ["Move sensor to GPIO 12", "Working circuit"]
    assert "w5 moved: b A4 → A12" in history[0]["lines"]
    old_sha = history[1]["commit"]["sha"]
    assert same_circuit(ok(client.get(f"/api/circuit/{old_sha}")), "working")
    assert ok(client.get("/api/diff", params={"old": old_sha, "new": "HEAD"}))["lines"][-1] == "w5 moved: b A4 → A12"


def test_manual_edit_via_put(project: Project) -> None:
    changes = ok(client.put("/api/circuit", json=example("moved-wire")))
    assert changes["lines"][-1] == "w5 moved: b A4 → A12"
    assert project.load_circuit() == Circuit.model_validate(example("moved-wire"))


def test_put_rejects_invalid_circuit(project: Project) -> None:
    bad = example("working")
    bad["wires"][0]["a"] = "Z99"
    assert client.put("/api/circuit", json=bad).status_code == 422
    assert same_circuit(ok(client.get("/api/circuit")), "working")


def test_reject_and_edit(project: Project) -> None:
    ok(client.post("/api/scan", json={"simulate": example("moved-wire")}))
    edited = ok(client.patch("/api/observations/obs1", json={"ends": {"b": "a13"}}))
    assert edited["after"] == {"a": "F21", "b": "A13"}
    assert ok(client.post("/api/observations/reject", json={"ids": None}))[0]["status"] == "rejected"
    r = client.post("/api/observations/accept", json={"ids": ["obs1"]})
    assert r.status_code == 400 and "no pending observation" in r.json()["detail"]


def test_scan_sync_and_errors(project: Project) -> None:
    assert ok(client.post("/api/scan", json={"simulate": example("working"), "sync": True}))["observations"] == []
    assert client.get("/api/circuit/nope").status_code == 400
    r = client.post("/api/commit", json={"message": "nothing"})
    assert r.status_code == 400 and "nothing to commit" in r.json()["detail"]


def test_netlist(project: Project) -> None:
    nets = ok(client.get("/api/netlist"))["nets"]
    assert any(set(n["pins"]) == {"esp32.GPIO34", "pot1.wiper"} for n in nets)
