"""Circuit checks. Run locally: `benchlog check`, before a commit, and on PRs. Owner: Person 2.

Each check is a small function that takes the circuit (and whatever extra data it needs) and
returns `CheckResult`s. `run_checks` runs them all. Missing data gives "not_supported", never a
silent pass.
"""

from benchlog.core.checks.report import CheckReport, CheckResult, Status
from benchlog.core.checks.runner import run_checks

__all__ = ["CheckReport", "CheckResult", "Status", "run_checks"]
