"""Local git branches for trying circuit changes without touching main.

Checking out a branch swaps circuit.json but not the wires on the bench, so checkout also says
how to rewire the board when it no longer matches.
"""

from pydantic import BaseModel, Field

from benchlog.core.board_state import BoardState, RewireStep, fingerprint, rewire_guide
from benchlog.core.models import Circuit
from benchlog.core.pairing import occupied_holes
from benchlog.core.project import Project, ProjectError
from benchlog.core.repo import GitError
from benchlog.core.scan import BoardReading


class Branch(BaseModel):
    name: str
    head: str
    subject: str = Field(description="Message of the branch's latest commit.")
    current: bool


class BoardReport(BaseModel):
    """Whether the board matches the checked-out circuit, and how to make it match."""

    branch: str
    head: str | None
    state: BoardState
    matches: bool | None = Field(description="Null when benchlog has never seen the board confirmed.")
    assumed_from: str | None = Field(default=None, description="If the board state is unknown, the commit the guide assumes.")
    guide: list[RewireStep] = []


def list_branches(project: Project) -> list[Branch]:
    current = project.repo.current_branch()
    out = project.repo.run("for-each-ref", "--format=%(refname:short)%09%(objectname)%09%(subject)", "refs/heads")
    branches = []
    for line in out.splitlines():
        name, head, subject = (line.split("\t") + ["", ""])[:3]
        branches.append(Branch(name=name, head=head, subject=subject, current=name == current))
    return branches


def branch_head(project: Project, name: str) -> str:
    try:
        return project.repo.run("rev-parse", "--verify", "--quiet", f"refs/heads/{name}").strip()
    except GitError as e:
        raise ProjectError(f"no branch named {name!r}") from e


def create_branch(project: Project, name: str) -> Branch:
    """Create `name` at HEAD (without switching to it)."""
    if project.repo.head() is None:
        raise ProjectError("commit the circuit once before creating branches")
    try:
        project.repo.run("check-ref-format", "--branch", name)
    except GitError as e:
        raise ProjectError(f"{name!r} is not a valid branch name") from e
    if any(b.name == name for b in list_branches(project)):
        raise ProjectError(f"branch {name!r} already exists")
    project.repo.run("branch", name)
    return next(b for b in list_branches(project) if b.name == name)


def _circuit(project: Project, rev: str | None, board: str) -> Circuit:
    return (project.circuit_at(rev) if rev else None) or Circuit(board=board)


def board_report(project: Project, assume: str | None = None) -> BoardReport:
    """Compare the board with the checked-out circuit.

    Without a confirmed board state, the guide assumes the board matches `assume` (a commit).
    """
    state = project.board_state.load()
    head = project.repo.head()
    circuit = project.load_circuit()
    report = dict(branch=project.repo.current_branch(), head=head, state=state)
    if state.circuit_fingerprint == fingerprint(circuit):
        return BoardReport(**report, matches=True)
    source = state.matches_commit
    if source is None:
        if assume is None:
            return BoardReport(**report, matches=None)
        report["assumed_from"] = source = assume
    board_circuit = _circuit(project, source, circuit.board)
    guide = rewire_guide(board_circuit, circuit)
    return BoardReport(**report, matches=not guide, guide=guide)


def shift_scan_reference(project: Project, old: Circuit, new: Circuit) -> None:
    """Keep scans measured against the checked-out circuit.

    The scan reference is what the camera saw when the board matched the old circuit. Swap the
    holes that differ, so scanning an un-rewired board proposes exactly the rewiring still to do,
    and scanning a rewired board comes back clean.
    """
    project.scans.promote_latest()  # nothing is pending, so the latest scan is the reference
    if not project.scans.reference_path.exists():
        return  # never scanned: the reference falls back to the circuit itself
    reference = project.scans.reference(old)
    old_holes, new_holes = set(occupied_holes(old)), set(occupied_holes(new))
    occupied = (set(reference.occupied) - (old_holes - new_holes)) | (new_holes - old_holes)
    project.scans.reset(
        BoardReading(
            occupied=sorted(occupied), colors=reference.colors, confidence=reference.confidence,
            source=f"{reference.source} (adjusted for checkout)",
        )
    )  # fmt: skip


def require_clean(project: Project, action: str) -> None:
    if project.repo.is_dirty(project.circuit_path):
        raise ProjectError(f"can't {action}: the circuit has uncommitted changes. Commit them first.")
    pending = project.pending_observations()
    if pending:
        raise ProjectError(
            f"can't {action}: {len(pending)} scan proposal(s) still pending. Accept or reject them first "
            "(`benchlog review`)."
        )


def checkout(project: Project, name: str) -> BoardReport:
    """Switch branches. Refuses with uncommitted circuit changes or pending scan proposals."""
    branch_head(project, name)  # raises for an unknown branch
    if name == project.repo.current_branch():
        return board_report(project)
    require_clean(project, f"switch to {name}")
    previous_head = project.repo.head()
    old = project.load_circuit()
    project.repo.run("checkout", "--quiet", name)
    new = project.load_circuit()
    shift_scan_reference(project, old, new)
    report = board_report(project, assume=previous_head)
    if report.matches:
        project.board_confirmed()  # e.g. both branches have the same circuit
    return report
