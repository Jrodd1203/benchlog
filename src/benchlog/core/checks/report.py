"""Check results and the report that collects them."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["pass", "fail", "needs_confirmation", "not_supported"]


class CheckResult(BaseModel):
    check: str = Field(description="Check id, e.g. 'short' or 'esp32_pins'.")
    status: Status
    message: str
    ids: list[str] = Field(
        default=[], description="Wires ('w5'), components ('pot1'), pins ('esp32.GPIO12') or observations involved."
    )


class CheckReport(BaseModel):
    status: Literal["pass", "fail", "needs_confirmation"] = Field(
        description="fail if any check fails, else needs_confirmation if any needs it, else pass. "
        "Checks that couldn't run are listed as not_supported in `results`."
    )
    commit: str | None = Field(description="Commit the circuit came from; null for uncommitted changes.")
    ran_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    results: list[CheckResult]

    @property
    def not_supported(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "not_supported"]


def overall(results: list[CheckResult]) -> Literal["pass", "fail", "needs_confirmation"]:
    statuses = {r.status for r in results}
    if "fail" in statuses:
        return "fail"
    if "needs_confirmation" in statuses:
        return "needs_confirmation"
    return "pass"
