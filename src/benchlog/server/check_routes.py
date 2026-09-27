"""API for circuit checks.

    POST /api/checks/run        check the working circuit (with a live serial snapshot if connected)
    GET  /api/checks?commit=... the report saved for a commit
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from benchlog.core.checks import CheckReport
from benchlog.core.checks.store import check_project, load_report
from benchlog.core.project import ProjectError
from benchlog.core.reconcile import reconcile
from benchlog.core.repo import GitError
from benchlog.server.deps import ProjectDep
from benchlog.server.serial_routes import CLIENT_SERIAL_FIELD, ClientSerialReadings, readings_for

router = APIRouter(prefix="/api/checks", tags=["checks"])


class RunChecksRequest(BaseModel):
    serial: ClientSerialReadings | None = Field(default=None, description=CLIENT_SERIAL_FIELD)


@router.post("/run")
def run(project: ProjectDep, request: Request, body: RunChecksRequest | None = None) -> CheckReport:
    """Check the working circuit, with what the ESP32 senses: the browser's readings if sent, else
    the server's own ESP32 (probed first if connected).

    When the circuit matches HEAD, the report is saved for that commit.
    """
    readings = readings_for(request.app, project, body.serial if body else None)
    reconciliation = None
    if readings is not None:
        reconciliation = reconcile(project.load_circuit(), project.pending_observations(), readings.pins, readings.i2c)
    return check_project(project, reconciliation)


@router.get("")
def saved(project: ProjectDep, commit: str = "HEAD") -> CheckReport:
    """The report saved for a commit (sha, short sha, branch or HEAD)."""
    try:
        sha = project.repo.run("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}").strip()
    except GitError as e:
        raise ProjectError(f"unknown revision {commit!r}") from e
    report = load_report(project, sha)
    if report is None:
        raise HTTPException(status_code=404, detail=f"no saved check report for {sha[:7]}; run the checks on that commit")
    return report
