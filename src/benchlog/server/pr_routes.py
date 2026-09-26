"""API for local pull requests."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from benchlog.core import prs
from benchlog.server.deps import ProjectDep

router = APIRouter(prefix="/api/prs", tags=["prs"])


def not_found(request: Request, exc: prs.PRNotFound) -> JSONResponse:
    """Registered on the app (see server/app.py): an unknown PR id is a 404, not a 400."""
    return JSONResponse(status_code=404, content={"detail": str(exc)})


class CreateRequest(BaseModel):
    title: str
    description: str = ""
    from_branch: str | None = None  # default: the current branch
    into_branch: str | None = None  # default: main


class TestedRequest(BaseModel):
    note: str = ""


@router.get("")
def list_prs(project: ProjectDep) -> list[prs.PR]:
    return prs.list_prs(project)


@router.post("")
def create_pr(request: CreateRequest, project: ProjectDep) -> prs.PRDetail:
    from_branch = request.from_branch or project.repo.current_branch()
    pr = prs.create(project, from_branch, request.into_branch, request.title, request.description)
    return prs.get(project, pr.id)


@router.get("/{pr_id}")
def get_pr(pr_id: int, project: ProjectDep) -> prs.PRDetail:
    """The PR with both circuits, the diff, checks on the branch head, and whether it can be merged."""
    return prs.get(project, pr_id)


@router.post("/{pr_id}/tested")
def mark_tested(pr_id: int, request: TestedRequest, project: ProjectDep) -> prs.PR:
    return prs.mark_tested(project, pr_id, request.note)


@router.post("/{pr_id}/merge")
def merge(pr_id: int, project: ProjectDep) -> prs.PRDetail:
    """Fast-forward into_branch. 400 with the reason if merging isn't allowed."""
    return prs.merge(project, pr_id)


@router.post("/{pr_id}/close")
def close(pr_id: int, project: ProjectDep) -> prs.CloseResult:
    return prs.close(project, pr_id)
