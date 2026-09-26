"""Local FastAPI service used by the web UI. Owner: Person 1 (scan capture: Person 4).

Every endpoint is a thin wrapper over `benchlog.core.project.Project`, so the UI and the CLI
behave the same. Run it from inside a project with `benchlog serve`, or point it at one with
the BENCHLOG_PROJECT environment variable.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from benchlog.camera import read_board
from benchlog.core.diff import CircuitDiff, describe, diff
from benchlog.core.models import Circuit, Hole, Observation
from benchlog.core.netlist import Netlist, netlist
from benchlog.core.project import Project, ProjectError
from benchlog.core.prs import PRNotFound
from benchlog.core.reconcile import Reconciliation, reconcile
from benchlog.core.repo import Commit, GitError
from benchlog.core.scan import reading_from_circuit
from benchlog.serial.service import SerialSnapshot
from benchlog.server.branch_routes import router as branch_router
from benchlog.server.check_routes import router as check_router
from benchlog.server.pr_routes import not_found as pr_not_found
from benchlog.server.pr_routes import router as pr_router
from benchlog.server.serial_routes import router as serial_router
from benchlog.server.serial_routes import serial_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    serial_service(app)  # one serial agent connection for the app's lifetime
    yield
    app.state.serial.close()


app = FastAPI(title="benchlog", lifespan=lifespan)
app.include_router(serial_router)
app.include_router(check_router)
app.include_router(branch_router)
app.include_router(pr_router)
app.add_exception_handler(PRNotFound, pr_not_found)


@app.exception_handler(ProjectError)
@app.exception_handler(GitError)
async def _user_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def get_project() -> Project:
    start = os.environ.get("BENCHLOG_PROJECT")
    return Project.find(Path(start) if start else None)


ProjectDep = Annotated[Project, Depends(get_project)]


def _circuit_at(project: Project, rev: str) -> Circuit:
    try:
        project.repo.run("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    except GitError as e:
        raise ProjectError(f"unknown revision {rev!r}") from e
    return project.circuit_at(rev) or Circuit(board=project.load_circuit().board)


# ── Response and request bodies ───────────────────────────────────────────────


class DiffResponse(BaseModel):
    diff: CircuitDiff
    lines: list[str] = Field(description="Plain-English summary, electrical changes first.")


class StatusResponse(BaseModel):
    branch: str
    head: Commit | None
    committed: bool = Field(description="False until the circuit's first commit.")
    changes: DiffResponse = Field(description="Working circuit vs HEAD.")
    pending: int = Field(description="Observations waiting for review.")


class HistoryEntry(BaseModel):
    commit: Commit
    lines: list[str] = Field(description="What this commit changed in the circuit.")


class ScanRequest(BaseModel):
    simulate: Circuit | None = Field(default=None, description="Pretend the board looks like this (demo fallback).")
    image: str | None = Field(default=None, description="Path to a photo to scan instead of the camera.")
    camera: int | None = Field(default=None, description="Camera index; default: the one chosen with `benchlog camera use`.")
    sync: bool = Field(default=False, description="Record the board as matching the circuit; propose nothing.")


class ScanResponse(BaseModel):
    source: str
    warnings: list[str]
    observations: list[Observation]
    serial: SerialSnapshot | None = Field(default=None, description="What the serial agent sensed; null without one.")
    reconciliation: Reconciliation = Field(description="Serial verdict for each observation, plus serial warnings.")


class IdsRequest(BaseModel):
    ids: list[str] | None = Field(default=None, description="Observation ids; null means every pending one.")


class AcceptResponse(BaseModel):
    accepted: list[Observation]
    circuit: Circuit
    changes: DiffResponse = Field(description="What accepting changed in the working circuit.")


class EditRequest(BaseModel):
    ends: dict[str, Hole] = Field(description='Wire ends to set, e.g. {"b": "J45"}.')


class CommitRequest(BaseModel):
    message: str = Field(min_length=1)
    firmware: list[str] = Field(default=[], description="Firmware paths relative to the project root.")


class CommitResponse(BaseModel):
    commit: Commit
    changes: DiffResponse


def _diff_response(old: Circuit, new: Circuit) -> DiffResponse:
    d = diff(old, new)
    return DiffResponse(diff=d, lines=describe(d))


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def status(project: ProjectDep) -> StatusResponse:
    head = project.repo.log(limit=1)
    return StatusResponse(
        branch=project.repo.current_branch(),
        head=head[0] if head else None,
        committed=project.circuit_at("HEAD") is not None,
        changes=_diff_response(project.baseline(), project.load_circuit()),
        pending=len(project.pending_observations()),
    )


@app.get("/api/circuit")
def get_circuit(project: ProjectDep) -> Circuit:
    """The working circuit (accepted, maybe not committed)."""
    return project.load_circuit()


@app.put("/api/circuit")
def put_circuit(circuit: Circuit, project: ProjectDep) -> DiffResponse:
    """Replace the working circuit, e.g. after manual click-to-wire edits. Returns what changed."""
    before = project.load_circuit()
    project.save_circuit(circuit)
    return _diff_response(before, circuit)


@app.get("/api/circuit/{rev}")
def get_circuit_at(rev: str, project: ProjectDep) -> Circuit:
    """The committed circuit at a revision (sha, branch, HEAD~1, ...); an empty board before it existed."""
    return _circuit_at(project, rev)


@app.get("/api/netlist")
def get_netlist(project: ProjectDep, rev: str | None = None) -> Netlist:
    return netlist(_circuit_at(project, rev) if rev else project.load_circuit())


@app.get("/api/diff")
def get_diff(
    project: ProjectDep,
    old: Annotated[str | None, Query(description="Revision to compare from (default: HEAD).")] = None,
    new: Annotated[str | None, Query(description="Revision to compare to (default: working circuit).")] = None,
) -> DiffResponse:
    before = _circuit_at(project, old) if old else project.baseline()
    after = _circuit_at(project, new) if new else project.load_circuit()
    return _diff_response(before, after)


@app.get("/api/history")
def history(project: ProjectDep, limit: int | None = None) -> list[HistoryEntry]:
    """Commits that changed the circuit, newest first. Fetch /api/circuit/{sha} for each one's circuit."""
    entries = []
    for c in project.history(limit=limit):
        after = project.circuit_at(c.sha)
        before = project.circuit_at(f"{c.sha}^") or Circuit(board=after.board)
        entries.append(HistoryEntry(commit=c, lines=describe(diff(before, after))))
    return entries


@app.post("/api/scan")
def scan(request: ScanRequest, project: ProjectDep, http: Request) -> ScanResponse:
    if request.simulate is not None:
        reading = reading_from_circuit(request.simulate, "simulated")
    else:
        reading = read_board(
            project.camera_index(request.camera), Path(request.image) if request.image else None, project.calibration_dir
        )
    serial = serial_service(http.app).snapshot()  # None when no agent is connected
    observations = project.scan(reading, sync=request.sync)
    check = reconcile(
        project.load_circuit(), observations,
        serial.probe.pins if serial else None, serial.i2c.devices if serial else None,
    )  # fmt: skip
    return ScanResponse(
        source=reading.source, warnings=reading.warnings, observations=observations, serial=serial, reconciliation=check
    )


@app.get("/api/observations")
def observations(project: ProjectDep, pending_only: bool = False) -> list[Observation]:
    return project.pending_observations() if pending_only else project.observations()


@app.post("/api/observations/accept")
def accept(request: IdsRequest, project: ProjectDep) -> AcceptResponse:
    """Apply observations to the circuit. All or nothing."""
    before = project.load_circuit()
    accepted = project.accept_observations(request.ids)
    after = project.load_circuit()
    return AcceptResponse(accepted=accepted, circuit=after, changes=_diff_response(before, after))


@app.post("/api/observations/reject")
def reject(request: IdsRequest, project: ProjectDep) -> list[Observation]:
    return project.reject_observations(request.ids)


@app.patch("/api/observations/{obs_id}")
def edit(obs_id: str, request: EditRequest, project: ProjectDep) -> Observation:
    """Set or correct a pending wire's ends before accepting it."""
    return project.edit_observation(obs_id, {k: v.strip().upper() for k, v in request.ends.items()})


@app.post("/api/commit")
def commit(request: CommitRequest, project: ProjectDep) -> CommitResponse:
    before = project.baseline()
    c = project.commit(request.message, firmware=[project.repo.root / p for p in request.firmware])
    return CommitResponse(commit=c, changes=_diff_response(before, project.load_circuit()))
