"""Scan proposals the user still has to confirm."""

from benchlog.core.checks.report import CheckResult
from benchlog.core.models import Observation, ObservationKind

CHECK = "unconfirmed_wires"


def unconfirmed_wires(pending: list[Observation] | None) -> list[CheckResult]:
    """Needs confirmation for every pending scan proposal with holes the camera wasn't sure about."""
    if pending is None:
        return [CheckResult(check=CHECK, status="not_supported", message="No scan data to check.")]
    results = []
    for obs in pending:
        missing_end = obs.object_type == "wire" and obs.kind == ObservationKind.ADDED and set(obs.after or {}) != {"a", "b"}
        if missing_end:
            message = f"{obs.object_id}: only one end was seen. Confirm where the other end goes."
        elif obs.uncertain_holes:
            message = f"{obs.object_id}: confirm hole{'s' * (len(obs.uncertain_holes) > 1)} {', '.join(obs.uncertain_holes)}."
        else:
            continue
        results.append(CheckResult(check=CHECK, status="needs_confirmation", message=message, ids=[obs.object_id, obs.id]))
    return results or [CheckResult(check=CHECK, status="pass", message="No wires or parts waiting for confirmation.")]
