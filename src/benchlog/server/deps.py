"""Dependencies shared by the route modules (importing them from app.py would be circular)."""

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from benchlog.core.project import Project


def get_project() -> Project:
    start = os.environ.get("BENCHLOG_PROJECT")
    return Project.find(Path(start) if start else None)


ProjectDep = Annotated[Project, Depends(get_project)]
