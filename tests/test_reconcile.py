from pathlib import Path

from benchlog.core.models import Circuit, Component, ComponentType, Observation, ObservationKind, Wire
from benchlog.core.reconcile import reconcile
from benchlog.core.serialize import load_circuit

EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"

# In working.json GPIO18 is in I7 (strip 7R); R+ is 3V3 (w1) and R- is GND (w2).
ALL_FLOATING = {g: "floating" for g in (4, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33)}


def load(name: str) -> Circuit:
    return load_circuit(EXAMPLES / f"{name}.json")


def with_wires(circuit: Circuit, *wires: Wire) -> Circuit:
    return circuit.model_copy(update={"wires": [*circuit.wires, *wires]})


def added(obs_id: str, wire_id: str, a: str, b: str | None = None) -> Observation:
    after = {"a": a, "b": b} if b else {"a": a}
    return Observation(id=obs_id, kind=ObservationKind.ADDED, object_type="wire", object_id=wire_id, after=after, confidence=1)


def removed(obs_id: str, wire: Wire) -> Observation:
    before = {"a": wire.a, "b": wire.b}
    return Observation(id=obs_id, kind=ObservationKind.REMOVED, object_type="wire", object_id=wire.id, before=before, confidence=1)


def moved(obs_id: str, wire: Wire, **ends: str) -> Observation:
    before = {"a": wire.a, "b": wire.b}
    return Observation(
        id=obs_id, kind=ObservationKind.MOVED, object_type="wire", object_id=wire.id,
        before=before, after={**before, **ends}, confidence=1,
    )  # fmt: skip


def pins(**readings: str) -> dict[int, str]:
    return ALL_FLOATING | {int(k.removeprefix("gpio")): v for k, v in readings.items()}


def verdict_of(result, obs_id: str):
    return next(v for v in result.proposals if v.observation_id == obs_id)


def pin(result, gpio: int):
    return next(c for c in result.pins if c.gpio == gpio)


# ── Proposals ─────────────────────────────────────────────────────────────────


def test_wire_to_gnd_confirmed_when_pin_reads_low():
    result = reconcile(load("working"), [added("obs1", "w7", "J7", "R-7")], pins(gpio18="pulled_low"), [])
    v = verdict_of(result, "obs1")
    assert (v.verdict, v.gpios, v.message) == ("confirmed", [18], None)
    assert pin(result, 18).expected == "pulled_low" and pin(result, 18).reason == "tied to GND"
    assert result.warnings == []


def test_wire_to_gnd_conflict_when_pin_floats():
    result = reconcile(load("working"), [added("obs1", "w7", "J7", "R-7")], pins(), [])
    v = verdict_of(result, "obs1")
    assert v.verdict == "conflict"
    assert v.message.startswith("w7 may not be seated: GPIO18 should read pulled_low (tied to GND) but reads floating")
    assert "Press it in and rescan." in v.message
    assert result.warnings == [v.message]  # reported once, not again as a standalone warning


def test_wire_to_3v3_is_expected_high():
    result = reconcile(load("working"), [added("obs1", "w7", "J7", "R+7")], pins(gpio18="pulled_high"), [])
    assert verdict_of(result, "obs1").verdict == "confirmed"


def test_wrong_rail_conflict():
    result = reconcile(load("working"), [added("obs1", "w7", "J7", "R+7")], pins(gpio18="pulled_low"), [])
    message = verdict_of(result, "obs1").message
    assert "GPIO18 reads pulled_low but should read pulled_high (tied to 3V3)" in message
    assert "w7 is on the right rail" in message


def test_removed_wire_expects_floating():
    gnd = Wire(id="w7", a="J7", b="R-7")
    circuit = with_wires(load("working"), gnd)
    assert verdict_of(reconcile(circuit, [removed("obs1", gnd)], pins(), []), "obs1").verdict == "confirmed"

    result = reconcile(circuit, [removed("obs1", gnd)], pins(gpio18="pulled_low"), [])
    v = verdict_of(result, "obs1")
    assert v.verdict == "conflict"
    assert "GPIO18 reads pulled_low, but with w7 as proposed nothing should drive it" in v.message


def test_moving_a_wire_along_the_rail_is_still_checked():
    # w7's end moves from R-7 to R-10: GPIO18 is still grounded, and w7 is the wire that does it.
    gnd = Wire(id="w7", a="J7", b="R-7")
    circuit = with_wires(load("working"), gnd)
    result = reconcile(circuit, [moved("obs1", gnd, b="R-10")], pins(gpio18="pulled_low"), [])
    assert (verdict_of(result, "obs1").verdict, verdict_of(result, "obs1").gpios) == ("confirmed", [18])


def test_unrelated_proposal_is_not_blamed():
    # GPIO18 is grounded by w7, but it floats. Moving the LED's ground wire (w6) along the same
    # rail doesn't change what GPIO18 should read, so w6 isn't blamed; the warning stands alone.
    circuit = with_wires(load("working"), Wire(id="w7", a="J7", b="R-7"))
    w6 = next(w for w in circuit.wires if w.id == "w6")
    result = reconcile(circuit, [moved("obs1", w6, b="R-19")], pins(), [])
    v = verdict_of(result, "obs1")
    assert (v.verdict, v.gpios) == ("no_expectation", [])
    assert result.warnings == [
        "GPIO18 should read pulled_low (tied to GND) but reads floating. A wire may have come loose; press it in and rescan."
    ]


def test_wire_to_a_component_has_no_expectation():
    # GPIO18 to the pot's wiper row: the reading depends on the knob.
    result = reconcile(load("working"), [added("obs1", "w7", "J7", "J21")], pins(gpio18="pulled_high"), [])
    v = verdict_of(result, "obs1")
    assert (v.verdict, v.gpios) == ("no_expectation", [18])
    assert pin(result, 18).reason == "connected to esp32.GPIO34, pot1.wiper"  # w5 also goes there


def test_unstable_reading_has_no_expectation():
    result = reconcile(load("working"), [added("obs1", "w7", "J7", "R-7")], pins(gpio18="unstable"), [])
    assert verdict_of(result, "obs1").verdict == "no_expectation"


def test_demo_move_is_not_checked():
    # w5 moves from GPIO34 to GPIO12; the agent can't probe either pin.
    circuit = load("working")
    w5 = next(w for w in circuit.wires if w.id == "w5")
    result = reconcile(circuit, [moved("obs1", w5, b="A12")], ALL_FLOATING, [])
    v = verdict_of(result, "obs1")
    assert (v.verdict, v.gpios) == ("not_checked", [12, 34])
    assert pin(result, 34).expected == "floating" and pin(result, 34).actual is None
    assert result.warnings == []


def test_proposal_that_cant_be_applied_is_not_checked():
    result = reconcile(load("working"), [added("obs1", "w7", "J7")], pins(), [])
    assert verdict_of(result, "obs1").verdict == "not_checked"


def test_unsafe_pin_is_not_checked():
    circuit = with_wires(load("working"), Wire(id="w7", a="J7", b="R-7"))
    result = reconcile(circuit, [], {18: "unsafe"}, [])
    assert pin(result, 18).verdict == "not_checked"


def test_every_proposal_gets_a_verdict():
    circuit = load("working")
    w6 = next(w for w in circuit.wires if w.id == "w6")
    obs = [added("obs1", "w7", "J7", "R-7"), moved("obs2", w6, b="R-19"), added("obs3", "w8", "A40")]
    result = reconcile(circuit, obs, pins(), [])
    assert [v.observation_id for v in result.proposals] == ["obs1", "obs2", "obs3"]


# ── Without proposals ─────────────────────────────────────────────────────────


def test_something_the_camera_doesnt_see():
    result = reconcile(load("working"), [], pins(gpio19="pulled_low"), [])
    assert pin(result, 19).verdict == "conflict"
    assert result.warnings == ["Something is connected to GPIO19 that the camera doesn't see (it reads pulled_low)."]


def test_matching_board_has_no_warnings():
    result = reconcile(load("working"), [], ALL_FLOATING, [])
    assert result.warnings == []
    assert pin(result, 18).verdict == "confirmed"
    assert pin(result, 13).verdict == "no_expectation"  # through r1 to the LED


def test_no_snapshot_means_not_checked():
    result = reconcile(load("working"), [added("obs1", "w7", "J7", "R-7")], None, None)
    assert verdict_of(result, "obs1").verdict == "not_checked"
    assert {c.verdict for c in result.pins} == {"not_checked"}
    assert result.i2c == [] and result.warnings == []


def test_no_esp32_in_circuit():
    result = reconcile(load("empty"), [added("obs1", "w1", "A1", "A5")], ALL_FLOATING, [])
    assert verdict_of(result, "obs1").verdict == "not_checked"
    assert result.pins == []


# ── I2C ───────────────────────────────────────────────────────────────────────


def with_bme280(circuit: Circuit, value: str | None = None) -> Circuit:
    bme = Component(
        id="bme", type=ComponentType.I2C_MODULE, model="BME280 breakout", value=value,
        pins={"VCC": "J30", "GND": "J31", "SDA": "J32", "SCL": "J33"},
    )  # fmt: skip
    return circuit.model_copy(update={"components": [*circuit.components, bme]})


def test_i2c_confirmed_missing_unexpected():
    circuit = with_bme280(load("working"))
    assert [c.model_dump() for c in reconcile(circuit, [], ALL_FLOATING, ["0x77"]).i2c] == [
        {"address": "0x77", "component": "bme", "status": "confirmed"}
    ]
    assert [c.status for c in reconcile(circuit, [], ALL_FLOATING, []).i2c] == ["missing"]
    unexpected = reconcile(load("working"), [], ALL_FLOATING, ["0x3c"]).i2c
    assert [c.model_dump() for c in unexpected] == [{"address": "0x3c", "component": None, "status": "unexpected"}]


def test_i2c_address_from_value():
    circuit = with_bme280(load("working"), value="0x77")
    assert [c.status for c in reconcile(circuit, [], ALL_FLOATING, ["0x76"]).i2c] == ["missing", "unexpected"]


def test_sda_scl_expected_high_with_declared_part():
    circuit = with_bme280(load("working"))
    obs = [added("obs1", "w7", "J5", "I32"), added("obs2", "w8", "J2", "I33")]  # GPIO21 -> SDA, GPIO22 -> SCL
    result = reconcile(circuit, obs, pins(gpio21="pulled_high", gpio22="pulled_high"), ["0x76"])
    assert [v.verdict for v in result.proposals] == ["confirmed", "confirmed"]
    assert pin(result, 21).reason == "I2C line with the module's pull-ups"
