"""Check a scan's camera proposals against what the ESP32 serial agent senses.

The camera is the only source of changes: this module never invents or moves holes. It works out
what each ESP32 GPIO *should* read if the proposals are right (holes -> nets -> GPIO -> expected
reading), compares that with what the serial agent actually read, and attaches a verdict to each
proposal. Conflicts become warnings; nothing is ever blocked.

Serial data comes in as plain values (pin readings and I2C addresses) so core doesn't depend on
the serial code.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from benchlog.core.board import TEMPLATES
from benchlog.core.board_state import fingerprint
from benchlog.core.models import Circuit, ComponentType, Observation, SerialI2cResult, SerialPinResult, SerialRecord
from benchlog.core.netlist import Netlist, netlist
from benchlog.core.pairing import apply_observations

Reading = Literal["floating", "pulled_low", "pulled_high"]
Verdict = Literal["confirmed", "conflict", "no_expectation", "not_checked"]

# The serial agent scans I2C on these pins (see agent/src/main.cpp).
I2C_SDA = 21
I2C_SCL = 22

# Default addresses for common I2C parts, matched against a component's `model`.
KNOWN_I2C_ADDRESSES = {
    "bme280": ["0x76", "0x77"],
    "bmp280": ["0x76", "0x77"],
    "ssd1306": ["0x3c", "0x3d"],
    "mpu6050": ["0x68", "0x69"],
    "ads1115": ["0x48", "0x49", "0x4a", "0x4b"],
    "aht20": ["0x38"],
    "bh1750": ["0x23", "0x5c"],
}

_GPIO_RE = re.compile(r"^GPIO(\d+)$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{2}$")


class PinCheck(BaseModel):
    gpio: int
    expected: Reading | None = Field(description="What the circuit (with proposals applied) implies; null if it can't say.")
    actual: str | None = Field(description="What the serial agent read; null if the pin wasn't probed.")
    verdict: Verdict
    reason: str = Field(description="Why this GPIO is expected to read that way, e.g. 'tied to GND'.")
    message: str | None = Field(default=None, description="Warning for a conflict no proposal explains.")


class ProposalVerdict(BaseModel):
    observation_id: str
    verdict: Verdict
    gpios: list[int] = Field(default=[], description="ESP32 GPIOs this proposal affects.")
    message: str | None = Field(default=None, description="What to do about a conflict.")


class I2cCheck(BaseModel):
    address: str
    component: str | None = Field(description="Declared part at this address; null for undeclared devices.")
    status: Literal["confirmed", "missing", "unexpected"]


class Reconciliation(BaseModel):
    proposals: list[ProposalVerdict]
    pins: list[PinCheck]
    i2c: list[I2cCheck]
    warnings: list[str] = Field(description="Every conflict, in plain English. Informational: nothing is blocked.")
    serial_checked: bool = Field(default=False, description="False when there was no serial snapshot to compare with.")


# ── Expectations ──────────────────────────────────────────────────────────────


class _Expectation(BaseModel):
    reading: Reading | None
    reason: str


def _esp32_id(circuit: Circuit) -> str | None:
    return next((c.id for c in circuit.components if c.type == ComponentType.ESP32_DEVKIT_V1_30), None)


def _gpios(circuit: Circuit, esp32: str) -> dict[int, str]:
    """GPIO number -> the hole its pin sits in."""
    component = next(c for c in circuit.components if c.id == esp32)
    return {int(m[1]): hole for pin, hole in component.pins.items() if (m := _GPIO_RE.match(pin))}


def _i2c_pin_names(circuit: Circuit) -> dict[str, set[str]]:
    """Component id -> its I2C pin refs ("bme.SDA"), for parts that declare both SDA and SCL."""
    parts = {}
    for c in circuit.components:
        names = {p.upper(): p for p in c.pins}
        if "SDA" in names and "SCL" in names:
            parts[c.id] = {f"{c.id}.{names['SDA']}", f"{c.id}.{names['SCL']}"}
    return parts


def _expect(nl: Netlist, esp32: str, gpio: int, i2c_refs: set[str]) -> _Expectation:
    ref = f"{esp32}.GPIO{gpio}"
    net = nl.net_of_pin(ref)
    others = [p for p in net.pins if p != ref] if net else []
    power = {p.split(".", 1)[1] for p in others if p.startswith(f"{esp32}.")}
    grounds = {p for p in power if p.startswith("GND")}
    supplies = power & {"3V3", "VIN"}
    if grounds and supplies:
        return _Expectation(reading=None, reason="on a net that shorts GND to a supply")
    if grounds:
        return _Expectation(reading="pulled_low", reason="tied to GND")
    if supplies:
        return _Expectation(reading="pulled_high", reason=f"tied to {sorted(supplies)[0]}")
    if gpio in (I2C_SDA, I2C_SCL) and any(p in i2c_refs for p in others):
        return _Expectation(reading="pulled_high", reason="I2C line with the module's pull-ups")
    if not others:
        return _Expectation(reading="floating", reason="nothing connected")
    return _Expectation(reading=None, reason=f"connected to {', '.join(others)}")


def _expectations(circuit: Circuit, esp32: str) -> dict[int, _Expectation]:
    nl = netlist(circuit)
    i2c_refs = set().union(*_i2c_pin_names(circuit).values())
    return {gpio: _expect(nl, esp32, gpio, i2c_refs) for gpio in _gpios(circuit, esp32)}


# ── Proposals ─────────────────────────────────────────────────────────────────


def _apply_each(circuit: Circuit, observations: list[Observation]) -> tuple[Circuit, list[Observation]]:
    """Apply every proposal that can be applied. Returns the result and the ones that were applied."""
    applied = []
    for obs in observations:
        try:
            circuit = apply_observations(circuit, [obs])
        except ValueError:
            continue  # e.g. a new wire whose other end hasn't been seen yet
        applied.append(obs)
    return circuit, applied


def _holes(obs: Observation) -> set[str]:
    return set((obs.before or {}).values()) | set((obs.after or {}).values())


def _affected_gpios(
    obs: Observation,
    circuit: Circuit,
    applied: list[Observation],
    expected: dict[int, _Expectation],
    esp32: str,
) -> list[int]:
    """GPIOs whose expectation depends on `obs`, or whose own strip it touches."""
    template = TEMPLATES[circuit.board]
    gpio_strips = {gpio: template.strip(hole) for gpio, hole in _gpios(circuit, esp32).items()}
    obs_strips = {template.strip(h) for h in _holes(obs) if template.is_valid(h)}
    without, _ = _apply_each(circuit, [o for o in applied if o is not obs])
    counterfactual = _expectations(without, esp32)
    return sorted(
        gpio
        for gpio in expected
        if gpio_strips.get(gpio) in obs_strips
        or (gpio in counterfactual and counterfactual[gpio].reading != expected[gpio].reading)
    )


# ── Verdicts and messages ─────────────────────────────────────────────────────


def _pin_verdict(expectation: _Expectation, actual: str | None) -> Verdict:
    if actual is None or actual == "unsafe":
        return "not_checked"
    if expectation.reading is None or actual == "unstable":
        return "no_expectation"
    return "confirmed" if actual == expectation.reading else "conflict"


def _conflict_message(check: PinCheck, obj: str | None) -> str:
    gpio, expected, actual = f"GPIO{check.gpio}", check.expected, check.actual
    if actual == "floating":
        if obj:
            return f"{obj} may not be seated: {gpio} should read {expected} ({check.reason}) but reads floating. Press it in and rescan."
        return f"{gpio} should read {expected} ({check.reason}) but reads floating. A wire may have come loose; press it in and rescan."
    if expected == "floating":
        if obj:
            return f"{gpio} reads {actual}, but with {obj} as proposed nothing should drive it. Check {obj}'s holes and rescan."
        return f"Something is connected to {gpio} that the camera doesn't see (it reads {actual})."
    where = f"Check that {obj} is on the right rail and rescan." if obj else "Check what is plugged into its row."
    return f"{gpio} reads {actual} but should read {expected} ({check.reason}). {where}"


def _proposal_verdict(checks: list[PinCheck]) -> Verdict:
    verdicts = {c.verdict for c in checks}
    for verdict in ("conflict", "confirmed", "no_expectation"):
        if verdict in verdicts:
            return verdict
    return "not_checked"


def _i2c_checks(circuit: Circuit, found: list[str]) -> list[I2cCheck]:
    found_set = {a.lower() for a in found}
    checks, claimed = [], set()
    for c in circuit.components:
        if c.id not in _i2c_pin_names(circuit):
            continue
        value = (c.value or "").strip().lower()
        candidates = [value] if _ADDRESS_RE.match(value) else next(
            (addrs for name, addrs in KNOWN_I2C_ADDRESSES.items() if name in (c.model or "").lower()), []
        )
        if not candidates:
            continue  # unknown part with no declared address: nothing to compare
        hit = next((a for a in candidates if a in found_set), None)
        checks.append(I2cCheck(address=hit or candidates[0], component=c.id, status="confirmed" if hit else "missing"))
        claimed.update(candidates)
    checks += [I2cCheck(address=a, component=None, status="unexpected") for a in sorted(found_set - claimed)]
    return checks


# ── Entry point ───────────────────────────────────────────────────────────────


def reconcile(
    circuit: Circuit,
    observations: list[Observation],
    pins: dict[int, str] | None,
    i2c_devices: list[str] | None,
) -> Reconciliation:
    """Attach a serial verdict to each camera proposal.

    `circuit` is the working circuit the proposals are relative to, `pins` the agent's PROBE
    result (GPIO -> reading) and `i2c_devices` its I2C scan; both are None without a snapshot.
    """
    esp32 = _esp32_id(circuit)
    if esp32 is None:
        verdicts = [ProposalVerdict(observation_id=o.id, verdict="not_checked") for o in observations]
        return Reconciliation(proposals=verdicts, pins=[], i2c=[], warnings=[], serial_checked=pins is not None)

    proposed, applied = _apply_each(circuit, observations)
    expected = _expectations(proposed, esp32)
    readings = pins or {}
    checks = {
        gpio: PinCheck(
            gpio=gpio,
            expected=e.reading,
            actual=readings.get(gpio),
            verdict=_pin_verdict(e, readings.get(gpio)),
            reason=e.reason,
        )
        for gpio, e in expected.items()
    }

    warnings: list[str] = []
    verdicts, explained = [], set()
    for obs in observations:
        if obs not in applied:
            verdicts.append(ProposalVerdict(observation_id=obs.id, verdict="not_checked"))
            continue
        gpios = _affected_gpios(obs, circuit, applied, expected, esp32)
        affected = [checks[g] for g in gpios]
        verdict = _proposal_verdict(affected) if affected else "no_expectation"
        message = None
        conflicts = [c for c in affected if c.verdict == "conflict"]
        if conflicts:
            message = " ".join(_conflict_message(c, obs.object_id) for c in conflicts)
            warnings.append(message)
            explained.update(c.gpio for c in conflicts)
        verdicts.append(ProposalVerdict(observation_id=obs.id, verdict=verdict, gpios=gpios, message=message))

    for check in checks.values():
        if check.verdict == "conflict" and check.gpio not in explained:
            check.message = _conflict_message(check, None)
            warnings.append(check.message)

    return Reconciliation(
        proposals=verdicts,
        pins=sorted(checks.values(), key=lambda c: c.gpio),
        i2c=_i2c_checks(proposed, i2c_devices) if i2c_devices is not None else [],
        warnings=warnings,
        serial_checked=pins is not None,
    )


# ── Saving results with the circuit ───────────────────────────────────────────


class SerialReadings(BaseModel):
    """The raw readings of one serial snapshot, kept with a scan until its proposals are reviewed."""

    probed_at: str
    port: str | None = None
    agent: str | None = None
    pins: dict[int, str]
    i2c: list[str] | None = None


def serial_record(circuit: Circuit, readings: SerialReadings) -> SerialRecord:
    """Serial verdicts for exactly `circuit`, to be saved (and committed) with it."""
    result = reconcile(circuit, [], readings.pins, readings.i2c)
    return SerialRecord(
        probed_at=readings.probed_at, port=readings.port, agent=readings.agent, wiring=fingerprint(circuit),
        pins=[
            SerialPinResult(gpio=p.gpio, expected=p.expected, actual=p.actual, verdict=p.verdict, message=p.message)
            for p in result.pins
        ],
        i2c=[SerialI2cResult(address=c.address, component=c.component, status=c.status) for c in result.i2c]
        if readings.i2c is not None else None,
    )  # fmt: skip
