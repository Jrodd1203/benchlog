"""What the ESP32 serial agent sensed.

The data comes from `circuit.serial`, the verdicts saved (and committed) when scan proposals were
accepted, so these checks work on any commit and on PRs. A live reconciliation can be passed
instead (POST /api/checks/run probes the board right away). Without either, or when the saved
data describes older wiring, the checks are not_supported: never a silent pass.
"""

from typing import Literal

from pydantic import BaseModel

from benchlog.core.board_state import fingerprint
from benchlog.core.checks._circuit import esp32_id
from benchlog.core.checks.report import CheckResult
from benchlog.core.models import Circuit, ComponentType
from benchlog.core.reconcile import Reconciliation

NO_SERIAL = "No serial results saved with this circuit. Scan with the ESP32 connected and accept the proposals."
STALE = "The saved serial results are from before the circuit was last edited. Scan again with the ESP32 connected."


class _Pin(BaseModel):
    gpio: int
    verdict: str
    message: str | None


class _I2c(BaseModel):
    address: str
    component: str | None
    status: Literal["confirmed", "missing", "unexpected"]


class _Serial(BaseModel):
    pins: list[_Pin]
    i2c: list[_I2c] | None
    probed_at: str | None = None
    proposal_conflicts: dict[str, str] = {}  # live only: observation id -> message


def _source(circuit: Circuit, live: Reconciliation | None) -> _Serial | str:
    """The serial data to check, or why there is none."""
    if live is not None and live.serial_checked:
        return _Serial(
            pins=[_Pin(gpio=p.gpio, verdict=p.verdict, message=p.message) for p in live.pins],
            i2c=[_I2c(**c.model_dump()) for c in live.i2c],
            proposal_conflicts={v.observation_id: v.message or "" for v in live.proposals if v.verdict == "conflict"},
        )
    saved = circuit.serial
    if saved is None:
        return NO_SERIAL
    if saved.wiring != fingerprint(circuit):
        return STALE
    return _Serial(
        pins=[_Pin(gpio=p.gpio, verdict=p.verdict, message=p.message) for p in saved.pins],
        i2c=[_I2c(**c.model_dump()) for c in saved.i2c] if saved.i2c is not None else None,
        probed_at=saved.probed_at,
    )


def _when(data: _Serial) -> str:
    return f" (probed {data.probed_at})" if data.probed_at else ""


def serial_conflicts(circuit: Circuit, reconciliation: Reconciliation | None = None) -> list[CheckResult]:
    """Fail for every pin whose serial reading contradicts the circuit."""
    check = "serial_conflicts"
    data = _source(circuit, reconciliation)
    if isinstance(data, str):
        return [CheckResult(check=check, status="not_supported", message=data)]
    esp32 = esp32_id(circuit) or "esp32"
    results = [
        CheckResult(check=check, status="fail", message=message or "Serial reading contradicts this change.", ids=[obs_id])
        for obs_id, message in data.proposal_conflicts.items()
    ]
    # Pins with a message aren't explained by a proposal; the rest were reported with their proposal.
    results += [
        CheckResult(check=check, status="fail", message=p.message, ids=[f"{esp32}.GPIO{p.gpio}"])
        for p in data.pins
        if p.verdict == "conflict" and (p.message or not data.proposal_conflicts)
    ]
    return results or [CheckResult(check=check, status="pass", message=f"Serial readings match the circuit{_when(data)}.")]


def i2c_missing(circuit: Circuit, reconciliation: Reconciliation | None = None) -> list[CheckResult]:
    """Fail for every declared I2C module whose address didn't answer."""
    check = "i2c_missing"
    declared = [c.id for c in circuit.components if c.type == ComponentType.I2C_MODULE]
    if not declared:
        return [CheckResult(check=check, status="pass", message="No I2C modules declared.")]
    data = _source(circuit, reconciliation)
    if isinstance(data, str):
        return [CheckResult(check=check, status="not_supported", message=data, ids=declared)]
    if data.i2c is None:
        return [CheckResult(check=check, status="not_supported", message="The I2C bus wasn't scanned.", ids=declared)]
    by_component = {c.component: c for c in data.i2c if c.component}
    results = []
    for part in declared:
        found = by_component.get(part)
        if found is None:
            results.append(CheckResult(
                check=check, status="not_supported", ids=[part],
                message=f"{part}: unknown I2C address. Set its value to the address, e.g. \"0x76\".",
            ))  # fmt: skip
        elif found.status == "missing":
            results.append(CheckResult(
                check=check, status="fail", ids=[part],
                message=f"{part} didn't answer at {found.address}. Check its SDA/SCL wires and power.",
            ))  # fmt: skip
    if not results:
        results.append(CheckResult(check=check, status="pass", message=f"Every declared I2C module answered{_when(data)}.", ids=declared))
    return results
