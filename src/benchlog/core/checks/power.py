"""Power-to-ground shorts."""

from benchlog.core.checks._circuit import esp32_id, power_pins
from benchlog.core.checks.report import CheckResult
from benchlog.core.models import Circuit
from benchlog.core.netlist import netlist

CHECK = "short"


def short(circuit: Circuit) -> list[CheckResult]:
    """Fail for every net that joins a supply (3V3 or VIN) to GND."""
    esp32 = esp32_id(circuit)
    if esp32 is None:
        return [CheckResult(check=CHECK, status="not_supported", message="No ESP32 in the circuit, so the power nets are unknown.")]
    results = []
    for net in netlist(circuit).nets:
        grounds, supplies = power_pins(net, esp32)
        if grounds and supplies:
            supply = ", ".join(p.split(".", 1)[1] for p in supplies)
            via = f" through {', '.join(net.wires)}" if net.wires else ""
            results.append(
                CheckResult(
                    check=CHECK, status="fail", ids=[*supplies, *grounds, *net.wires],
                    message=f"{supply} is shorted to GND{via}. Unplug the ESP32 before fixing it.",
                )
            )  # fmt: skip
    return results or [CheckResult(check=CHECK, status="pass", message="No supply is connected to GND.")]
