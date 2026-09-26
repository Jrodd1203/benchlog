"""ESP32 pin rules. Owner: Person 2."""

from benchlog.core.checks._circuit import attached, esp32_id, gpio_pins
from benchlog.core.checks.report import CheckResult
from benchlog.core.models import Circuit, ComponentType
from benchlog.core.netlist import Netlist, netlist

INPUT_ONLY_PINS = {34, 35, 36, 37, 38, 39}
STRAPPING_PINS = {0, 2, 5, 12, 15}
FLASH_PINS = {6, 7, 8, 9, 10, 11}

CHECK = "esp32_pins"
STRAPPING_NOTES = {
    0: "GPIO0 held low at boot puts the ESP32 in download mode.",
    2: "GPIO2 must be low or floating at boot to flash the ESP32.",
    5: "GPIO5 sets the SDIO timing at boot.",
    15: "GPIO15 held low at boot silences the boot log.",
}


def _component(ref: str) -> str:
    return ref.split(".", 1)[0]


def _needs_output(nl: Netlist, circuit: Circuit, others: list[str]) -> list[str]:
    """Pins that need this GPIO to drive them: LEDs (directly or through one resistor) and I2C lines."""
    types = {c.id: c.type for c in circuit.components}
    drivers = []
    for ref in others:
        kind = types.get(_component(ref))
        if kind == ComponentType.LED:
            drivers.append(ref)
        elif kind == ComponentType.I2C_MODULE and ref.split(".", 1)[1].upper() in ("SDA", "SCL"):
            drivers.append(ref)
        elif kind == ComponentType.RESISTOR:
            resistor = next(c for c in circuit.components if c.id == _component(ref))
            for pin in resistor.pins:
                far = f"{resistor.id}.{pin}"
                if far != ref:
                    drivers += [p for p in attached(nl, far)[0] if types.get(_component(p)) == ComponentType.LED]
    return drivers


def esp32_pins(circuit: Circuit) -> list[CheckResult]:
    esp32 = esp32_id(circuit)
    if esp32 is None:
        return [CheckResult(check=CHECK, status="not_supported", message="No ESP32 in the circuit.")]
    nl = netlist(circuit)
    results = []
    for gpio, ref in sorted(gpio_pins(circuit, esp32).items()):
        others, wires = attached(nl, ref)
        if not others and not wires:
            continue
        ids = [ref, *wires, *others]
        name = f"GPIO{gpio}"
        if gpio in FLASH_PINS:
            results.append(CheckResult(
                check=CHECK, status="fail", ids=ids,
                message=f"{name} is wired to the ESP32's flash chip; using it crashes the board. Move the wire.",
            ))  # fmt: skip
        elif gpio == 12:
            results.append(CheckResult(
                check=CHECK, status="fail", ids=ids,
                message="GPIO12 held high at boot can stop the ESP32 from starting. Move the wire to a safe pin.",
            ))  # fmt: skip
        elif gpio in STRAPPING_PINS:
            results.append(CheckResult(
                check=CHECK, status="needs_confirmation", ids=ids,
                message=f"{name} is a strapping pin: {STRAPPING_NOTES[gpio]} Confirm the circuit leaves it in a safe state at boot.",
            ))  # fmt: skip
        if gpio in INPUT_ONLY_PINS and (drivers := _needs_output(nl, circuit, others)):
            results.append(CheckResult(
                check=CHECK, status="fail", ids=[ref, *wires, *drivers],
                message=f"{name} is input-only, so it can't drive {', '.join(drivers)}. Move it to an output-capable pin.",
            ))  # fmt: skip
    return results or [CheckResult(check=CHECK, status="pass", message="No wires on flash, strapping, or misused input-only pins.")]
