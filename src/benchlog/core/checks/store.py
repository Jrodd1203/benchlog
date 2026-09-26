"""Saved check reports, one per commit: .benchlog/checks/<sha>.json (gitignored)."""

from benchlog.core.checks.report import CheckReport
from benchlog.core.checks.runner import run_checks
from benchlog.core.project import STATE_DIR, Project
from benchlog.core.reconcile import Reconciliation

CHECKS_DIR = STATE_DIR / "checks"


def save_report(project: Project, report: CheckReport) -> None:
    if report.commit is None:
        raise ValueError("only reports for a commit are saved")
    path = project.repo.root / CHECKS_DIR / f"{report.commit}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2) + "\n")


def load_report(project: Project, sha: str) -> CheckReport | None:
    path = project.repo.root / CHECKS_DIR / f"{sha}.json"
    return CheckReport.model_validate_json(path.read_text()) if path.exists() else None


def check_project(project: Project, reconciliation: Reconciliation | None = None) -> CheckReport:
    """Check the working circuit. If it matches HEAD, the report is for HEAD and is saved."""
    circuit = project.load_circuit()
    head = project.repo.head()
    commit = head if head and project.circuit_at("HEAD") == circuit else None
    report = run_checks(circuit, reconciliation, pending=project.pending_observations(), commit=commit)
    if commit:
        save_report(project, report)
    return report
