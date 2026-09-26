"""API for circuit checks.

    POST /api/checks/run        check the working circuit (with a live serial snapshot if connected)
    GET  /api/checks?commit=... the report saved for a commit
"""

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from benchlog.core.checks import CheckReport
from benchlog.core.checks.store import check_project, load_report
from benchlog.core.project import Project, ProjectError
from benchlog.core.reconcile import reconcile
from benchlog.core.repo import GitError
from benchlog.server.serial_routes import serial_service

router = APIRouter(prefix="/api/checks", tags=["checks"])


def get_project() -> Project:
    # Same lookup as server/app.py (importing it from there would be circular).
    start = os.environ.get("BENCHLOG_PROJECT")
    return Project.find(Path(start) if start else None)


ProjectDep = Annotated[Project, Depends(get_project)]


@router.post("/run")
def run(project: ProjectDep, request: Request) -> CheckReport:
    """Check the working circuit. Probes the ESP32 first if one is connected.

    When the circuit matches HEAD, the report is saved for that commit.
    """
    snapshot = serial_service(request.app).snapshot()
    reconciliation = None
    if snapshot is not None:
        reconciliation = reconcile(
            project.load_circuit(), project.pending_observations(), snapshot.probe.pins, snapshot.i2c.devices
        )
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
