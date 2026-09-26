"""Local pull requests: compare a branch with the branch it will merge into, check it, merge it.

    .benchlog/prs/0001-<slug>.json   gitignored, so switching branches never changes them

A PR file is only a pointer: which branches to compare, plus status, check results and whether
someone tested it. It never stores circuit data. Both circuits are read from git every time
(circuit.json at `into_branch` is "before", at `from_branch` is "after"), so a new commit on the
branch shows up in the PR on its own.
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from benchlog.core.board_state import RewireStep, rewire_guide
from benchlog.core.branches import branch_head, list_branches, require_clean, shift_scan_reference
from benchlog.core.checks import CheckReport, run_checks
from benchlog.core.checks.store import load_report, save_report
from benchlog.core.diff import CircuitDiff, describe, diff
from benchlog.core.models import Circuit
from benchlog.core.project import Project, ProjectError
from benchlog.core.repo import GitError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── The pointer file ──────────────────────────────────────────────────────────


class PRChecks(BaseModel):
    commit: str
    overall: Literal["pass", "fail", "needs_confirmation"]


class PRTested(BaseModel):
    done: bool = False
    note: str | None = None
    commit: str | None = Field(default=None, description="Branch head that was tested.")
    at: str | None = None


class PRMerge(BaseModel):
    commit: str = Field(description="Head of from_branch that was merged.")
    into_before: str = Field(description="Head of into_branch just before the merge.")
    at: str


class PR(BaseModel):
    id: int
    title: str
    description: str = ""
    from_branch: str
    into_branch: str
    status: Literal["open", "merged", "closed"] = "open"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    last_checked_commit: str | None = None
    checks: PRChecks | None = None
    tested: PRTested = PRTested()
    merged: PRMerge | None = Field(default=None, description="Set when merged, so the PR still shows what it changed.")


class PRNotFound(ProjectError):
    pass


class PRStore:
    """All access to .benchlog/prs/."""

    def __init__(self, state_dir: Path) -> None:
        self.dir = state_dir / "prs"

    def _path(self, pr_id: int) -> Path | None:
        return next(iter(sorted(self.dir.glob(f"{pr_id:04d}-*.json"))), None)

    def all(self) -> list[PR]:
        return [PR.model_validate_json(p.read_text()) for p in sorted(self.dir.glob("[0-9][0-9][0-9][0-9]-*.json"))]

    def get(self, pr_id: int) -> PR:
        path = self._path(pr_id)
        if path is None:
            raise PRNotFound(f"no PR #{pr_id}")
        return PR.model_validate_json(path.read_text())

    def next_id(self) -> int:
        return max((pr.id for pr in self.all()), default=0) + 1

    def save(self, pr: PR) -> PR:
        pr = pr.model_copy(update={"updated_at": _now()})
        path = self._path(pr.id) or self.dir / f"{pr.id:04d}-{_slug(pr.title)}.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_text(pr.model_dump_json(indent=2) + "\n")
        return pr


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "pr"


# ── The detail view ───────────────────────────────────────────────────────────


class MergeStatus(BaseModel):
    allowed: bool
    reason: str
    warnings: list[str] = Field(default=[], description="needs_confirmation results: they don't block, but show them.")
    not_checked: list[str] = Field(default=[], description="not_supported results: couldn't be checked (never a pass).")


class PRDetail(BaseModel):
    pr: PR
    before: Circuit = Field(description="circuit.json at into_branch.")
    after: Circuit = Field(description="circuit.json at the head of from_branch.")
    diff: CircuitDiff
    summary: list[str] = Field(description="The diff in plain English, electrical changes first.")
    checks: CheckReport | None = Field(description="Checks on the branch head.")
    merge: MergeStatus


def _store(project: Project) -> PRStore:
    return PRStore(project.state_dir)


def default_into(project: Project) -> str:
    """'main', or 'master' in repositories that still use it."""
    names = {b.name for b in list_branches(project)}
    for name in ("main", "master"):
        if name in names:
            return name
    raise ProjectError("no main branch; pass the branch to merge into")


def _circuit(project: Project, rev: str) -> Circuit:
    return project.circuit_at(rev) or Circuit(board=project.load_circuit().board)


def _run_checks(project: Project, pr: PR, head: str) -> tuple[PR, CheckReport]:
    """Check the branch head (committed circuit only: no scan proposals or serial data)."""
    report = run_checks(_circuit(project, head), pending=[], commit=head)
    save_report(project, report)
    pr = pr.model_copy(update={"last_checked_commit": head, "checks": PRChecks(commit=head, overall=report.status)})
    return pr, report


def _is_ancestor(project: Project, older: str, newer: str) -> bool:
    try:
        project.repo.run("merge-base", "--is-ancestor", older, newer)
        return True
    except GitError:
        return False


def _merge_status(project: Project, pr: PR, head: str, report: CheckReport | None) -> MergeStatus:
    """Only a failing check blocks a merge. Warnings and unchecked items are listed, not blocking."""
    notes = {}
    if report is not None:
        notes = dict(
            warnings=[r.message for r in report.results if r.status == "needs_confirmation"],
            not_checked=[r.message for r in report.results if r.status == "not_supported"],
        )
    if pr.status != "open":
        return MergeStatus(allowed=False, reason=f"this PR is {pr.status}", **notes)
    if report is None or report.commit != head:
        return MergeStatus(allowed=False, reason="checks haven't run on the branch's latest commit", **notes)
    failing = [r.message for r in report.results if r.status == "fail"]
    if failing:
        return MergeStatus(allowed=False, reason="checks failed: " + " ".join(failing), **notes)
    if not _is_ancestor(project, branch_head(project, pr.into_branch), head):
        return MergeStatus(
            allowed=False, reason=f"{pr.into_branch} changed since this branch was created. Update your branch first.",
            **notes,
        )  # fmt: skip
    n = len(notes["warnings"])
    reason = "no checks failed and the branch can be fast-forwarded"
    if n:
        reason += f"; {n} warning{'s' * (n != 1)} to confirm first"
    return MergeStatus(allowed=True, reason=reason, **notes)


# ── Operations ────────────────────────────────────────────────────────────────


def create(
    project: Project, from_branch: str, into_branch: str | None = None, title: str = "", description: str = ""
) -> PR:
    """Save a PR pointer and check the branch head."""
    into_branch = into_branch or default_into(project)
    if not title.strip():
        raise ProjectError("a PR needs a title")
    if from_branch == into_branch:
        raise ProjectError(f"can't open a PR from {from_branch} into itself")
    head, base = branch_head(project, from_branch), branch_head(project, into_branch)
    if head == base or _is_ancestor(project, head, base):
        raise ProjectError(f"{from_branch} has no commits that aren't already in {into_branch}")
    store = _store(project)
    if any(p.status == "open" and p.from_branch == from_branch and p.into_branch == into_branch for p in store.all()):
        raise ProjectError(f"there's already an open PR from {from_branch} into {into_branch}")
    project.ensure_state_ignored()
    pr = PR(id=store.next_id(), title=title.strip(), description=description, from_branch=from_branch, into_branch=into_branch)
    pr, _ = _run_checks(project, pr, head)
    return store.save(pr)


def list_prs(project: Project) -> list[PR]:
    return _store(project).all()


def get(project: Project, pr_id: int) -> PRDetail:
    """Build the full PR from git at read time, rerunning checks if the branch moved."""
    store = _store(project)
    pr = store.get(pr_id)
    if pr.merged is not None:
        before, after = _circuit(project, pr.merged.into_before), _circuit(project, pr.merged.commit)
        report = load_report(project, pr.merged.commit)
        head = pr.merged.commit
    else:
        head = branch_head(project, pr.from_branch)
        before, after = _circuit(project, pr.into_branch), _circuit(project, head)
        changed = False
        if pr.status == "open" and pr.last_checked_commit != head:
            pr, report = _run_checks(project, pr, head)
            changed = True
        else:
            report = load_report(project, pr.last_checked_commit) if pr.last_checked_commit else None
        if pr.tested.done and pr.tested.commit != head:
            pr = pr.model_copy(update={"tested": pr.tested.model_copy(update={"done": False})})
            changed = True
        if changed:
            pr = store.save(pr)
    d = diff(before, after)
    return PRDetail(
        pr=pr, before=before, after=after, diff=d, summary=describe(d), checks=report,
        merge=_merge_status(project, pr, head, report),
    )  # fmt: skip


def mark_tested(project: Project, pr_id: int, note: str = "") -> PR:
    store = _store(project)
    pr = store.get(pr_id)
    if pr.status != "open":
        raise ProjectError(f"PR #{pr_id} is {pr.status}")
    head = branch_head(project, pr.from_branch)
    return store.save(pr.model_copy(update={"tested": PRTested(done=True, note=note or None, commit=head, at=_now())}))


def merge(project: Project, pr_id: int) -> PRDetail:
    """Fast-forward into_branch to the branch head. Only when open, checks pass on the head, and ff is possible."""
    detail = get(project, pr_id)  # reruns checks if the branch moved
    if not detail.merge.allowed:
        raise ProjectError(f"can't merge PR #{pr_id}: {detail.merge.reason}")
    pr = detail.pr
    require_clean(project, "merge")
    head, into_before = branch_head(project, pr.from_branch), branch_head(project, pr.into_branch)
    board_matched_branch = project.board_state.load().matches_commit == head
    old = project.load_circuit()
    project.repo.run("checkout", "--quiet", pr.into_branch)
    project.repo.run("merge", "--ff-only", "--quiet", pr.from_branch)
    shift_scan_reference(project, old, project.load_circuit())
    if board_matched_branch:
        project.board_state.set(branch_head(project, pr.into_branch), project.load_circuit())
    _store(project).save(pr.model_copy(update={"status": "merged", "merged": PRMerge(commit=head, into_before=into_before, at=_now())}))
    return get(project, pr_id)


class CloseResult(BaseModel):
    pr: PR
    guide: list[RewireStep] = Field(description="How to rewire the board back to into_branch.")


def close(project: Project, pr_id: int) -> CloseResult:
    store = _store(project)
    pr = store.get(pr_id)
    if pr.status != "open":
        raise ProjectError(f"PR #{pr_id} is already {pr.status}")
    pr = store.save(pr.model_copy(update={"status": "closed"}))
    board = project.board_state.load().matches_commit or branch_head(project, pr.from_branch)
    guide = rewire_guide(_circuit(project, board), _circuit(project, pr.into_branch))
    return CloseResult(pr=pr, guide=guide)
