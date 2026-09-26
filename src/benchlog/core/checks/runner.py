"""Run every check."""

from benchlog.core.checks.esp32 import esp32_pins
from benchlog.core.checks.power import short
from benchlog.core.checks.report import CheckReport, overall
from benchlog.core.checks.serial import i2c_missing, serial_conflicts
from benchlog.core.checks.wires import unconfirmed_wires
from benchlog.core.models import Circuit, Observation
from benchlog.core.reconcile import Reconciliation


def run_checks(
    circuit: Circuit,
    reconcile_result: Reconciliation | None = None,
    pending: list[Observation] | None = None,
    commit: str | None = None,
) -> CheckReport:
    """Run all checks on `circuit`.

    `reconcile_result` is the latest scan's reconciliation (serial data) and `pending` the scan
    proposals still waiting for review; checks that need either report not_supported without it.
    """
    results = [
        *short(circuit),
        *unconfirmed_wires(pending),
        *serial_conflicts(circuit, reconcile_result),
        *i2c_missing(circuit, reconcile_result),
        *esp32_pins(circuit),
    ]
    return CheckReport(status=overall(results), commit=commit, results=results)
