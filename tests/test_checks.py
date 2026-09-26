import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchlog.cli.main import app as cli
from benchlog.core.checks import run_checks
from benchlog.core.checks.esp32 import STRAPPING_PINS, esp32_pins
from benchlog.core.checks.power import short
from benchlog.core.checks.report import CheckResult, overall
from benchlog.core.checks.serial import i2c_missing, serial_conflicts
from benchlog.core.checks.store import check_project, load_report
from benchlog.core.checks.wires import unconfirmed_wires
from benchlog.core.models import Circuit, Component, ComponentType, Observation, ObservationKind, Wire
from benchlog.core.project import Project
from benchlog.core.reconcile import reconcile
from benchlog.core.repo import Repo
from benchlog.core.serialize import dump, load_circuit

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"
ALL_FLOATING = {g: "floating" for g in (4, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33)}


def load(name: str) -> Circuit:
    return load_circuit(EXAMPLES / f"{name}.json")


def add(circuit: Circuit, *things: Wire | Component) -> Circuit:
    wires = [t for t in things if isinstance(t, Wire)]
    components = [t for t in things if isinstance(t, Component)]
    return Circuit.model_validate(
        circuit.model_copy(update={"wires": circuit.wires + wires, "components": circuit.components + components}).model_dump()
    )


def statuses(results: list[CheckResult]) -> list[str]:
    return [r.status for r in results]


def bme280(value: str | None = None) -> Component:
    return Component(
        id="bme", type=ComponentType.I2C_MODULE, model="BME280", value=value,
        pins={"VCC": "J30", "GND": "J31", "SDA": "J32", "SCL": "J33"},
    )  # fmt: skip


def new_wire(obs_id: str, wire_id: str, after: dict[str, str], uncertain: list[str] = ()) -> Observation:
    return Observation(
        id=obs_id, kind=ObservationKind.ADDED, object_type="wire", object_id=wire_id,
        after=after, confidence=0.6, uncertain_holes=list(uncertain),
    )  # fmt: skip


# ── short ─────────────────────────────────────────────────────────────────────


def test_short_passes_on_the_demo_circuit():
    assert statuses(short(load("working"))) == ["pass"]


def test_short_fails_when_rails_are_bridged():
    # R+ is 3V3 (w1) and R- is GND (w2): a wire between them shorts the supply.
    [result] = short(add(load("working"), Wire(id="w7", a="R+30", b="R-30")))
    assert result.status == "fail"
    assert result.message.startswith("3V3 is shorted to GND through")
    assert {"esp32.3V3", "esp32.GND2", "w7", "w1", "w2"} <= set(result.ids)


def test_short_needs_an_esp32():
    assert statuses(short(load("empty"))) == ["not_supported"]


# ── unconfirmed_wires ─────────────────────────────────────────────────────────


def test_unconfirmed_wires():
    assert statuses(unconfirmed_wires(None)) == ["not_supported"]
    assert statuses(unconfirmed_wires([])) == ["pass"]
    assert statuses(unconfirmed_wires([new_wire("obs1", "w7", {"a": "J7", "b": "R-7"})])) == ["pass"]

    results = unconfirmed_wires([
        new_wire("obs1", "w7", {"a": "J7", "b": "R-7"}, uncertain=["R-7"]),
        new_wire("obs2", "w8", {"a": "A40"}),
    ])  # fmt: skip
    assert statuses(results) == ["needs_confirmation", "needs_confirmation"]
    assert results[0].message == "w7: confirm hole R-7." and results[0].ids == ["w7", "obs1"]
    assert "only one end was seen" in results[1].message


# ── serial_conflicts ──────────────────────────────────────────────────────────


def test_serial_conflicts_without_serial_is_not_supported():
    circuit = load("working")
    assert statuses(serial_conflicts(circuit, None)) == ["not_supported"]
    assert statuses(serial_conflicts(circuit, reconcile(circuit, [], None, None))) == ["not_supported"]


def test_serial_conflicts_pass_when_readings_match():
    circuit = load("working")
    assert statuses(serial_conflicts(circuit, reconcile(circuit, [], ALL_FLOATING, []))) == ["pass"]


def test_serial_conflict_on_a_proposal_fails_with_its_message():
    circuit = load("working")
    rec = reconcile(circuit, [new_wire("obs1", "w7", {"a": "J7", "b": "R-7"})], ALL_FLOATING, [])
    [result] = serial_conflicts(circuit, rec)
    assert result.status == "fail" and result.ids == ["obs1"]
    assert result.message.startswith("w7 may not be seated")


def test_serial_conflict_without_a_proposal_names_the_pin():
    circuit = load("working")
    [result] = serial_conflicts(circuit, reconcile(circuit, [], ALL_FLOATING | {19: "pulled_low"}, []))
    assert (result.status, result.ids) == ("fail", ["esp32.GPIO19"])
    assert "doesn't see" in result.message


# ── i2c_missing ───────────────────────────────────────────────────────────────


def test_i2c_missing():
    plain = load("working")
    assert statuses(i2c_missing(plain, None)) == ["pass"]  # nothing declared

    circuit = add(plain, bme280())
    assert statuses(i2c_missing(circuit, None)) == ["not_supported"]
    assert statuses(i2c_missing(circuit, reconcile(circuit, [], ALL_FLOATING, ["0x76"]))) == ["pass"]

    [missing] = i2c_missing(circuit, reconcile(circuit, [], ALL_FLOATING, []))
    assert (missing.status, missing.ids) == ("fail", ["bme"])
    assert missing.message == "bme didn't answer at 0x76. Check its SDA/SCL wires and power."


def test_i2c_part_with_unknown_address_is_not_supported():
    mystery = Component(id="x", type=ComponentType.I2C_MODULE, model="Mystery", pins={"SDA": "J32", "SCL": "J33"})
    circuit = add(load("working"), mystery)
    assert statuses(i2c_missing(circuit, reconcile(circuit, [], ALL_FLOATING, ["0x40"]))) == ["not_supported"]


# ── esp32_pins ────────────────────────────────────────────────────────────────


def test_strapping_pins():
    assert STRAPPING_PINS == {0, 2, 5, 12, 15}


def test_esp32_pins_pass_on_the_demo_circuit():
    # The pot wiper on input-only GPIO34 is fine: nothing needs the pin to drive it.
    assert statuses(esp32_pins(load("working"))) == ["pass"]


def test_gpio12_fails():
    [result] = esp32_pins(load("moved-wire"))
    assert result.status == "fail"
    assert result.message.startswith("GPIO12 held high at boot can stop the ESP32 from starting")
    assert result.ids == ["esp32.GPIO12", "w5", "pot1.wiper"]


@pytest.mark.parametrize(("gpio", "hole"), [(2, "J12"), (5, "J8"), (15, "J13")])
def test_other_strapping_pins_need_confirmation(gpio: int, hole: str):
    [result] = esp32_pins(add(load("working"), Wire(id="w7", a=hole, b="J40")))
    assert (result.status, result.ids[0]) == ("needs_confirmation", f"esp32.GPIO{gpio}")
    assert "strapping pin" in result.message


def test_flash_pin_fails():
    working = load("working")
    esp32 = next(c for c in working.components if c.id == "esp32")
    board = Component(**{**esp32.model_dump(), "pins": {**esp32.pins, "GPIO6": "A40"}})
    circuit = Circuit.model_validate({**working.model_dump(), "components": [board, *working.components[1:]]})
    [result] = esp32_pins(add(circuit, Wire(id="w7", a="C40", b="C45")))
    assert result.status == "fail" and "flash" in result.message


def test_input_only_pin_driving_an_led_fails():
    # GPIO35 (B5) wired straight to the LED's row.
    [result] = esp32_pins(add(load("working"), Wire(id="w7", a="A5", b="C18")))
    assert result.status == "fail"
    assert "GPIO35 is input-only, so it can't drive" in result.message and "led1.anode" in result.ids


def test_input_only_pin_driving_an_led_through_a_resistor_fails():
    circuit = add(
        load("working"),
        Wire(id="w7", a="A5", b="A30"),
        Component(id="r2", type=ComponentType.RESISTOR, value="220Ω", pins={"1": "B30", "2": "B35"}),
        Component(id="led2", type=ComponentType.LED, pins={"anode": "C35", "cathode": "F35"}),
    )
    [result] = esp32_pins(circuit)
    assert result.status == "fail" and "led2.anode" in result.ids


def test_esp32_pins_need_an_esp32():
    assert statuses(esp32_pins(load("empty"))) == ["not_supported"]


# ── Report ────────────────────────────────────────────────────────────────────


def result(status: str) -> CheckResult:
    return CheckResult(check="x", status=status, message="")


def test_overall_status():
    assert overall([result("pass"), result("not_supported")]) == "pass"
    assert overall([result("pass"), result("needs_confirmation")]) == "needs_confirmation"
    assert overall([result("needs_confirmation"), result("fail")]) == "fail"


def test_run_checks_runs_everything():
    report = run_checks(load("moved-wire"), commit="abc123")
    assert report.status == "fail" and report.commit == "abc123"
    assert [r.check for r in report.results] == ["short", "unconfirmed_wires", "serial_conflicts", "i2c_missing", "esp32_pins"]
    # Missing data is reported, never counted as a pass.
    assert {r.check for r in report.not_supported} == {"unconfirmed_wires", "serial_conflicts"}


# ── Project, CLI, API ─────────────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    project, _ = Project.init(path)
    shutil.copy(EXAMPLES / "working.json", project.circuit_path)
    project.commit("Working circuit")
    monkeypatch.chdir(path)
    monkeypatch.setenv("BENCHLOG_PROJECT", str(path))
    return project


def test_report_is_saved_for_a_clean_commit(project: Project):
    report = check_project(project)
    assert report.commit == project.repo.head()
    assert load_report(project, report.commit) == report


def test_report_is_not_saved_for_uncommitted_changes(project: Project):
    dump(load("moved-wire"), project.circuit_path)
    report = check_project(project)
    assert report.commit is None and report.status == "fail"
    assert load_report(project, project.repo.head()) is None


def test_cli_check(project: Project):
    runner = CliRunner(env={"COLUMNS": "200"})
    ok = runner.invoke(cli, ["check"])
    assert ok.exit_code == 0, ok.output
    assert "pass on commit" in ok.output and "not checked (missing data): serial_conflicts" in ok.output

    dump(load("moved-wire"), project.circuit_path)
    failed = runner.invoke(cli, ["check"])
    assert failed.exit_code == 1
    assert "GPIO12 held high at boot" in failed.output and "uncommitted changes" in failed.output


def test_cli_commit_saves_a_report_even_when_checks_fail(project: Project):
    dump(load("moved-wire"), project.circuit_path)
    result = CliRunner(env={"COLUMNS": "200"}).invoke(cli, ["commit", "-m", "Move pot to GPIO12"])
    assert result.exit_code == 0, result.output
    assert "checks: fail (1 failing)" in result.output
    assert load_report(project, project.repo.head()).status == "fail"


def test_routes(project: Project):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from benchlog.server import check_routes

    api = FastAPI()
    api.include_router(check_routes.router)
    client = TestClient(api)

    assert client.get("/api/checks").status_code == 404  # nothing saved yet
    report = client.post("/api/checks/run").json()
    assert report["status"] == "pass" and report["commit"] == project.repo.head()
    serial = next(r for r in report["results"] if r["check"] == "serial_conflicts")
    assert serial["status"] == "not_supported"  # no ESP32 connected
    assert client.get("/api/checks", params={"commit": project.repo.head()[:7]}).json() == report
