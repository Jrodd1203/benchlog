"""API for branches and the physical board state."""

from fastapi import APIRouter
from pydantic import BaseModel

from benchlog.core.branches import Branch, BoardReport, board_report, checkout, create_branch, list_branches
from benchlog.server.deps import ProjectDep

router = APIRouter(prefix="/api", tags=["branches"])


class BranchRequest(BaseModel):
    name: str


class CheckoutRequest(BaseModel):
    branch: str


@router.get("/board-state")
def get_board_state(project: ProjectDep) -> BoardReport:
    """Which commit the board matches, and how to rewire it to the checked-out circuit if it doesn't."""
    return board_report(project)


@router.get("/branches")
def get_branches(project: ProjectDep) -> list[Branch]:
    return list_branches(project)


@router.post("/branches")
def post_branch(request: BranchRequest, project: ProjectDep) -> Branch:
    """Create a branch at HEAD (without switching to it)."""
    return create_branch(project, request.name)


@router.post("/checkout")
def post_checkout(request: CheckoutRequest, project: ProjectDep) -> BoardReport:
    """Switch branches. 400 if the circuit has uncommitted changes or scan proposals are pending."""
    return checkout(project, request.branch)
