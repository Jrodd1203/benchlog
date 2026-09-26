"""What the ESP32 serial agent sensed, via the reconciler."""

from benchlog.core.checks._circuit import esp32_id
from benchlog.core.checks.report import CheckResult
from benchlog.core.models import Circuit, ComponentType
from benchlog.core.reconcile import Reconciliation

NO_SERIAL = "No serial data recorded. Connect the ESP32 and run the checks from the web app."


def serial_conflicts(circuit: Circuit, reconciliation: Reconciliation | None) -> list[CheckResult]:
    """Fail for every conflict between the camera/circuit and the serial readings."""
    check = "serial_conflicts"
    if reconciliation is None or not reconciliation.serial_checked:
        return [CheckResult(check=check, status="not_supported", message=NO_SERIAL)]
    results = [
        CheckResult(check=check, status="fail", message=v.message or "Serial reading contradicts this change.", ids=[v.observation_id])
        for v in reconciliation.proposals
        if v.verdict == "conflict"
    ]
    esp32 = esp32_id(circuit) or "esp32"
    results += [
        CheckResult(check=check, status="fail", message=p.message, ids=[f"{esp32}.GPIO{p.gpio}"])
        for p in reconciliation.pins
        if p.verdict == "conflict" and p.message
    ]
    return results or [CheckResult(check=check, status="pass", message="Serial readings match the circuit.")]


def i2c_missing(circuit: Circuit, reconciliation: Reconciliation | None) -> list[CheckResult]:
    """Fail for every declared I2C module whose address didn't answer."""
    check = "i2c_missing"
    declared = [c.id for c in circuit.components if c.type == ComponentType.I2C_MODULE]
    if not declared:
        return [CheckResult(check=check, status="pass", message="No I2C modules declared.")]
    if reconciliation is None or not reconciliation.serial_checked:
        return [CheckResult(check=check, status="not_supported", message=NO_SERIAL, ids=declared)]
    by_component = {c.component: c for c in reconciliation.i2c if c.component}
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
        results.append(CheckResult(check=check, status="pass", message="Every declared I2C module answered.", ids=declared))
    return results
